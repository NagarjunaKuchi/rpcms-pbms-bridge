"""DEVELOPMENT ONLY. A stand-in for the G2P Bridge Partner API. It is NOT the Bridge.

It lets the PBMS addon be exercised end to end without a real Bridge: it accepts the three Partner API calls, applies
the validation rules read from the Bridge source (one shallow clone dated 2026-08-28; not a running Bridge), keeps
state in memory (and in a JSON file so a restart does not forget what it accepted), and lets you "settle" accepted
disbursements by hand so the status check has something to copy into Odoo.

What it does not do: move money, call a bank, talk to SPAR, check that benefit program / code ids exist, or behave
like any specific Bridge release. Unknown whether the real Bridge answers a resent disbursement_id with
DUPLICATE_DISBURSEMENT: this mock does.

Run:   uvicorn --factory mock_bridge:create_app --port 8010          (from dev/mock_bridge)
Env:   MOCK_BRIDGE_STATE=path.json            where state is kept (default ./mock_bridge_state.json)
       MOCK_BRIDGE_PUBLIC_KEY_FILE=pub.pem    if set, requests must carry a valid detached-JWS Signature header
                                              (the rule the Bridge applies when its signature validation is on)
"""
from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Header, Request

FREQ = ("Daily", "Weekly", "Fortnightly", "Monthly", "BiMonthly", "Quarterly", "SemiAnnually", "Annually")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def canonical(body: dict) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class Store:
    def __init__(self, path: str | None):
        self.path = Path(path) if path else None
        self.envelopes: dict[str, dict] = {}
        self.disbursements: dict[str, dict] = {}
        self.recons: dict[str, dict] = {}
        if self.path and self.path.exists():
            data = json.loads(self.path.read_text())
            self.envelopes, self.disbursements, self.recons = data["envelopes"], data["disbursements"], data["recons"]

    def save(self):
        if self.path:
            self.path.write_text(json.dumps({"envelopes": self.envelopes, "disbursements": self.disbursements,
                                             "recons": self.recons}, indent=1))


