"""Shows why the per-cycle code matters, against the real Duplicate Guard service.
Skipped when rpcms-dedup is not installed."""

import pytest

pytest.importorskip("rpcms_dedup")
from fastapi.testclient import TestClient  # noqa: E402
from rpcms_dedup.api import create_app  # noqa: E402
from rpcms_dedup.config import Settings  # noqa: E402
from rpcms_dedup.db import init_schema, make_engine  # noqa: E402

from rpcms_mapping import cycle_code, normalise_progres_id  # noqa: E402

API_KEY = "test-api-key-123456"


@pytest.fixture()
def guard(tmp_path):
    s = Settings(database_url=f"sqlite:///{tmp_path}/g.db", hmac_key="k" * 40, api_key=API_KEY)
    e = make_engine(s.database_url)
    init_schema(e)
    return TestClient(create_app(s, e), headers={"X-API-Key": API_KEY})


def event(n, cycle):
    return {"idempotency_key": f"idem-key-{n:04d}", "progres_id": normalise_progres_id(" uga-00423318"),
            "assistance_type": "BIDIBIDI CASH #3", "cycle": cycle, "partner_id": "NHU",
            "record_id": f"PAY-{n}", "recorded_at": "2026-11-13T08:00:00Z"}


def test_hardcoded_sep_blocks_second_cycle(guard):
    assert guard.post("/v1/assistance-events", json=event(1, "SEP")).status_code == 201
    second = guard.post("/v1/assistance-events", json=event(2, "SEP"))  # next month, same code
    assert second.status_code == 409 and second.json()["code"] == "DUPLICATE_ASSISTANCE"


def test_per_cycle_code_allows_next_cycle_but_still_blocks_same_cycle(guard):
    assert guard.post("/v1/assistance-events", json=event(1, cycle_code("Oct", 11))).status_code == 201
    assert guard.post("/v1/assistance-events", json=event(2, cycle_code("Nov", 12))).status_code == 201
    again = guard.post("/v1/assistance-events", json=event(3, cycle_code("Nov", 12)))
    assert again.status_code == 409
