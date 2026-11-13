import datetime

import pytest

import rpcms_bridge_api as api

NOW = datetime.datetime(2026, 11, 13, 9, 0, tzinfo=datetime.timezone.utc)
HEADER = api.request_header("req-1", "rpcms-pbms", "https://pbms.example.org", NOW)


def envelope(**kw):
    base = dict(benefit_program_id=11, benefit_program_mnemonic="Bidibidi Cash #1", target_registry="PROGRES",
                benefit_code_id=22, benefit_code_mnemonic="CASH-UGX", disbursement_cycle_id=13,
                disbursement_frequency="Monthly", cycle_code_mnemonic="NOV-2026-C13", number_of_payments=2,
                total_quantity=100000.0, measurement_unit="UGX", schedule_date=NOW)
    return api.envelope_request(HEADER, **{**base, **kw})


def item(i=1, **kw):
    base = dict(disbursement_id=api.disbursement_id_for("rpcms-pbms", i), envelope_id="ENV-1",
                beneficiary_id="UGA-00423318", beneficiary_name="A. Person", quantity=50000.0,
                narrative="Payment for Nov 2026", cycle_id=13)
    return api.disbursement_item(**{**base, **kw})


def test_header_and_envelope_shape():
    assert HEADER == {"sender_app_mnemonic": "rpcms-pbms", "sender_app_url": "https://pbms.example.org",
                      "request_id": "req-1", "request_timestamp": "2026-11-13T09:00:00+00:00", "instance_id": None}
    body = envelope()
    assert set(body) == {"request_header", "request_body"}
    (p,) = body["request_body"]["request_payload"]
    assert p["benefit_type"] == "CASH_DIGITAL" and p["number_of_beneficiaries"] == p["number_of_disbursements"] == 2
    assert p["total_disbursement_quantity"] == 100000.0 and p["measurement_unit"] == "UGX"
    assert p["disbursement_schedule_date"] == "2026-11-13"  # a date, not a timestamp


@pytest.mark.parametrize("freq", ["OnDemand", "monthly", "", None])
def test_frequency_must_be_a_bridge_value(freq):
    with pytest.raises(ValueError):
        envelope(disbursement_frequency=freq)


def test_disbursement_ids_are_deterministic_and_distinct():
    assert api.disbursement_id_for("a", 7) == api.disbursement_id_for("a", 7)
    assert api.disbursement_id_for("a", 7) != api.disbursement_id_for("a", 8)
    assert api.disbursement_id_for("a", 7) != api.disbursement_id_for("b", 7)


def test_disbursements_request_carries_batch_control_id_only_when_given():
    assert "disbursement_batch_control_id" not in api.disbursements_request(HEADER, [item()])["request_body"]
    body = api.disbursements_request(HEADER, [item()], "BATCH-9")
    assert body["request_body"]["disbursement_batch_control_id"] == "BATCH-9"


def ok(payload):
    return {"response_header": {"request_id": "r", "response_status": "SUCCESS", "response_timestamp": "2026-11-13T09:00:00"},
            "response_body": {"response_payload": payload}}


def err(code="INVALID_PROGRAM_ID", payload=None):
    return {"response_header": {"request_id": "r", "response_status": "ERROR", "response_error_code": code,
                                "response_error_message": code, "response_timestamp": "2026-11-13T09:00:00"},
            "response_body": {"response_payload": payload if payload is not None else []}}


def test_envelope_response_parsing():
    assert api.parse_envelope_response(ok([{"id": "ENV-9"}])) == "ENV-9"
    with pytest.raises(api.BridgeError) as e:
        api.parse_envelope_response(err("INVALID_BENEFIT_CODE_ID"))
    assert e.value.code == "INVALID_BENEFIT_CODE_ID"
    for bad in (ok([]), ok([{"id": None}]), {"message": {"disbursement_envelope_id": "X"}}, None, {}):
        with pytest.raises(api.BridgeError):
            api.parse_envelope_response(bad)


def test_disbursement_response_per_item_errors_are_data_not_exceptions():
    res = api.parse_disbursement_response(err("INVALID_NARRATIVE", [
        {"disbursement_id": "a-P1", "response_error_codes": []},
        {"disbursement_id": "a-P2", "response_error_codes": ["INVALID_BENEFICIARY_ID", "INVALID_NARRATIVE"]}]))
    assert res == {"a-P1": [], "a-P2": ["INVALID_BENEFICIARY_ID", "INVALID_NARRATIVE"]}
    assert api.parse_disbursement_response(ok([{"disbursement_id": "a-P1"}])) == {"a-P1": []}
    with pytest.raises(api.BridgeError) as e:  # ERROR and nothing to say which item
        api.parse_disbursement_response(err("DISBURSEMENT_ENVELOPE_NOT_FOUND"))
    assert e.value.code == "DISBURSEMENT_ENVELOPE_NOT_FOUND"
    with pytest.raises(api.BridgeError):
        api.parse_disbursement_response({"message": []})


def test_status_response_parsing():
    recon = {"disbursement_id": "a-P1", "remittance_reference_number": "RR1", "remittance_statement_id": "S1",
             "remittance_entry_sequence": "3", "reversal_found": False}
    body = ok([{"reconciliation_id": "x", "disbursement_recon_records": {"disbursement_recon_payloads": [recon]}},
               {"reconciliation_id": "y", "disbursement_recon_records": None}])
    assert api.parse_status_response(body) == [recon]
    assert api.parse_status_response(ok([])) == []
    with pytest.raises(api.BridgeError):
        api.parse_status_response(err())


def test_canonical_json_is_sorted_compact_and_utf8():
    body = api.disbursements_request(HEADER, [item(1, beneficiary_name="Zoë Ñ / 100%")], "B-1")
    text = api.canonical_json(body)
    assert text.startswith('{"request_body":') and '", "' not in text and '": ' not in text and ", " not in text
    assert "Zoë" in text and "\\u" not in text
    assert api.canonical_json({"b": 1, "a": {"d": 2, "c": [3, 1]}}) == '{"a":{"c":[3,1],"d":2},"b":1}'


def test_canonical_json_equals_what_the_bridge_signs_when_orjson_is_available():
    orjson = pytest.importorskip("orjson")
    for body in (envelope(), api.disbursements_request(HEADER, [item(1, beneficiary_name="Zoë Ñ", quantity=12.5),
                                                                item(2, quantity=1000000.0)], "B-1"),
                 api.status_request(HEADER, ["a-P1"])):
        assert api.canonical_json(body).encode() == orjson.dumps(body, option=orjson.OPT_SORT_KEYS)


def test_partner_key_reference_matches_the_bridge_rule():
    assert api.partner_key_reference("rpcms-pbms") == "PARTNER_RPCMS_PBMS"
