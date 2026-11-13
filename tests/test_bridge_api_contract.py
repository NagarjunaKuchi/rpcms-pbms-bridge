"""Validate what bridge_api builds against the G2P Bridge's own pydantic schemas.

Skipped unless the Bridge models package is installed:
    pip install -e <g2p-bridge>/core/models "sqlalchemy[asyncio]"
Shape only: nothing is sent to a Bridge. Re-run against the Bridge version you deploy.
"""
import pytest

pytest.importorskip("openg2p_g2p_bridge_models.schemas", exc_type=ImportError)
from openg2p_g2p_bridge_models.schemas.disbursement import DisbursementRequest, DisbursementResponse
from openg2p_g2p_bridge_models.schemas.disbursement_envelope import (DisbursementEnvelopeRequest,
                                                                     DisbursementEnvelopeResponse)
from openg2p_g2p_bridge_models.schemas.disbursement_envelope import DisbursementStatusRequest

import rpcms_bridge_api as api
from test_bridge_api import HEADER, envelope, item


def test_envelope_request_is_accepted_and_nothing_is_dropped():
    body = envelope()
    parsed = DisbursementEnvelopeRequest.model_validate(body)
    sent = body["request_body"]["request_payload"][0]
    got = parsed.request_body.request_payload[0].model_dump(mode="json")
    for k, v in sent.items():
        if v is not None:
            assert str(got[k])[:10] == str(v)[:10] or got[k] == v, k  # every field we send survives validation
    assert parsed.request_body.request_payload[0].benefit_type.value == "CASH_DIGITAL"


def test_disbursement_request_is_accepted_and_nothing_is_dropped():
    body = api.disbursements_request(HEADER, [item(1), item(2)], "BATCH-9")
    parsed = DisbursementRequest.model_validate(body)
    assert parsed.request_body.disbursement_batch_control_id == "BATCH-9"
    assert [p.disbursement_quantity for p in parsed.request_body.request_payload] == [50000.0, 50000.0]
    sent = body["request_body"]["request_payload"][0]
    assert set(sent) <= set(type(parsed.request_body.request_payload[0]).model_fields)  # no unknown field names


def test_status_request_is_accepted():
    DisbursementStatusRequest.model_validate(api.status_request(HEADER, ["a-P1", 2]))


def test_our_parsers_read_real_bridge_responses():
    """Build responses with the Bridge's own response models, serialise them as FastAPI would, and parse them."""
    from openg2p_g2p_bridge_models.schemas.disbursement import DisbursementPayload, DisbursementResponseBody
    from openg2p_g2p_bridge_models.schemas.disbursement_envelope import (DisbursementEnvelopePayload,
                                                                         DisbursementEnvelopeResponseBody)
    from openg2p_fastapi_common.schemas import G2PResponseHeader
    hdr = lambda status: G2PResponseHeader(request_id="r", response_status=status, response_timestamp="2026-11-13T09:00:00")
    env = DisbursementEnvelopeResponse(response_header=hdr("SUCCESS"), response_body=DisbursementEnvelopeResponseBody(
        response_payload=[DisbursementEnvelopePayload(id="ENV-77")]))
    assert api.parse_envelope_response(env.model_dump(mode="json")) == "ENV-77"
    dis = DisbursementResponse(response_header=hdr("ERROR"), response_body=DisbursementResponseBody(response_payload=[
        DisbursementPayload(disbursement_id="a-P1", response_error_codes=[]),
        DisbursementPayload(disbursement_id="a-P2", response_error_codes=["INVALID_NARRATIVE"])]))
    assert api.parse_disbursement_response(dis.model_dump(mode="json")) == {"a-P1": [], "a-P2": ["INVALID_NARRATIVE"]}
