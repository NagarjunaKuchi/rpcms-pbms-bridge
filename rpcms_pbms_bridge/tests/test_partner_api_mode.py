"""Partner API mode against a FAKE Bridge.

The fake applies the rules read from the Bridge source (envelope/disbursement validation, detached-JWS check over
sorted-key compact JSON), plus a real RS256 signature round trip. It is not the Bridge: it proves the addon behaves
correctly against those rules, and tests/test_bridge_api_contract.py checks the request shapes against the Bridge's
own schemas. DUPLICATE_DISBURSEMENT is a code that exists in the Bridge's enum; that the real Bridge answers a
resent disbursement_id with it is NOT confirmed.
"""
import base64
import json
from datetime import timedelta
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.rpcms_pbms_bridge.models.payment_manager import bridge_api

FREQ = ("Daily", "Weekly", "Fortnightly", "Monthly", "BiMonthly", "Quarterly", "SemiAnnually", "Annually")


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


class Resp:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status
        self.content = json.dumps(body).encode()

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class Signer:
    """Stands in for the encryption provider; signs like the Keymanager does with include_payload=False."""

    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def jwt_sign(self, payload, include_payload=True, **kw):
        data = payload.encode() if isinstance(payload, str) else payload
        head = b64(json.dumps({"alg": "RS256"}).encode())
        sig = self.key.sign(f"{head}.{b64(data)}".encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{head}..{b64(sig)}"


class FakeBridge:
    def __init__(self, signer):
        self.public = signer.key.public_key()
        self.calls, self.envelopes, self.disbursements = [], {}, {}
        self.fail_with = None  # an exception to raise, or ("whole", code) for a whole-request rejection
        self.reject_ids = {}  # disbursement_id -> [codes]
        self.status_recons = []

    def signature_valid(self, body, token):
        part1, _, part3 = token.split(".")
        # What the Bridge does: parse the body, sort keys, compact, UTF-8, then check the detached signature.
        expected = b64(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())
        pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
        try:
            self.public.verify(base64.urlsafe_b64decode(pad(part3)), f"{part1}.{expected}".encode(),
                               padding.PKCS1v15(), hashes.SHA256())
            return True
        except Exception:
            return False

    def reply(self, body, status="SUCCESS", code=None, payload=None):
        return Resp({"response_header": {"request_id": body["request_header"]["request_id"], "response_status": status,
                                         "response_error_code": code, "response_error_message": code,
                                         "response_timestamp": "2026-11-13T09:00:00"},
                     "response_body": {"response_payload": payload if payload is not None else []}})

    def post(self, url, json=None, timeout=None, headers=None):
        wire = __import__("json").loads(__import__("json").dumps(json))  # what the Bridge parses off the wire
        self.calls.append((url, wire, headers, timeout))
        if self.fail_with and not isinstance(self.fail_with, tuple):
            raise self.fail_with
        if not self.signature_valid(wire, headers["Signature"]):
            return self.reply(wire, "ERROR", "rjct_jwt_invalid")
        if self.fail_with:
            return self.reply(wire, "ERROR", self.fail_with[1])
        payload = wire["request_body"]["request_payload"]
        if url.endswith("/create_disbursement_envelopes"):
            p = payload[0]
            for field, code in (("benefit_program_id", "INVALID_PROGRAM_ID"), ("benefit_code_id", "INVALID_BENEFIT_CODE_ID"),
                                ("cycle_code_mnemonic", "INVALID_CYCLE_CODE_MNEMONIC"),
                                ("target_registry", "INVALID_TARGET_REGISTRY")):
                if not p.get(field):
                    return self.reply(wire, "ERROR", code)
            if p.get("disbursement_frequency") not in FREQ:
                return self.reply(wire, "ERROR", "INVALID_DISBURSEMENT_FREQUENCY")
            if (p.get("number_of_beneficiaries") or 0) < 1 or (p.get("number_of_disbursements") or 0) < 1:
                return self.reply(wire, "ERROR", "INVALID_NO_OF_DISBURSEMENTS")
            if p.get("total_disbursement_quantity") is None or p["total_disbursement_quantity"] < 0:
                return self.reply(wire, "ERROR", "INVALID_DISBURSEMENT_QUANTITY")
            eid = f"ENV-{len(self.envelopes) + 1}"
            self.envelopes[eid] = p
            return self.reply(wire, payload=[{**p, "id": eid}])
        if url.endswith("/create_disbursements"):
            out, bad = [], False
            for d in payload:
                codes = list(self.reject_ids.get(d["disbursement_id"], []))
                if d["disbursement_envelope_id"] not in self.envelopes:
                    codes.append("DISBURSEMENT_ENVELOPE_NOT_FOUND")
                if d["disbursement_id"] in self.disbursements:
                    codes.append("DUPLICATE_DISBURSEMENT")
                if not d.get("beneficiary_id"):
                    codes.append("INVALID_BENEFICIARY_ID")
                if d.get("disbursement_cycle_id") is None:
                    codes.append("INVALID_CYCLE")
                bad = bad or bool(codes)
                out.append({"disbursement_id": d["disbursement_id"], "response_error_codes": codes})
            if not bad:
                for d in payload:
                    self.disbursements[d["disbursement_id"]] = d
            return self.reply(wire, "ERROR" if bad else "SUCCESS", "INVALID_DISBURSEMENT" if bad else None, out)
        if url.endswith("/get_disbursement_status"):
            return self.reply(wire, payload=[{"reconciliation_id": "R1", "disbursement_recon_records": {
                "disbursement_recon_payloads": [r for r in self.status_recons if r["disbursement_id"] in payload]}}])
        return Resp({}, 404)


@tagged("post_install", "-at_install")
class TestPartnerApiMode(TransactionCase):
    def setUp(self):
        super().setUp()
        self.program = self.env["g2p.program"].create({"name": "Bidibidi Cash"})
        self.manager = self.env["g2p.program.payment.manager.g2p.connect"].create({
            "name": "RPCMS manager", "program_id": self.program.id, "create_batch": True, "locale": "en_US",
            "payment_endpoint_url": "http://bridge/create_disbursements",
            "status_endpoint_url": "http://bridge/get_disbursement_status",
            "envelope_creation_url": "http://bridge/create_disbursement_envelopes",
            "envelope_status_url": "http://bridge/get_disbursement_envelope_status",
            "payee_id_type": "bank_acc_no",
            "rpcms_bridge_api": "partner_api", "sender_id": "rpcms-pbms",
            "rpcms_sender_app_url": "https://pbms.example.org", "rpcms_benefit_program_id": 11,
            "rpcms_benefit_code_id": 22, "rpcms_benefit_code_mnemonic": "CASH-UGX",
            "rpcms_target_registry": "PROGRES", "rpcms_disbursement_frequency": "Monthly"})
        self.signer = Signer()
        self.bridge = FakeBridge(self.signer)
        patcher = patch.object(type(self.manager), "get_encryption_provider", lambda s: self.signer)
        patcher.start()
        self.addCleanup(patcher.stop)
        post = patch("requests.post", self.bridge.post)
        post.start()
        self.addCleanup(post.stop)
        self.cycle = self.env["g2p.cycle"].create({
            "name": "Nov 2026", "program_id": self.program.id, "start_date": fields.Datetime.now(),
            "end_date": fields.Datetime.now() + timedelta(days=7)})
        self.payments = self.env["g2p.payment"]
        for i in (1, 2):
            partner = self.env["res.partner"].create({"name": f"Person {i}", "bank_ids": [(0, 0, {"acc_number": f"ACC{i}"})]})
            ent = self.env["g2p.entitlement"].create({
                "program_id": self.program.id, "partner_id": partner.id, "cycle_id": self.cycle.id,
                "state": "draft", "initial_amount": 50000.0})
            self.payments |= self.env["g2p.payment"].create({
                "name": f"Pay {i}", "amount_issued": 50000.0, "cycle_id": self.cycle.id, "state": "issued",
                "entitlement_id": ent.id, "currency_id": self.env.ref("base.USD").id})
        self.batch = self.env["g2p.payment.batch"].create({
            "program_id": self.program.id, "cycle_id": self.cycle.id, "payment_ids": [(6, 0, self.payments.ids)]})

    def make_envelope(self):
        self.assertTrue(self.manager._create_envelope_g2p_bridge(self.cycle))

    # --- configuration
    def test_missing_configuration_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.manager.rpcms_benefit_code_id = 0
        with self.assertRaises(ValidationError):
            self.manager.rpcms_disbursement_frequency = False

    def test_legacy_is_the_default_and_needs_no_bridge_fields(self):
        other = self.env["g2p.program.payment.manager.g2p.connect"].create({
            "name": "plain", "program_id": self.program.id, "payee_id_type": "bank_acc_no", "locale": "en_US",
            "payment_endpoint_url": "http://x/p", "status_endpoint_url": "http://x/s",
            "envelope_creation_url": "http://x/e", "envelope_status_url": "http://x/es"})
        self.assertEqual(other.rpcms_bridge_api, "legacy")

    # --- envelope
    def test_envelope_is_signed_valid_and_carries_per_cycle_code(self):
        self.make_envelope()
        url, wire, headers, _ = self.bridge.calls[0]
        self.assertTrue(url.endswith("/create_disbursement_envelopes"))
        self.assertEqual(set(wire), {"request_header", "request_body"})
        p = wire["request_body"]["request_payload"][0]
        self.assertEqual(p["cycle_code_mnemonic"], f"NOV-2026-C{self.cycle.id}")  # never upstream's hardcoded "SEP"
        self.assertEqual((p["benefit_program_id"], p["benefit_code_id"], p["benefit_type"], p["disbursement_frequency"]),
                         (11, 22, "CASH_DIGITAL", "Monthly"))
        self.assertEqual(p["number_of_disbursements"], 2)
        self.assertEqual(wire["request_header"]["sender_app_mnemonic"], "rpcms-pbms")
        self.assertEqual(self.cycle.disbursement_envelope_id, "ENV-1")  # read from response_payload[0].id

    def test_envelope_refused_by_bridge_leaves_cycle_without_envelope(self):
        self.bridge.fail_with = ("whole", "INVALID_PROGRAM_ID")
        self.assertFalse(self.manager._create_envelope_g2p_bridge(self.cycle))
        self.assertFalse(self.cycle.disbursement_envelope_id)

    def test_envelope_network_error_is_reported_not_raised(self):
        self.bridge.fail_with = ConnectionError("down")
        self.assertFalse(self.manager._create_envelope_g2p_bridge(self.cycle))

    def test_a_tampered_request_fails_the_bridge_signature_check(self):
        req = {"request_header": self.manager._rpcms_header(), "request_body": {"request_payload": [{"x": 1}]}}
        token = self.manager.create_jwt_token(bridge_api.canonical_json(req))
        self.assertTrue(self.bridge.signature_valid(req, token))
        req["request_body"]["request_payload"][0]["x"] = 2
        self.assertFalse(self.bridge.signature_valid(req, token))

    # --- sending
    def test_send_marks_each_payment_sent_with_a_deterministic_disbursement_id(self):
        self.make_envelope()
        self.manager._send_payments(self.batch)
        url, wire, _, _ = self.bridge.calls[-1]
        self.assertTrue(url.endswith("/create_disbursements"))
        items = wire["request_body"]["request_payload"]
        self.assertEqual({i["disbursement_id"] for i in items}, {f"rpcms-pbms-P{p.id}" for p in self.payments})
        self.assertEqual({i["disbursement_envelope_id"] for i in items}, {"ENV-1"})
        self.assertEqual(wire["request_body"]["disbursement_batch_control_id"], f"rpcms-pbms-B{self.batch.id}")
        self.assertEqual(set(self.payments.mapped("dispatch_status")), {"sent"})
        self.assertEqual(self.payments.mapped("disbursement_id"), [f"rpcms-pbms-P{p.id}" for p in self.payments])
        self.assertTrue(self.batch.batch_has_completed)

    def test_partial_rejection_marks_only_accepted_payments_and_reports_the_rest(self):
        self.make_envelope()
        bad = self.payments[1]
        self.bridge.reject_ids[f"rpcms-pbms-P{bad.id}"] = ["INVALID_BENEFICIARY_ID"]
        before = len(self.manager.message_ids)
        self.manager._send_payments(self.batch)
        # the fake accepts none when any item is bad (it records nothing), but the addon trusts per-item codes
        self.assertNotEqual(self.payments[1].dispatch_status, "sent")
        self.assertFalse(self.batch.batch_has_completed)
        self.assertGreater(len(self.manager.message_ids), before)
        self.assertIn("INVALID_BENEFICIARY_ID", self.manager.message_ids[0].body)

    def test_whole_request_rejection_allows_a_later_resend(self):
        self.make_envelope()
        self.bridge.fail_with = ("whole", "DISBURSEMENT_ENVELOPE_NOT_FOUND")
        self.manager._send_payments(self.batch)
        self.assertFalse(self.batch.batch_has_started)  # nothing was processed: safe to try again
        self.bridge.fail_with = None
        self.manager._send_payments(self.batch)
        self.assertTrue(self.batch.batch_has_completed)

    def test_unknown_outcome_is_not_resent_automatically(self):
        self.make_envelope()
        self.bridge.fail_with = TimeoutError("read timed out")
        self.manager._send_payments(self.batch)
        self.assertTrue(self.batch.batch_has_started)  # it may have been accepted: a blind resend could pay twice
        n = len(self.bridge.calls)
        self.bridge.fail_with = None
        self.manager._send_payments(self.batch)
        self.assertEqual(len(self.bridge.calls), n)  # skipped, not re-sent
        self.assertNotIn("sent", self.payments.mapped("dispatch_status"))

    def test_resending_an_accepted_disbursement_is_refused_by_the_bridge_not_paid_twice(self):
        self.make_envelope()
        self.manager._send_payments(self.batch)
        self.batch.batch_has_started = False  # someone forces a resend
        self.manager._send_payments(self.batch)
        self.assertEqual(len(self.bridge.disbursements), 2)  # still two disbursements at the Bridge

    # --- status
    def status_calls(self):
        return [c for c in self.bridge.calls if c[0].endswith("/get_disbursement_status")]

    def test_status_check_asks_in_chunks(self):
        self.make_envelope()
        self.manager._send_payments(self.batch)
        with patch.object(type(self.manager), "STATUS_CHUNK", 1):
            self.manager.payments_status_check(self.manager.id)
        ids = [c[1]["request_body"]["request_payload"] for c in self.status_calls()]
        self.assertEqual(sorted(ids), sorted([[p.disbursement_id] for p in self.payments]))

    def test_status_check_polls_only_payments_the_bridge_accepted(self):
        self.make_envelope()
        self.manager._send_payments(self.batch)
        self.payments[1].dispatch_status = "pending"
        self.manager.payments_status_check(self.manager.id)
        (call,) = self.status_calls()
        self.assertEqual(call[1]["request_body"]["request_payload"], [self.payments[0].disbursement_id])

    def test_status_check_applies_reconciliation(self):
        self.make_envelope()
        self.manager._send_payments(self.batch)
        did = self.payments[0].disbursement_id
        self.bridge.status_recons = [{"disbursement_id": did, "remittance_reference_number": "RR-1",
                                      "remittance_statement_id": "S-1", "remittance_entry_sequence": "3",
                                      "remittance_entry_date": None, "reversal_statement_id": None,
                                      "reversal_entry_sequence": None, "reversal_entry_date": None,
                                      "reversal_reason": None}]
        self.manager.payments_status_check(self.manager.id)
        self.assertEqual(self.payments[0].remittance_statement_id, "S-1")
        self.assertFalse(self.payments[1].remittance_statement_id)
        url, wire, _, _ = self.bridge.calls[-1]
        self.assertTrue(url.endswith("/get_disbursement_status"))
        self.assertEqual(set(wire["request_body"]["request_payload"]), set(self.payments.mapped("disbursement_id")))