def create_app() -> FastAPI:
    app = FastAPI(title="RPCMS dev mock of the G2P Bridge (NOT the Bridge)")
    store = Store(os.environ.get("MOCK_BRIDGE_STATE", "./mock_bridge_state.json"))
    key_file = os.environ.get("MOCK_BRIDGE_PUBLIC_KEY_FILE")
    public = None
    if key_file:
        from cryptography.hazmat.primitives import serialization
        public = serialization.load_pem_public_key(Path(key_file).read_bytes())

    def signature_ok(body: dict, token: str | None) -> bool:
        if public is None:
            return True
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        try:
            head, _, sig = (token or "").split(".")
            signed = f"{head}.{_b64(canonical(body).encode())}".encode()
            public.verify(base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4)), signed, padding.PKCS1v15(),
                          hashes.SHA256())
            return True
        except Exception:
            return False

    def reply(body, status="SUCCESS", code=None, payload=None):
        rid = ((body or {}).get("request_header") or {}).get("request_id")
        return {"response_header": {"request_id": rid, "response_status": status, "response_error_code": code,
                                    "response_error_message": code,
                                    "response_timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()},
                "response_body": {"response_payload": payload if payload is not None else []}}

    async def parsed(request: Request, signature: str | None):
        try:
            body = await request.json()
            payload = body["request_body"]["request_payload"]
            assert isinstance(payload, list) and body["request_header"]["request_id"]
        except Exception:
            return None, None, reply(None, "ERROR", "INVALID_REQUEST")
        if not signature_ok(body, signature):
            return None, None, reply(body, "ERROR", "rjct_jwt_invalid")
        return body, payload, None

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "note": "dev mock of the G2P Bridge, not the Bridge"}

    @app.post("/create_disbursement_envelopes")
    async def envelopes(request: Request, signature: str | None = Header(default=None)):
        body, payload, err = await parsed(request, signature)
        if err:
            return err
        if len(payload) != 1:
            return reply(body, "ERROR", "INVALID_REQUEST")
        p = payload[0]
        for field, code in (("benefit_program_id", "INVALID_PROGRAM_ID"), ("benefit_code_id", "INVALID_BENEFIT_CODE_ID"),
                            ("cycle_code_mnemonic", "INVALID_CYCLE_CODE_MNEMONIC"),
                            ("target_registry", "INVALID_TARGET_REGISTRY")):
            if not p.get(field):
                return reply(body, "ERROR", code)
        if p.get("disbursement_frequency") not in FREQ:
            return reply(body, "ERROR", "INVALID_DISBURSEMENT_FREQUENCY")
        if (p.get("number_of_beneficiaries") or 0) < 1 or (p.get("number_of_disbursements") or 0) < 1:
            return reply(body, "ERROR", "INVALID_NO_OF_DISBURSEMENTS")
        if p.get("total_disbursement_quantity") is None or p["total_disbursement_quantity"] < 0:
            return reply(body, "ERROR", "INVALID_DISBURSEMENT_QUANTITY")
        eid = f"ENV-{len(store.envelopes) + 1}"
        store.envelopes[eid] = p
        store.save()
        return reply(body, payload=[{**p, "id": eid}])

    @app.post("/create_disbursements")
    async def disbursements(request: Request, signature: str | None = Header(default=None)):
        body, payload, err = await parsed(request, signature)
        if err:
            return err
        out, bad, seen = [], False, set()
        for d in payload:
            did = d.get("disbursement_id")
            codes = []
            if d.get("disbursement_envelope_id") not in store.envelopes:
                codes.append("DISBURSEMENT_ENVELOPE_NOT_FOUND")
            if did in store.disbursements or did in seen:
                codes.append("DUPLICATE_DISBURSEMENT")
            if not d.get("beneficiary_id"):
                codes.append("INVALID_BENEFICIARY_ID")
            if d.get("disbursement_cycle_id") is None:
                codes.append("INVALID_CYCLE")
            seen.add(did)
            bad = bad or bool(codes)
            out.append({"disbursement_id": did, "response_error_codes": codes})
        if not bad:  # all or nothing, as in the tests' fake
            for d in payload:
                store.disbursements[d["disbursement_id"]] = d
            store.save()
        return reply(body, "ERROR" if bad else "SUCCESS", "INVALID_DISBURSEMENT" if bad else None, out)

    @app.post("/get_disbursement_status")
    async def status(request: Request, signature: str | None = Header(default=None)):
        body, payload, err = await parsed(request, signature)
        if err:
            return err
        recons = [store.recons[i] for i in payload if i in store.recons]
        return reply(body, payload=[{"reconciliation_id": "MOCK-R1",
                                     "disbursement_recon_records": {"disbursement_recon_payloads": recons}}])

    # ---- development controls (not part of the Bridge API) -------------------------------------------------
    @app.get("/dev/state")
    def state():
        return {"envelopes": store.envelopes, "disbursements": store.disbursements, "recons": store.recons}

    @app.post("/dev/settle")
    def settle(ids: list[str] | None = None, reverse: bool = False):
        """Mark accepted disbursements as paid (all unsettled ones if no ids given). With reverse=true, also add a
        reversal so the status check shows that case. Updates what /get_disbursement_status reports."""
        done = []
        for did in (ids or list(store.disbursements)):
            if did not in store.disbursements:
                continue
            n = len(store.recons) + 1
            rec = store.recons.get(did) or {
                "disbursement_id": did, "remittance_reference_number": f"MOCK-REM-{n}",
                "remittance_statement_id": f"MOCK-STMT-{n}", "remittance_entry_sequence": str(n),
                "remittance_entry_date": datetime.now(timezone.utc).date().isoformat(),
                "reversal_statement_id": None, "reversal_entry_sequence": None,
                "reversal_entry_date": None, "reversal_reason": None}
            if reverse:
                rec.update(reversal_statement_id=f"MOCK-REV-{n}", reversal_entry_sequence=str(n),
                           reversal_entry_date=datetime.now(timezone.utc).date().isoformat(),
                           reversal_reason="dev mock reversal")
            store.recons[did] = rec
            done.append(did)
        store.save()
        return {"settled": done}

    @app.post("/dev/reset")
    def reset():
        store.envelopes.clear(); store.disbursements.clear(); store.recons.clear()  # noqa: E702
        store.save()
        return {"reset": True}

    return app
