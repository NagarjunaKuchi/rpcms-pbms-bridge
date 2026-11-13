"""Pure-Python mapping rules between OpenG2P PBMS and the G2P Bridge for RPCMS.

No Odoo imports here on purpose, so the rules can be unit-tested without an Odoo install.

Convention shared with rpcms-bridge-connectors / rpcms-dedup:
  beneficiary_id          = the beneficiary's UNHCR ProGres ID
  benefit_program_mnemonic= assistance type (PBMS already builds "<program name> #<manager id>")
  cycle_code_mnemonic     = a stable, unique code per PBMS cycle
"""

from __future__ import annotations

import re

PROGRES_ID_TYPE_NAME = "UNHCR ProGres ID"


def normalise_progres_id(value: str | None) -> str:
    """Same normalisation the Duplicate Guard applies (strip + upper-case)."""
    out = (value or "").strip().upper()
    if not out:
        raise ValueError("ProGres ID is empty")
    return out


def cycle_code(cycle_name: str | None, cycle_id: int) -> str:
    """Stable per-cycle code: readable slug of the name plus the PBMS cycle id.

    The id guarantees uniqueness across programs and renames-before-envelope-creation; the code
    is sent once when the envelope is created and then reused for every payment in that cycle.
    """
    if not isinstance(cycle_id, int) or cycle_id <= 0:
        raise ValueError("cycle_id must be a positive integer")
    slug = re.sub(r"[^A-Z0-9]+", "-", (cycle_name or "").upper()).strip("-")[:32].strip("-")
    return f"{slug}-C{cycle_id}" if slug else f"C{cycle_id}"


def build_envelope_message(*, program_mnemonic: str, cycle_name: str | None, cycle_id: int,
                           number_of_payments: int, total_amount: float, currency_code: str,
                           schedule_date) -> dict:
    """The ``message`` part of the Bridge create-envelope request.

    Identical to what g2p_payment_g2p_connect sends today except ``cycle_code_mnemonic``, which
    upstream hardcodes to "SEP".
    """
    return {
        "benefit_program_mnemonic": program_mnemonic,
        "disbursement_frequency": "OnDemand",
        "cycle_code_mnemonic": cycle_code(cycle_name, cycle_id),
        "number_of_beneficiaries": number_of_payments,
        "number_of_disbursements": number_of_payments,
        "total_disbursement_amount": total_amount,
        "disbursement_currency_code": currency_code,
        "disbursement_schedule_date": str(schedule_date),
    }


def manager_config_problems(*, payee_id_type: str | None, id_type_name: str | None,
                            prefix: str | None, suffix: str | None) -> list[str]:
    """Why a payment manager cannot send ProGres IDs as beneficiary_id (empty list = fine)."""
    problems = []
    if payee_id_type != "reg_id":
        problems.append("Payee ID Field must be 'Registrant ID'.")
    if id_type_name != PROGRES_ID_TYPE_NAME:
        problems.append(f"ID Type for Payee ID must be '{PROGRES_ID_TYPE_NAME}'.")
    if prefix:
        problems.append("Payee prefix must be empty (it would be sent as part of the ProGres ID).")
    if suffix:
        problems.append("Payee suffix must be empty (it would be sent as part of the ProGres ID).")
    return problems
