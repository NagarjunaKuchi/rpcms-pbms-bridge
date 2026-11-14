import base64
import datetime
import json

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient

import mock_bridge
import rpcms_bridge_api as api

NOW = datetime.datetime(2026, 11, 14, 9, 0, tzinfo=datetime.timezone.utc)


def hdr(rid="r1"):
    return api.request_header(rid, "rpcms-pbms", "https://pbms.example.org", NOW)


def env_body(**kw):
    base = dict(benefit_program_id=11, benefit_program_mnemonic="Bidibidi", target_registry="PROGRES", benefit_code_id=22,
                benefit_code_mnemonic="CASH-UGX", disbursement_cycle_id=13, disbursement_frequency="Monthly",
                cycle_code_mnemonic="NOV-2026-C13", number_of_payments=2, total_quantity=100000.0,
                measurement_unit="UGX", schedule_date=NOW)
    return api.envelope_request(hdr(), **{**base, **kw})


def items(ids=(1, 2), env="ENV-1"):
    return [api.disbursement_item(disbursement_id=api.disbursement_id_for("rpcms-pbms", i), envelope_id=env,
                                  beneficiary_id=f"UGA-{i}", beneficiary_name="A", quantity=50000.0, narrative="n",
                                  cycle_id=13) for i in ids]


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("MOCK_BRIDGE_STATE", str(tmp_path / "s.json"))
    monkeypatch.delenv("MOCK_BRIDGE_PUBLIC_KEY_FILE", raising=False)
    return tmp_path / "s.json"


@pytest.fixture
def client(state):
    return TestClient(mock_bridge.create_app())


def test_full_flow_through_the_addons_own_builders_and_parsers(client):
    eid = api.parse_envelope_response(client.post("/create_disbursement_envelopes", json=env_body()).json())
    assert eid == "ENV-1"
    res = api.parse_disbursement_response(client.post("/create_disbursements", json=api.disbursements_request(hdr(), items(env=eid), "B1")).json())
    assert res == {"rpcms-pbms-P1": [], "rpcms-pbms-P2": []}
    ids = list(res)
    assert api.parse_status_response(client.post("/get_disbursement_status", json=api.status_request(hdr(), ids)).json()) == []
    assert client.post("/dev/settle").json() == {"settled": ids}
    recons = api.parse_status_response(client.post("/get_disbursement_status", json=api.status_request(hdr(), ids)).json())
    assert [r["disbursement_id"] for r in recons] == ids
    assert all(r["remittance_statement_id"] and r["reversal_statement_id"] is None for r in recons)
    client.post("/dev/settle", json=[ids[0]], params={"reverse": True})
    again = api.parse_status_response(client.post("/get_disbursement_status", json=api.status_request(hdr(), ids)).json())
    assert again[0]["reversal_statement_id"] and again[1]["reversal_statement_id"] is None


def test_resent_disbursement_is_refused_and_nothing_partial_is_stored(client):
    client.post("/create_disbursement_envelopes", json=env_body())
    client.post("/create_disbursements", json=api.disbursements_request(hdr(), items([1])))
    res = api.parse_disbursement_response(client.post("/create_disbursements", json=api.disbursements_request(hdr(), items([1, 2]))).json())
    assert res["rpcms-pbms-P1"] == ["DUPLICATE_DISBURSEMENT"] and res["rpcms-pbms-P2"] == []
    assert list(client.get("/dev/state").json()["disbursements"]) == ["rpcms-pbms-P1"]


def test_unknown_envelope_and_missing_beneficiary(client):
    res = api.parse_disbursement_response(client.post("/create_disbursements", json=api.disbursements_request(hdr(), items([1], env="ENV-9"))).json())
    assert res == {"rpcms-pbms-P1": ["DISBURSEMENT_ENVELOPE_NOT_FOUND"]}
    client.post("/create_disbursement_envelopes", json=env_body())
    bad = items([3])
    bad[0]["beneficiary_id"] = ""
    res = api.parse_disbursement_response(client.post("/create_disbursements", json=api.disbursements_request(hdr(), bad)).json())
    assert res == {"rpcms-pbms-P3": ["INVALID_BENEFICIARY_ID"]}


