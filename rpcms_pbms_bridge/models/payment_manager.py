import importlib.util
import json
import logging
import pathlib
import uuid

import requests

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# mapping.py is Odoo-free so it can be tested standalone; load it by path to keep one source.
_spec = importlib.util.spec_from_file_location("rpcms_mapping", pathlib.Path(__file__).parent.parent / "mapping.py")
mapping = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mapping)

_spec2 = importlib.util.spec_from_file_location("rpcms_bridge_api", pathlib.Path(__file__).parent.parent / "bridge_api.py")
bridge_api = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(bridge_api)

_logger = logging.getLogger(__name__)


class G2PPaymentManagerG2PConnectRPCMS(models.Model):
    _inherit = "g2p.program.payment.manager.g2p.connect"

    rpcms_progres_mode = fields.Boolean(
        "RPCMS ProGres mode",
        help="Send the ProGres ID as beneficiary_id and a unique code per cycle as cycle_code_mnemonic.",
    )

    rpcms_bridge_api = fields.Selection(
        [("legacy", "Legacy G2P Connect shape (as upstream)"),
         ("partner_api", "Bridge Partner API (request_header / request_body)")],
        "RPCMS Bridge API", default="legacy", required=True,
        help="Which request shape to send to the G2P Bridge. 'legacy' leaves upstream behaviour untouched. "
             "'partner_api' sends the shape the Bridge's own schemas define (checked against one Bridge develop "
             "clone only): set the URLs to the Bridge's /create_disbursement_envelopes, /create_disbursements and "
             "/get_disbursement_status endpoints.")
    rpcms_benefit_program_id = fields.Integer("Bridge benefit program ID")
    rpcms_benefit_code_id = fields.Integer("Bridge benefit code ID")
    rpcms_benefit_code_mnemonic = fields.Char("Bridge benefit code mnemonic")
    rpcms_target_registry = fields.Char("Bridge target registry")
    rpcms_disbursement_frequency = fields.Selection([(f, f) for f in bridge_api.FREQUENCIES], "Bridge disbursement frequency")
    rpcms_sender_app_url = fields.Char("Sender app URL")

    @api.constrains("rpcms_bridge_api", "sender_id", "rpcms_sender_app_url", "rpcms_benefit_program_id",
                    "rpcms_benefit_code_id", "rpcms_benefit_code_mnemonic", "rpcms_target_registry",
                    "rpcms_disbursement_frequency")
    def _check_rpcms_partner_api(self):
        for rec in self.filtered(lambda r: r.rpcms_bridge_api == "partner_api"):
            missing = [label for label, value in (
                ("Sender ID", rec.sender_id), ("Sender app URL", rec.rpcms_sender_app_url),
                ("Bridge benefit program ID", rec.rpcms_benefit_program_id),
                ("Bridge benefit code ID", rec.rpcms_benefit_code_id),
                ("Bridge benefit code mnemonic", rec.rpcms_benefit_code_mnemonic),
                ("Bridge target registry", rec.rpcms_target_registry),
                ("Bridge disbursement frequency", rec.rpcms_disbursement_frequency)) if not value]
            if missing:
                raise ValidationError(_("Bridge Partner API mode needs: ") + ", ".join(missing))

    @api.constrains("rpcms_progres_mode", "payee_id_type", "reg_id_type_for_payee_id", "payee_prefix", "payee_suffix")
    def _check_rpcms_progres_mode(self):
        for rec in self.filtered("rpcms_progres_mode"):
            problems = mapping.manager_config_problems(
                payee_id_type=rec.payee_id_type,
                id_type_name=rec.reg_id_type_for_payee_id.name,
                prefix=rec.payee_prefix,
                suffix=rec.payee_suffix,
            )
            if problems:
                raise ValidationError(_("RPCMS ProGres mode: ") + " ".join(problems))

    def _create_envelope_g2p_bridge(self, cycle):
        """Same as g2p_payment_g2p_connect's method (copied from openg2p-program 17.0-develop at
        8a44dd5) except the message is built by mapping.build_envelope_message, which gives each
        cycle its own cycle_code_mnemonic instead of the hardcoded "SEP". Re-check this copy
        whenever the upstream addon changes. Without ProGres mode, behaviour is unchanged."""
        if self.rpcms_bridge_api == "partner_api":
            return self._rpcms_create_envelope_partner_api(cycle)
        if not self.rpcms_progres_mode:
            return super()._create_envelope_g2p_bridge(cycle)
        _logger.info("Creating envelope for g2p_bridge (RPCMS ProGres mode)")
        n = len(cycle.entitlement_ids)
        envelope_request_data = {
            "signature": "string",
            "header": {
                "version": "1.0.0",
                "message_id": "string",
                "message_ts": "string",
                "action": "create_disbursement_envelope",
                "sender_id": self.sender_id,
                "sender_uri": "",
                "receiver_id": "",
                "total_count": 0,
                "is_msg_encrypted": False,
                "meta": "string",
            },
            "message": mapping.build_envelope_message(
                program_mnemonic=f"{cycle.program_id.name} #{self.id}",
                cycle_name=cycle.name,
                cycle_id=cycle.id,
                number_of_payments=n,
                total_amount=cycle.total_amount,
                currency_code=cycle.program_id.company_id.currency_id.name,
                schedule_date=cycle.start_date,
            ),
        }
        try:
            token = self.create_jwt_token(json.dumps(envelope_request_data, separators=(",", ":")))
            headers = {"Accept": "application/json", "Content-Type": "application/json", "Signature": token}
            response = requests.post(
                self.envelope_creation_url, json=envelope_request_data, timeout=10, headers=headers
            )
            response.raise_for_status()
            cycle.write({"disbursement_envelope_id": response.json().get("message").get("disbursement_envelope_id")})
            _logger.info("G2P Connect Disbursement Envelope response: %s", response.content)
            return True
        except Exception as e:
            _logger.error("G2P Connect Disbursement Envelope Failed with reason: %s", str(e))
        return False

    # ------------------------------------------------------------------ Bridge Partner API mode

    def _rpcms_header(self):
        return bridge_api.request_header(str(uuid.uuid4()), self.sender_id, self.rpcms_sender_app_url)

    def _rpcms_post(self, url, body, timeout):
        """POST a Bridge request signed over the canonical JSON the Bridge rebuilds (sorted keys, compact).
        The Bridge only checks the signature when signature_validation_enabled is true on its side, and looks the
        key up under bridge_api.partner_key_reference(sender_id): that partner must exist there."""
        token = self.create_jwt_token(bridge_api.canonical_json(body))
        headers = {"Accept": "application/json", "Content-Type": "application/json", "Signature": token}
        return requests.post(url, json=body, timeout=timeout, headers=headers)

    def _rpcms_create_envelope_partner_api(self, cycle):
        """Create the cycle's envelope with the Bridge Partner API. The cycle code is always the per-cycle code
        (never upstream's hardcoded "SEP")."""
        _logger.info("Creating envelope for g2p_bridge (Partner API shape)")
        body = bridge_api.envelope_request(
            self._rpcms_header(),
            benefit_program_id=self.rpcms_benefit_program_id,
            benefit_program_mnemonic=f"{cycle.program_id.name} #{self.id}",
            target_registry=self.rpcms_target_registry,
            benefit_code_id=self.rpcms_benefit_code_id,
            benefit_code_mnemonic=self.rpcms_benefit_code_mnemonic,
            disbursement_cycle_id=cycle.id,
            disbursement_frequency=self.rpcms_disbursement_frequency,
            cycle_code_mnemonic=mapping.cycle_code(cycle.name, cycle.id),
            number_of_payments=len(cycle.entitlement_ids),
            total_quantity=cycle.total_amount,
            measurement_unit=cycle.program_id.company_id.currency_id.name,
            schedule_date=cycle.start_date,
        )
        try:
            response = self._rpcms_post(self.envelope_creation_url, body, 10)
            response.raise_for_status()
            envelope_id = bridge_api.parse_envelope_response(response.json())
        except Exception as e:  # noqa: BLE001 - same policy as upstream: log, return False, no envelope id
            _logger.error("G2P Bridge envelope creation failed: %s", e)
            return False
        cycle.write({"disbursement_envelope_id": envelope_id})
        return True

    def _send_payments(self, batches):
        """Partner API mode only; otherwise upstream. The loop mirrors upstream's (copied from openg2p-program
        17.0-develop at 8a44dd5; re-check it when upstream changes) but builds Bridge Partner API requests and
        reads per-disbursement error codes. A batch stays marked as started unless the Bridge rejected the whole
        request, so an unclear failure (timeout) is never blindly re-sent."""
        if self.rpcms_bridge_api != "partner_api":
            return super()._send_payments(batches)
        if batches:
            batches = batches.filtered_domain(self._safe_eval(self.send_payments_domain))
        self._rpcms_ensure_status_cron()
        for batch in batches:
            if batch.batch_has_started:
                continue
            batch.batch_has_started = True
            items, by_id = [], {}
            for payment in batch.payment_ids:
                payee_fa = self._get_payee_fa(payment)
                if not payee_fa:
                    payment.status = "failed"
                    continue
                did = bridge_api.disbursement_id_for(self.sender_id, payment.id)
                by_id[did] = payment
                items.append(bridge_api.disbursement_item(
                    disbursement_id=did, envelope_id=payment.disbursement_envelope_id, beneficiary_id=payee_fa,
                    beneficiary_name=payment.partner_id.name, quantity=payment.amount_issued,
                    narrative=f"Payment for {batch.cycle_id.name} under {self.program_id.name}",
                    cycle_id=batch.cycle_id.id))
            if not items:
                continue
            body = bridge_api.disbursements_request(self._rpcms_header(), items, f"{self.sender_id}-B{batch.id}")
            try:
                response = self._rpcms_post(self.payment_endpoint_url, body, self.api_timeout)
                response.raise_for_status()
                results = bridge_api.parse_disbursement_response(response.json())
            except bridge_api.BridgeError as e:
                batch.batch_has_started = False  # rejected as a whole: nothing was processed, safe to send again
                self._rpcms_report(f"G2P Bridge rejected batch {batch.name}: {e} ({e.code})")
                continue
            except Exception as e:  # noqa: BLE001 - outcome unknown (timeout, 5xx): do not re-send automatically
                self._rpcms_report(f"G2P Bridge batch {batch.name}: outcome unknown, not retried automatically: {e}")
                continue
            bad = {}
            for did, payment in by_id.items():
                codes = results.get(did)
                if codes is None:
                    bad[did] = ["MISSING_FROM_RESPONSE"]
                elif codes:
                    bad[did] = codes
                else:
                    payment.write({"disbursement_id": did, "dispatch_status": "sent", "amount_paid": payment.amount_issued})
            if bad:
                self._rpcms_report(f"G2P Bridge batch {batch.name}: not accepted: " +
                                   "; ".join(f"{d}: {', '.join(c)}" for d, c in bad.items()))
            else:
                batch.batch_has_completed = True

    def _rpcms_report(self, text):
        _logger.error(text)
        self.message_post(body=text, subject=_("G2P Bridge Payment Disbursement"))

    def _rpcms_ensure_status_cron(self):
        """Same cron as upstream's _send_payments creates."""
        if self.status_check_cron_id:
            return
        self.status_check_cron_id = (
            self.env["ir.cron"].sudo().create({
                "name": "G2P Connect Payment Manager Status Cron " + self.name + " #" + str(self.id),
                "active": True,
                "interval_number": self.status_cron_job_interval_minutes,
                "interval_type": "minutes",
                "model_id": self.env["ir.model"].search([("model", "=", self._name)]).id,
                "state": "code",
                "code": "model.payments_status_check(" + str(self.id) + ")",
                "doall": False,
                "numbercall": -1,
            })
        )

    STATUS_CHUNK = 200

    def payments_status_check(self, id_):
        """Partner API mode only; otherwise upstream. Polls payments of this program that were accepted by the Bridge
        and still lack a remittance or a reversal statement (upstream's rule: so a later reversal is still seen, and
        every sent payment keeps being polled until both are set). Unlike upstream it does not filter on batch
        statistics time and asks the Bridge in chunks."""
        manager = self.browse(id_)
        if manager.rpcms_bridge_api != "partner_api":
            return super().payments_status_check(id_)
        payments = self.env["g2p.payment"].search([
            ("program_id", "=", manager.program_id.id), ("dispatch_status", "=", "sent"),
            ("disbursement_id", "!=", False),
            "|", ("remittance_statement_id", "=", False), ("reversal_statement_id", "=", False)])
        for start in range(0, len(payments), self.STATUS_CHUNK):
            chunk = payments[start:start + self.STATUS_CHUNK]
            body = bridge_api.status_request(manager._rpcms_header(), chunk.mapped("disbursement_id"))
            try:
                response = manager._rpcms_post(manager.status_endpoint_url, body, manager.api_timeout)
                response.raise_for_status()
                recons = bridge_api.parse_status_response(response.json())
            except Exception as e:  # noqa: BLE001
                manager._rpcms_report(f"G2P Bridge status check failed: {e}")
                continue
            for recon in recons:
                payment = chunk.filtered(lambda p, d=recon.get("disbursement_id"): p.disbursement_id == d)
                if payment:
                    payment.write({k: recon.get(k) for k in (
                        "remittance_reference_number", "remittance_statement_id", "remittance_entry_sequence",
                        "remittance_entry_date", "reversal_statement_id", "reversal_entry_sequence",
                        "reversal_entry_date", "reversal_reason")})
