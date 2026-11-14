"""DEVELOPMENT ONLY. Plays the RPCMS duplicate-assistance scenario over HTTP against the dev services, using the
households and the assistance type / cycle code that dev/seed_usecase.py wrote to usecase.json.

Needs the Duplicate Guard (8001), the offline-sync API (8002) and `rpcms-sync-worker` running, and in the environment
(source ~/rpcms/secrets.sh): RPCMS_DEDUP_API_KEY, RPCMS_SYNC_JWT_KEY. Python with httpx and PyJWT (the services' venv has them).

    python dev/usecase_scenario.py [--file ~/rpcms/usecase.json] [--guard http://127.0.0.1:8001] [--sync http://127.0.0.1:8002]

Honest scope: the dev mock Bridge does NOT call the Duplicate Guard. In production the Bridge connector (rpcms-bridge-connectors)
records each paid disbursement in the Guard; here step 1 does that on its behalf with the same values.
Everything uses invented IDs. Run it ONCE per seed run: a second run finds UGA-DEV-0009 already taken by the first (correct Guard behaviour) and reports FAIL. Re-run dev/seed_usecase.py first for a fresh start.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import jwt

ap = argparse.ArgumentParser()
ap.add_argument("--file", default="~/rpcms/usecase.json")
ap.add_argument("--guard", default="http://127.0.0.1:8001")
ap.add_argument("--sync", default="http://127.0.0.1:8002")
ap.add_argument("--wait", type=int, default=60, help="seconds to wait for the sync worker")
a = ap.parse_args()
info = json.loads(Path(a.file).expanduser().read_text())
guard_key, jwt_key = os.environ.get("RPCMS_DEDUP_API_KEY"), os.environ.get("RPCMS_SYNC_JWT_KEY")
if not guard_key or not jwt_key:
    sys.exit("RPCMS_DEDUP_API_KEY / RPCMS_SYNC_JWT_KEY not set (run: source ~/rpcms/secrets.sh)")

ATYPE, CYCLE, AMOUNT, CUR = info["assistance_type"], info["cycle"], info["amount"], info["currency"]
PARTNER_A, PARTNER_B = "WFP-DEV", "DRC-DEV"  # invented partner codes
ids = [h["progres_id"] for h in info["households"]]
pay_of = {p["progres_id"]: p for p in info.get("payments", [])}
ok = True
now = datetime.now(timezone.utc)
gh = {"X-API-Key": guard_key}


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not cond else ""))


def officer(partner):
    tok = jwt.encode({"sub": f"officer-{partner}", "partner_id": partner, "exp": now + timedelta(minutes=30)}, jwt_key, algorithm="HS256")
    return {"Authorization": f"Bearer {tok}"}


def event(c, partner, pid, key, record, offline=False):
    return c.post(a.guard + "/v1/assistance-events", headers=gh, json={
        "idempotency_key": key, "progres_id": pid, "assistance_type": ATYPE, "cycle": CYCLE, "partner_id": partner,
        "record_id": record, "recorded_at": now.isoformat(), "amount": AMOUNT, "currency": CUR, "offline": offline})


with httpx.Client(timeout=20) as c:
    print(f"assistance type: {ATYPE!r}   cycle: {CYCLE!r}   households: {len(ids)}\n")
    check("Guard healthy", c.get(a.guard + "/healthz").status_code == 200)
    check("Sync API healthy", c.get(a.sync + "/healthz").status_code == 200)
    check("Guard refuses calls without its API key", c.get(a.guard + "/v1/referrals").status_code in (401, 403))

    print("\n-- Step 1: partner A (WFP-DEV) is paid via the Bridge; each payment becomes a Guard assistance event")
    for pid in ids:
        r = event(c, PARTNER_A, pid, f"{info['tag']}-A-{pid}", pay_of.get(pid, {}).get("disbursement_id") or f"rec-{pid}")
        check(f"A recorded {pid}", r.status_code in (200, 201), f"{r.status_code} {r.text[:150]}")

    print("\n-- Step 2: partner B (DRC-DEV) asks before paying (online duplicate check)")
    r = c.post(a.guard + "/v1/duplicate-checks", headers=gh, json={
        "progres_id": ids[2], "assistance_type": ATYPE, "cycle": CYCLE, "partner_id": PARTNER_B})
    j = r.json() if r.status_code == 200 else {}
    check(f"{ids[2]} is reported as already assisted", j.get("duplicate") is True, r.text[:200])
    check("... and it was assisted by partner A", (j.get("existing") or {}).get("partner_id") == PARTNER_A)
    check("... and a referral was opened", bool(j.get("referral_id")))
    r = c.post(a.guard + "/v1/duplicate-checks", headers=gh, json={
        "progres_id": "UGA-DEV-0009", "assistance_type": ATYPE, "cycle": CYCLE, "partner_id": PARTNER_B, "open_referral": False})
    check("a household nobody assisted (UGA-DEV-0009) is not a duplicate", r.status_code == 200 and r.json().get("duplicate") is False, r.text[:200])

    print("\n-- Step 3: partner B tries to record assistance for an already-assisted household, online")
    r = event(c, PARTNER_B, ids[3], f"{info['tag']}-B-{ids[3]}", f"B-rec-{ids[3]}")
    check(f"{ids[3]}: refused as duplicate (409)", r.status_code == 409, f"{r.status_code} {r.text[:150]}")
    check("... with a referral", bool((r.json().get("detail") or r.json()).get("referral_id")) if r.status_code == 409 else False, r.text[:200])

    print("\n-- Step 4: partner B's field officer works OFFLINE, then syncs one batch")
    run = str(int(time.time()))
    batch = {"clientBatchId": f"{info['tag']}-batch-{run}", "deviceId": "tablet-dev-01", "records": [
        {"clientRecordId": f"{run}-{pid}", "progresId": pid, "assistanceType": ATYPE, "cycle": CYCLE,
         "recordId": f"off-{run}-{pid}", "capturedAt": (now - timedelta(hours=3)).isoformat(), "amount": AMOUNT, "currency": CUR}
        for pid in ("UGA-DEV-0009", ids[4], ids[5])]}
    hb = officer(PARTNER_B)
    r = c.post(a.sync + "/api/v1/sync/batches", headers=hb, json=batch)
    check("batch accepted (202)", r.status_code == 202, f"{r.status_code} {r.text[:200]}")
    r = c.post(a.sync + "/api/v1/sync/batches", headers=hb, json=batch)
    check("sending the identical batch again is a harmless replay (200)", r.status_code == 200, f"{r.status_code}")
    states = {}
    deadline = time.time() + a.wait
    while time.time() < deadline:
        r = c.get(f"{a.sync}/api/v1/sync/batches/{batch['deviceId']}/{batch['clientBatchId']}", headers=hb)
        recs = r.json().get("records", r.json()) if r.status_code == 200 else []
        states = {x.get("clientRecordId"): x.get("state") for x in recs} if isinstance(recs, list) else {}
        if states and all(s != "PENDING" for s in states.values()):
            break
        time.sleep(2)
    print("   record states:", states)
    check("new household synced", states.get(f"{run}-UGA-DEV-0009") == "SYNCED")
    check("the two already-assisted households became CONFLICT", states.get(f"{run}-{ids[4]}") == "CONFLICT" and states.get(f"{run}-{ids[5]}") == "CONFLICT")

    print("\n-- Step 5: a programme officer works the referral queue")
    r = c.get(a.guard + "/v1/referrals", headers=gh, params={"status": "OPEN"})
    refs = r.json() if r.status_code == 200 else []
    refs = refs if isinstance(refs, list) else refs.get("items", [])
    mine = [x for x in refs if (x.get("assistance_type") or "").upper() == ATYPE.upper()]  # the Guard upper-cases codes
    print(f"   open referrals for this programme: {len(mine)}")
    check("at least 4 open referrals (1 check, 1 online refusal, 2 offline conflicts)", len(mine) >= 4, str(len(mine)))
    if len(mine) >= 2:
        r1 = c.post(f"{a.guard}/v1/referrals/{mine[0]['id']}/resolve", headers=gh, json={
            "resolution": "CONFIRMED_DUPLICATE", "resolved_by": "programme-officer-dev", "note": "Same household, already paid by WFP-DEV."})
        r2 = c.post(f"{a.guard}/v1/referrals/{mine[1]['id']}/resolve", headers=gh, json={
            "resolution": "FALSE_POSITIVE", "resolved_by": "programme-officer-dev", "note": "Different household, ProGres ID mis-keyed."})
        check("one referral resolved as CONFIRMED_DUPLICATE", r1.status_code == 200, f"{r1.status_code} {r1.text[:150]}")
        check("one referral resolved as FALSE_POSITIVE", r2.status_code == 200, f"{r2.status_code} {r2.text[:150]}")
    r = c.get(a.sync + "/api/v1/sync/summary", headers=hb)
    print("   partner B sync summary:", r.text[:200])

print("\nRESULT:", "ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