def test_envelope_validation_errors_are_reported_as_bridge_errors(client):
    body = env_body()
    body["request_body"]["request_payload"][0]["disbursement_frequency"] = "OnDemand"  # the legacy value the Bridge rejects
    with pytest.raises(api.BridgeError) as e:
        api.parse_envelope_response(client.post("/create_disbursement_envelopes", json=body).json())
    assert e.value.code == "INVALID_DISBURSEMENT_FREQUENCY"
    body = env_body(); body["request_body"]["request_payload"][0]["benefit_program_id"] = 0  # noqa: E702
    with pytest.raises(api.BridgeError) as e:
        api.parse_envelope_response(client.post("/create_disbursement_envelopes", json=body).json())
    assert e.value.code == "INVALID_PROGRAM_ID"


def test_legacy_shaped_request_is_rejected_like_the_real_bridge_does(client):
    legacy = {"signature": "x", "header": {"message_id": "m"}, "message": {"envelope_id": "e"}}
    r = client.post("/create_disbursements", json=legacy).json()
    assert r["response_header"]["response_status"] == "ERROR" and r["response_header"]["response_error_code"] == "INVALID_REQUEST"


def test_state_survives_a_restart(state):
    c1 = TestClient(mock_bridge.create_app())
    c1.post("/create_disbursement_envelopes", json=env_body())
    c1.post("/create_disbursements", json=api.disbursements_request(hdr(), items([1])))
    c2 = TestClient(mock_bridge.create_app())
    assert list(c2.get("/dev/state").json()["disbursements"]) == ["rpcms-pbms-P1"]
    assert c2.post("/dev/reset").json() == {"reset": True}
    assert json.loads(state.read_text())["disbursements"] == {}


def _sign(key, body):
    b64 = lambda d: base64.urlsafe_b64encode(d).decode().rstrip("=")  # noqa: E731
    head = b64(json.dumps({"alg": "RS256"}).encode())
    sig = key.sign(f"{head}.{b64(api.canonical_json(body).encode())}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{head}..{b64(sig)}"


def test_signature_check_when_a_public_key_is_configured(state, tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub = tmp_path / "pub.pem"
    pub.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    monkeypatch.setenv("MOCK_BRIDGE_PUBLIC_KEY_FILE", str(pub))
    c = TestClient(mock_bridge.create_app())
    body = env_body()
    ok = c.post("/create_disbursement_envelopes", json=body, headers={"Signature": _sign(key, body)}).json()
    assert ok["response_header"]["response_status"] == "SUCCESS"
    for headers in ({}, {"Signature": "a..b"}, {"Signature": _sign(rsa.generate_private_key(public_exponent=65537, key_size=2048), body)}):
        bad = c.post("/create_disbursement_envelopes", json=body, headers=headers).json()
        assert bad["response_header"]["response_error_code"] == "rjct_jwt_invalid"
    tampered = env_body(total_quantity=1.0)
    bad = c.post("/create_disbursement_envelopes", json=tampered, headers={"Signature": _sign(key, body)}).json()
    assert bad["response_header"]["response_error_code"] == "rjct_jwt_invalid"


def test_responses_validate_against_the_real_bridge_models(client):
    pytest.importorskip("openg2p_g2p_bridge_models.schemas", exc_type=ImportError)
    from openg2p_g2p_bridge_models.schemas.disbursement import DisbursementResponse
    from openg2p_g2p_bridge_models.schemas.disbursement_envelope import DisbursementEnvelopeResponse
    DisbursementEnvelopeResponse.model_validate(client.post("/create_disbursement_envelopes", json=env_body()).json())
    DisbursementResponse.model_validate(client.post("/create_disbursements", json=api.disbursements_request(hdr(), items([1]))).json())
