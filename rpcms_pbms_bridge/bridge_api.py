"""Requests to, and responses from, the G2P Bridge Partner API. No Odoo imports, so it can be tested alone.

Shapes follow the Bridge's own request/response schemas (G2PRequest: ``request_header`` + ``request_body``;
G2PResponse: ``response_header`` + ``response_body``) and the valid payloads in the Bridge sanity suite
(test/sanity/tests/test_l2_e2e_cash.py). They were read from one shallow clone of the Bridge ``develop`` branch dated
2026-08-28, not from a running Bridge or a release: re-run tests/test_bridge_api_contract.py against the Bridge
version you deploy.

Endpoints (appended to the Bridge base URL by whoever configures the payment manager URLs):
    /create_disbursement_envelopes   envelope_request / parse_envelope_response
    /create_disbursements            disbursements_request / parse_disbursement_response
    /get_disbursement_status         status_request / parse_status_response
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

BENEFIT_TYPE = "CASH_DIGITAL"  # this adapter only sends cash; other Bridge benefit types are not supported here

# Values of the Bridge's DisbursementFrequency enum. The addon's legacy "OnDemand" is NOT one of them.
FREQUENCIES = ("Daily", "Weekly", "Fortnightly", "Monthly", "BiMonthly", "Quarterly", "SemiAnnually", "Annually")


class BridgeError(Exception):
    """The Bridge answered with response_status ERROR, or with a body we cannot read."""

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


def canonical_json(body: dict) -> str:
    """The text the Bridge rebuilds before checking a signature: the parsed request body as compact JSON with
    sorted keys and unescaped UTF-8 (orjson with OPT_SORT_KEYS in openg2p_fastapi_common). Sign exactly this.
    Matches orjson for the values this module sends (strings, ints, plain floats, null); tests compare against
    orjson when it is installed."""
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def partner_key_reference(sender_app_mnemonic: str) -> str:
    """The Keymanager reference the Bridge looks the sender's key up under (from the Bridge's JWT helper)."""
    return "PARTNER_" + sender_app_mnemonic.replace("-", "_").upper()


def request_header(request_id: str, sender_app_mnemonic: str, sender_app_url: str, now: datetime | None = None) -> dict:
    return {
        "sender_app_mnemonic": sender_app_mnemonic,
        "sender_app_url": sender_app_url,
        "request_id": request_id,
        "request_timestamp": (now or datetime.now(timezone.utc)).isoformat(),
        "instance_id": None,
    }


def _wrap(header: dict, payload, **body_extra) -> dict:
    return {"request_header": header,
            "request_body": {"pagination_request": None, "request_payload": payload, **body_extra}}


def envelope_request(header: dict, *, benefit_program_id: int, benefit_program_mnemonic: str,
                     target_registry: str, benefit_code_id: int, benefit_code_mnemonic: str,
                     disbursement_cycle_id: int, disbursement_frequency: str, cycle_code_mnemonic: str,
                     number_of_payments: int, total_quantity: float, measurement_unit: str, schedule_date,
                     benefit_program_description: str | None = None,
                     benefit_code_description: str | None = None) -> dict:
    if disbursement_frequency not in FREQUENCIES:
        raise ValueError(f"disbursement_frequency must be one of {FREQUENCIES}, got {disbursement_frequency!r}")
    payload = {
        "benefit_program_id": benefit_program_id,
        "benefit_program_mnemonic": benefit_program_mnemonic,
        "benefit_program_description": benefit_program_description,
        "target_registry": target_registry,
        "benefit_code_id": benefit_code_id,
        "benefit_code_mnemonic": benefit_code_mnemonic,
        "benefit_code_description": benefit_code_description,
        "benefit_type": BENEFIT_TYPE,
        "disbursement_cycle_id": disbursement_cycle_id,
        "disbursement_frequency": disbursement_frequency,
        "cycle_code_mnemonic": cycle_code_mnemonic,
        "number_of_beneficiaries": number_of_payments,
        "number_of_disbursements": number_of_payments,
        "total_disbursement_quantity": total_quantity,
        "measurement_unit": measurement_unit,
        "disbursement_schedule_date": str(schedule_date)[:10],  # the Bridge field is a date
    }
    return _wrap(header, [payload])


def disbursement_id_for(sender_app_mnemonic: str, payment_id: int) -> str:
    """Deterministic and unique per payment: the Bridge enforces uniqueness of disbursement_id, so a resend of
    the same payment is refused by the Bridge instead of paying twice."""
    return f"{sender_app_mnemonic}-P{payment_id}"


def disbursement_item(*, disbursement_id: str, envelope_id: str, beneficiary_id: str, beneficiary_name: str,
                      quantity: float, narrative: str, cycle_id: int) -> dict:
    return {
        "disbursement_id": disbursement_id,
        "disbursement_envelope_id": envelope_id,
        "beneficiary_id": beneficiary_id,
        "beneficiary_name": beneficiary_name,
        "disbursement_quantity": quantity,
        "disbursement_cycle_id": cycle_id,
        "narrative": narrative,
    }


def disbursements_request(header: dict, items: list[dict], batch_control_id: str | None = None) -> dict:
    extra = {"disbursement_batch_control_id": batch_control_id} if batch_control_id else {}
    return _wrap(header, items, **extra)


def status_request(header: dict, disbursement_ids: list[str]) -> dict:
    return _wrap(header, [str(i) for i in disbursement_ids])


def _check_header(body) -> dict:
    try:
        header = body["response_header"]
        status = header["response_status"]
    except (KeyError, TypeError):
        raise BridgeError("Response has no response_header.response_status (not a Bridge Partner API response?)")
    if status != "SUCCESS":
        raise BridgeError(header.get("response_error_message") or "Bridge returned an error",
                          header.get("response_error_code"))
    return body.get("response_body") or {}


def parse_envelope_response(body) -> str:
    """The id of the created envelope. Raises BridgeError on an ERROR response or when no id came back."""
    payload = _check_header(body).get("response_payload") or []
    if not payload or not payload[0].get("id"):
        raise BridgeError("Bridge reported SUCCESS but returned no envelope id")
    return str(payload[0]["id"])


def parse_disbursement_response(body) -> dict[str, list[str]]:
    """{disbursement_id: [error codes]} for every disbursement the Bridge echoed back (empty list = accepted).

    A header ERROR with a payload (per-disbursement errors) is returned as data, because some disbursements in the
    batch may still be fine; a header ERROR without any payload raises BridgeError."""
    try:
        status = body["response_header"]["response_status"]
    except (KeyError, TypeError):
        raise BridgeError("Response has no response_header.response_status (not a Bridge Partner API response?)")
    payload = (body.get("response_body") or {}).get("response_payload") or []
    if status != "SUCCESS" and not payload:
        _check_header(body)  # raises with the Bridge's code and message
    return {str(p["disbursement_id"]): [str(c) for c in (p.get("response_error_codes") or [])] for p in payload}


def parse_status_response(body) -> list[dict]:
    """Recon records, one dict per reconciled disbursement (keys as the Bridge names them), in Bridge order.
    Only successful reconciliations (disbursement_recon_payloads); the Bridge's error reconciliations
    (disbursement_error_recon_payloads) are ignored here."""
    out = []
    for p in _check_header(body).get("response_payload") or []:
        records = p.get("disbursement_recon_records") or {}
        out.extend(records.get("disbursement_recon_payloads") or [])
    return out
