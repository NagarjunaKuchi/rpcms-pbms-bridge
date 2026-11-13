import datetime

import pytest

from rpcms_mapping import (PROGRES_ID_TYPE_NAME, build_envelope_message, cycle_code,
                           manager_config_problems, normalise_progres_id)


def test_normalise_matches_dedup_rule():
    assert normalise_progres_id("  uga-00423318 ") == "UGA-00423318"
    for bad in ("", "   ", None):
        with pytest.raises(ValueError):
            normalise_progres_id(bad)


def test_cycle_code_is_unique_stable_and_safe():
    assert cycle_code("September 2026", 7) == "SEPTEMBER-2026-C7"
    assert cycle_code("September 2026", 7) == cycle_code("September 2026", 7)
    assert cycle_code("Cycle", 1) != cycle_code("Cycle", 2)
    assert cycle_code("Sep", 1) != cycle_code("Sep", 2)
    assert cycle_code("  ", 9) == "C9" and cycle_code(None, 9) == "C9"
    assert cycle_code("Würzburg / Ñ #3!", 4) == "W-RZBURG-3-C4"
    long = cycle_code("x" * 100, 5)
    assert len(long) <= 32 + 4 and long.endswith("-C5")
    for bad in (0, -1, None, "3"):
        with pytest.raises(ValueError):
            cycle_code("a", bad)


def test_envelope_message_matches_upstream_shape_except_cycle_code():
    m = build_envelope_message(program_mnemonic="Bidibidi Cash #3", cycle_name="Nov", cycle_id=12,
                               number_of_payments=214, total_amount=1200.5, currency_code="UGX",
                               schedule_date=datetime.date(2026, 11, 20))
    assert set(m) == {"benefit_program_mnemonic", "disbursement_frequency", "cycle_code_mnemonic",
                      "number_of_beneficiaries", "number_of_disbursements", "total_disbursement_amount",
                      "disbursement_currency_code", "disbursement_schedule_date"}
    assert m["cycle_code_mnemonic"] == "NOV-C12" and m["cycle_code_mnemonic"] != "SEP"
    assert m["disbursement_schedule_date"] == "2026-11-20" and m["disbursement_frequency"] == "OnDemand"


def test_manager_config_problems():
    ok = dict(payee_id_type="reg_id", id_type_name=PROGRES_ID_TYPE_NAME, prefix=None, suffix="")
    assert manager_config_problems(**ok) == []
    assert len(manager_config_problems(**{**ok, "payee_id_type": "phone"})) == 1
    assert len(manager_config_problems(**{**ok, "id_type_name": "National ID"})) == 1
    assert len(manager_config_problems(**{**ok, "prefix": "reg_id:", "suffix": "@x"})) == 2
