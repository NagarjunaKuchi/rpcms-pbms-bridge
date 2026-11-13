import importlib.util
import json
import logging
import pathlib

import requests

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# mapping.py is Odoo-free so it can be tested standalone; load it by path to keep one source.
_spec = importlib.util.spec_from_file_location("rpcms_mapping", pathlib.Path(__file__).parent.parent / "mapping.py")
mapping = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mapping)

_logger = logging.getLogger(__name__)


class G2PPaymentManagerG2PConnectRPCMS(models.Model):
    _inherit = "g2p.program.payment.manager.g2p.connect"

    rpcms_progres_mode = fields.Boolean(
        "RPCMS ProGres mode",
        help="Send the ProGres ID as beneficiary_id and a unique code per cycle as cycle_code_mnemonic.",
    )

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
