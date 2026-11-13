"""Check the requests the PBMS payment manager sends against the G2P Bridge's own request schemas.

Usage (needs the Bridge models package, e.g. `pip install -e <g2p-bridge>/core/models "sqlalchemy[asyncio]"`):
    python scripts/check_bridge_contract.py

The payloads are built exactly as g2p_payment_g2p_connect builds them (the envelope `message` comes from this
addon's own mapping code). Exit code 0 = both accepted by the Bridge schemas, 1 = at least one rejected,
2 = Bridge models not installed. It checks request SHAPE only: it does not sign, send, or test the Bridge's
behaviour, and says nothing about other Bridge versions than the one installed.
"""
import pathlib
import sys

import importlib.util  # noqa: E402

# Load mapping.py by path: importing the addon package would import Odoo.
_spec = importlib.util.spec_from_file_location(
    "rpcms_mapping", pathlib.Path(__file__).resolve().parents[1] / "rpcms_pbms_bridge" / "mapping.py")
mapping = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mapping)

try:
    from openg2p_g2p_bridge_models.schemas.disbursement import DisbursementPayload, DisbursementRequest
    from openg2p_g2p_bridge_models.schemas.disbursement_envelope import (DisbursementEnvelopePayload,
                                                                         DisbursementEnvelopeRequest)
except ImportError as e:
    print(f"Bridge models not installed: {e}")
    sys.exit(2)

HEADER = {"version": "1.0.0", "message_id": "string", "message_ts": "string", "action": "create_disbursement_envelope",
          "sender_id": "sender", "sender_uri": "", "receiver_id": "", "total_count": 0, "is_msg_encrypted": False,
          "meta": "string"}
envelope = {"signature": "string", "header": HEADER, "message": mapping.build_envelope_message(
    program_mnemonic="Bidibidi Cash #1", cycle_name="Nov 2026", cycle_id=13, number_of_payments=1,
    total_amount=100.0, currency_code="UGX", schedule_date="2026-11-13")}
batch = {"siganature": "string", "header": HEADER, "message": [{
    "disbursement_envelope_id": "ENV-1", "beneficiary_id": "UGA-00423318", "mis_reference_number": "1",
    "beneficiary_name": "Test", "disbursement_amount": 100.0, "narrative": "Payment"}]}

failed = False
for name, model, payload_model, body in (
        ("create envelope", DisbursementEnvelopeRequest, DisbursementEnvelopePayload, envelope),
        ("create disbursements", DisbursementRequest, DisbursementPayload, batch)):
    try:
        model.model_validate(body)
        print(f"{name}: ACCEPTED")
    except Exception as e:  # pydantic ValidationError
        failed = True
        print(f"{name}: REJECTED, missing/invalid top-level fields: {sorted({str(x['loc'][0]) for x in e.errors()})}")
        sent = body["message"] if isinstance(body["message"], dict) else body["message"][0]
        unknown = sorted(set(sent) - set(payload_model.model_fields))
        print(f"    message fields with no field of the same name in {payload_model.__name__}: {unknown}")
sys.exit(1 if failed else 0)
