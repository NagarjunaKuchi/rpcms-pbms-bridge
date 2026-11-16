"""DEVELOPMENT ONLY. Plays the RPCMS "Refugee Coordination Demo" (RPCMS-Refugee-Coordination-Demo_2_2.html) screen by screen
against the real dev services: compliance (:8003), UGPass MoU workflow (:8004), ProGres authentication (:8005),
Duplicate Guard (:8001) and offline sync (:8002), using the demo's own names, IDs, amounts and numbers.

    source ~/rpcms/secrets.sh
    ~/rpcms/venv/bin/python dev/usecase_demo.py [--suffix r1]

Needs the five services plus `rpcms-sync-worker` and `rpcms-compliance-worker` running, and in the environment
RPCMS_DEDUP_API_KEY, RPCMS_SYNC_JWT_KEY, RPCMS_COMPLIANCE_JWT_KEY, RPCMS_UGPASS_JWT_KEY, RPCMS_UGPASS_CALLBACK_SECRET, RPCMS_PROGRES_JWT_KEY.

ONE-SHOT per database: the Duplicate Guard remembers March 2026, so a second run sees Mary as already assisted. For a rehearsal use
--suffix NAME, which changes the cycle, MoU reference and record ids (the people stay the same).

Screens 7 (ID-renewal portal and SMS), 8 (self-reliance survey) and the national dashboard figures on 9 have NO service behind them in
these repos; this script says so instead of faking them. Every upstream (ProGres, UGPass, URSB, NGO Bureau, AMP, NIMES) is a development mock.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx
import jwt

ap = argparse.ArgumentParser()
ap.add_argument("--suffix", default="")
ap.add_argument("--wait", type=int, default=240)
for k, v in (("dedup", 8001), ("sync", 8002), ("compliance", 8003), ("ugpass", 8004), ("progres", 8005)):
    ap.add_argument(f"--{k}", default=f"http://127.0.0.1:{v}")
a = ap.parse_args()
SFX = a.suffix
E = {k: os.environ.get(k) for k in ("RPCMS_DEDUP_API_KEY", "RPCMS_SYNC_JWT_KEY", "RPCMS_COMPLIANCE_JWT_KEY",
                                    "RPCMS_UGPASS_JWT_KEY", "RPCMS_UGPASS_CALLBACK_SECRET", "RPCMS_PROGRES_JWT_KEY")}
missing = [k for k, v in E.items() if not v]
if missing:
    sys.exit("Missing environment: " + ", ".join(missing) + "  (source ~/rpcms/secrets.sh)")

NHU, WNRP = "NHU", "WNRP"                       # Nile Hope Uganda, West Nile Relief Partners
CYCLE = "2026-03" + (f"-{SFX}" if SFX else "")  # "March 2026"
ATYPE = "CASH-FOR-FOOD"
AMOUNT, CUR = 150000, "UGX"
AMP_ID = "AMP-UG-2026-00731"
MARY, NEXT_IN_QUEUE = "UGA-00423318", "UGA-00419907"
EAT = timezone(timedelta(hours=3))
RUN = str(int(time.time()))
ok = True


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"   ({detail})" if detail and not cond else ""))


def note(msg):
    print(f"  [NOTE] {msg}")


def token(key, sub, **claims):
    return {"Authorization": "Bearer " + jwt.encode(
        {"sub": sub, "exp": datetime.now(timezone.utc) + timedelta(minutes=30), **claims}, key, algorithm="HS256")}


def screen(n, title):
    print(f"\n=== Screen {n}: {title}")


c = httpx.Client(timeout=30)
gh = {"X-API-Key": E["RPCMS_DEDUP_API_KEY"]}
print("RPCMS demo run" + (f" (suffix {SFX})" if SFX else "") + f"  cycle={CYCLE}")
for name, url in (("dedup", a.dedup), ("sync", a.sync), ("compliance", a.compliance), ("ugpass", a.ugpass), ("progres", a.progres)):
    check(f"{name} service up", c.get(url + "/healthz").status_code == 200)

# ---------------------------------------------------------------- 0
screen(0, "Scene")
note("Mary Poni, 34, Bidibidi Zone 3; Nile Hope Uganda; UGX 1.2 bn cash-for-food. Narrative only, nothing to run.")

# ---------------------------------------------------------------- 1
screen(1, "Partner compliance (URSB + NGO Bureau)")
r = c.post(f"{a.compliance}/api/v1/partners/{NHU}/compliance-checks",
           headers={**token(E["RPCMS_COMPLIANCE_JWT_KEY"], "officer-nhu", partner_id=NHU), "Idempotency-Key": f"PRT-2026-0187-{RUN}"},
           json={"ursbRegistrationNo": "80020003318742", "ngoBureauPermitNo": "NGO/PMT/2025/4418", "ampRecordId": AMP_ID})
j = r.json() if r.status_code == 200 else {}
print("   ", json.dumps(j)[:400])
check("Nile Hope is COMPLIANT (URSB, NGO Bureau, AMP record)", j.get("overall") == "COMPLIANT", r.text[:200])
note("Not covered by the service: 'permit covers Yumbe', 'no open compliance issues', permit-expiry alert 60 days ahead.")

# ---------------------------------------------------------------- 2
screen(2, "e-MoU signed with UGPass")
coord = token(E["RPCMS_UGPASS_JWT_KEY"], "coordinator-opm", roles=["mou-admin"])
REF = "OPM/DoR/NHU/2026/014" + (f"-{SFX}" if SFX else "")
signers = [("Grace Achan", "Settlement Commandant, Bidibidi", "Office of the Prime Minister", 1, False),
           ("Dr. Samuel Okello", "Country Director", "Nile Hope Uganda", 2, True),
           ("Joseph Mugisha", "Commissioner for Refugees", "Office of the Prime Minister", 3, True)]
r = c.post(f"{a.ugpass}/api/v1/mous", headers=coord, json={
    "programmeId": "PRJ-BDB-2026-022", "reference": REF, "ampRecordId": AMP_ID,
    "signatories": [{"name": n, "role": ro, "organisation": o, "order": i, "sealRequired": s} for n, ro, o, i, s in signers]})
check("MoU created", r.status_code == 201, r.text[:200])
mou = r.json()
mid = mou.get("mouId")
for n, *_ in signers:
    r = c.post(f"{a.ugpass}/api/v1/mous/{mid}/signing-requests", headers=coord)
    check(f"signing request sent to {n}", r.status_code == 202, r.text[:200])
    body = json.dumps({"requestId": r.json().get("requestId"), "outcome": "APPROVED",
                       "certificateRef": "UGP-CERT-" + hashlib.sha256(n.encode()).hexdigest()[:8].upper(),
                       "signedAt": datetime.now(timezone.utc).isoformat()}).encode()
    sig = hmac.new(E["RPCMS_UGPASS_CALLBACK_SECRET"].encode(), body, hashlib.sha256).hexdigest()
    rr = c.post(f"{a.ugpass}/api/v1/callbacks/ugpass", content=body, headers={"X-UGPass-Signature": sig, "Content-Type": "application/json"})
    check(f"{n} approved in UGPass (signed callback accepted)", rr.status_code == 204, f"{rr.status_code} {rr.text[:150]}")
r = c.post(f"{a.ugpass}/api/v1/mous/{mid}/e-seals", headers=coord)
check("organisation e-Seals applied (Nile Hope Uganda + OPM)", r.status_code == 200 and r.json().get("status") == "COMPLETE", r.text[:200])
h = mou.get("documentHashSha256")
v = c.get(f"{a.ugpass}/api/v1/mous/{mid}/verification", params={"hash": h}).json()
print("    verification:", json.dumps(v)[:300])
check("public verification (QR target): document unchanged", v.get("documentUnchanged") is True)
v2 = c.get(f"{a.ugpass}/api/v1/mous/{mid}/verification", params={"hash": "0" * 64}).json()
check("'Tamper' case: a different document hash no longer verifies", v2.get("documentUnchanged") is False, json.dumps(v2)[:200])
note("The demo page signs with real ECDSA keys in the browser and embeds them in a QR code; the service stores the SHA-256 and the "
     "signing sequence only, and does not validate UGPass certificates. Signing time ('52 minutes') is not measured here.")

# ---------------------------------------------------------------- 3
screen(3, "Project registration, AMP funding, map")
r = c.get(f"{a.compliance}/api/v1/amp/records/{AMP_ID}", headers=token(E["RPCMS_COMPLIANCE_JWT_KEY"], "officer-nhu", partner_id=NHU))
print("   ", r.text[:300])
check("AMP record found and active", r.status_code == 200 and r.json().get("status") == "ACTIVE", r.text[:200])
note("Mock AMP returns funder 'MOCK FUNDER' and UGX 1,200,000,000; the demo's donor name (West Nile Resilience Fund), Q1 disbursement "
     "(UGX 300,000,000), 1,800 target households, the GIS map and the Zone 4 'overlap spotted' warning are not held by any service.")

# ---------------------------------------------------------------- 4
screen(4, "Beneficiary authentication with ProGres v4 (Yes/No only)")
officer = token(E["RPCMS_PROGRES_JWT_KEY"], "emmanuel.taban", partner_id=NHU)
ctx = {"deviceId": "tablet-zone3-pointA", "officerId": "emmanuel.taban", "partnerId": NHU,
       "capturedAt": datetime(2026, 3, 5, 11, 41, tzinfo=EAT).isoformat()}
bio = base64.b64encode(b"MATCH-dev-face-sample").decode()  # the mock treats bytes starting MATCH as a match; this is not a real biometric
r = c.post(f"{a.progres}/api/v1/progres/authenticate", headers={**officer, "Idempotency-Key": f"auth-mary-{RUN}"},
           json={"progresId": MARY, "modality": "FACE", "biometric": {"format": "dev-sample", "data": bio}, "context": ctx})
auth = r.json() if r.status_code == 200 else {}
print("   ", json.dumps(auth))
check("Mary: authenticated = YES, record active", auth.get("authenticated") is True and auth.get("recordActive") is True, r.text[:200])
check("response carries only the four allowed fields (no name, photo, DOB, household)",
      set(auth) == {"authenticated", "recordActive", "txnRef", "timestamp"}, str(sorted(auth)))
TXN = auth.get("txnRef")
bad = base64.b64encode(b"NOPE-dev-face-sample").decode()
r = c.post(f"{a.progres}/api/v1/progres/authenticate", headers={**officer, "Idempotency-Key": f"auth-bad-{RUN}"},
           json={"progresId": "UGA-00419999", "modality": "FACE", "biometric": {"format": "dev-sample", "data": bad}, "context": ctx})
check("a non-matching capture is answered NO", r.status_code == 200 and r.json().get("authenticated") is False, r.text[:200])

# ---------------------------------------------------------------- 5
screen(5, "Duplicate assistance check (double dipping)")


def event(partner, pid, rec, when, key):
    return c.post(f"{a.dedup}/v1/assistance-events", headers=gh, json={
        "idempotency_key": key, "progres_id": pid, "assistance_type": ATYPE, "cycle": CYCLE, "partner_id": partner,
        "record_id": rec, "recorded_at": when.isoformat(), "amount": AMOUNT, "currency": CUR, "offline": False})


# Background in the demo: West Nile Relief Partners already gave cash-for-food on 02 Mar 2026.
WN_DUPES = [(NEXT_IN_QUEUE, "DST-WNRP-0302-0188")] + [(f"UGA-0041991{i}", f"DST-WNRP-0302-019{i}") for i in range(3)]
for pid, rec in WN_DUPES:
    r = event(WNRP, pid, rec + (f"-{SFX}" if SFX else ""), datetime(2026, 3, 2, 10, 0, tzinfo=EAT), f"{RUN}-wn-{pid}")
    check(f"background: WNRP recorded {pid} on 02 Mar ({rec})", r.status_code in (200, 201), r.text[:150])
r = c.post(f"{a.dedup}/v1/duplicate-checks", headers=gh, json={
    "progres_id": MARY, "assistance_type": ATYPE, "cycle": CYCLE, "partner_id": NHU, "open_referral": False})
check("Mary: no duplicate found", r.status_code == 200 and r.json().get("duplicate") is False, r.text[:200])
r = c.post(f"{a.dedup}/v1/duplicate-checks", headers=gh, json={
    "progres_id": NEXT_IN_QUEUE, "assistance_type": ATYPE, "cycle": CYCLE, "partner_id": NHU})
j = r.json() if r.status_code == 200 else {}
check(f"{NEXT_IN_QUEUE}: duplicate flagged", j.get("duplicate") is True, r.text[:200])
ex = j.get("existing") or {}
check("... by West Nile Relief Partners, ref DST-WNRP-0302-0188",
      ex.get("partner_id") == WNRP and str(ex.get("record_id", "")).upper().startswith("DST-WNRP-0302-0188"), json.dumps(ex)[:200])
check("... and a referral was opened for the settlement commandant", bool(j.get("referral_id")))
note("'UGX 150,000 duplicate avoided' and the 'advised not to issue' wording are presentation; the Guard returns duplicate + existing record + referral.")

# ---------------------------------------------------------------- 6
screen(6, "Handover recorded offline and synced (214 records, 3 duplicates)")
start = datetime(2026, 3, 5, 8, 0, tzinfo=EAT)
recs = []
for i in range(1, 215):
    if i <= 3:
        pid = WN_DUPES[i][0]                     # three households WNRP already assisted
    elif i == 214:
        pid = MARY                               # Mary's handover is DST-NHU-0305-00214
    else:
        pid = f"UGA-0050{i:04d}"
    recs.append({"clientRecordId": f"DST-NHU-0305-{i:05d}{('-' + SFX) if SFX else ''}", "progresId": pid, "assistanceType": ATYPE,
                 "cycle": CYCLE, "recordId": f"DST-NHU-0305-{i:05d}{('-' + SFX) if SFX else ''}",
                 "capturedAt": (start + timedelta(minutes=(i - 1) * 640 // 213) if i < 214 else datetime(2026, 3, 5, 11, 42, tzinfo=EAT)).isoformat(),
                 "amount": AMOUNT, "currency": CUR, **({"authTxnRef": TXN} if i == 214 and TXN else {})})
batch = {"clientBatchId": f"NHU-0305-{RUN}", "deviceId": "tablet-zone3-pointA", "records": recs}
hb = token(E["RPCMS_SYNC_JWT_KEY"], "emmanuel.taban", partner_id=NHU)
r = c.post(f"{a.sync}/api/v1/sync/batches", headers=hb, json=batch)
check("214 records accepted in one batch after reconnecting", r.status_code == 202, f"{r.status_code} {r.text[:200]}")
deadline, counts = time.time() + a.wait, {}
while time.time() < deadline:
    s = c.get(f"{a.sync}/api/v1/sync/summary", headers=hb).json()
    if s.get("PENDING", 1) == 0 and s.get("total", 0) >= 214:
        break
    time.sleep(3)
print("    sync summary:", json.dumps(s))
check("nothing left waiting to sync", s.get("PENDING") == 0, json.dumps(s))
check("3 flagged as duplicates (CONFLICT)", s.get("CONFLICT") == 3, json.dumps(s))
check("211 recorded (SYNCED)", s.get("SYNCED") == 211, json.dumps(s))
note("The demo shows 'UGX 32,100,000 recorded' (= 214 x 150,000) and 'Flagged as duplicates: 3'. Here the 3 flagged are part of the 214, so "
     "recorded value is 211 x 150,000 = UGX 31,650,000; the demo's wording is ambiguous on this. Sync time (14 s) is not asserted.")
r = c.post(f"{a.dedup}/v1/duplicate-checks", headers=gh, json={
    "progres_id": MARY, "assistance_type": ATYPE, "cycle": CYCLE, "partner_id": WNRP, "open_referral": False})
check("Mary's handover is now visible to other partners (recorded once)", r.status_code == 200 and r.json().get("duplicate") is True, r.text[:200])

# ---------------------------------------------------------------- 7
screen(7, "Online refugee services: ID-renewal reminder")
r = c.post(f"{a.progres}/api/v1/progres/authenticate", headers={**token(E["RPCMS_PROGRES_JWT_KEY"], "clerk-bidibidi", partner_id="OPM"), "Idempotency-Key": f"svc-mary-{RUN}"},
           json={"progresId": MARY, "modality": "FACE", "biometric": {"format": "dev-sample", "data": bio},
                 "context": {"deviceId": "service-centre-pc", "officerId": "clerk-bidibidi", "partnerId": "OPM",
                             "capturedAt": datetime(2026, 9, 22, 9, 0, tzinfo=EAT).isoformat()}})
check("service-centre authentication of Mary: YES", r.status_code == 200 and r.json().get("authenticated") is True, r.text[:200])
print("    NOT COVERED: expiry flag (ID valid to 31 Oct 2026), appointment booking, SMS to Mary, request REQ-BDB-2026-11842 timeline.")
print("                 No repository contains this; ProGres renewal itself is outside RPCMS by design.")

# ---------------------------------------------------------------- 8
screen(8, "Self-reliance survey")
print("    NOT COVERED: offline survey (SR-R2), self-reliance index 31 -> 54, NDP IV indicator calculation. No repository contains this.")

# ---------------------------------------------------------------- 9
screen(9, "National dashboard, expenditure to AMP, indicators to NIMES")
note("Dashboard figures (146 partners, 412,580 events, 3,904 duplicates, UGX 585M avoided, coverage bars) are illustrative in the demo; "
     "no national dashboard exists in these repos. Only the two submissions below are real calls.")
ch = token(E["RPCMS_COMPLIANCE_JWT_KEY"], "officer-nhu", partner_id=NHU)
spent = 204_000_000  # my derivation: 68% of the UGX 300,000,000 Q1 disbursement; the demo gives only "68%"
r = c.post(f"{a.compliance}/api/v1/amp/expenditure-reports", headers={**ch, "Idempotency-Key": f"amp-{RUN}"}, json={
    "reportId": f"NHU-2026Q3{('-' + SFX) if SFX else ''}", "period": "2026-Q3",
    "lines": [{"ampRecordId": AMP_ID, "partnerId": NHU, "amount": spent, "currency": CUR}]})
check("expenditure report queued for AMP", r.status_code in (200, 202), r.text[:200])
amp_id = r.json().get("submissionId") or r.json().get("id") or r.json().get("reportId")
r2 = c.post(f"{a.compliance}/api/v1/nimes/indicator-submissions", headers={**ch, "Idempotency-Key": f"nimes-{RUN}"}, json={
    "submissionRef": f"NIMES-RPM-2026Q3{('-' + SFX) if SFX else ''}", "period": "2026-Q3", "indicators": [
        {"code": "HH_WITH_INCOME_SOURCE_ZONE3", "value": 47, "unit": "percent"},
        {"code": "ACCESS_INTEGRATED_SERVICES", "value": 71, "unit": "percent"},
        {"code": "HOST_HH_JOINT_INTERVENTIONS", "value": 1240, "unit": "households"}]})
check("quarterly indicators queued for NIMES", r2.status_code in (200, 202), r2.text[:200])
note("Indicator codes are invented (NIMES codes are unknown); the three values are the demo's. The demo says 18 indicators; it shows three.")
for label, rr in (("AMP", r), ("NIMES", r2)):
    sid = (rr.json() or {}).get("submissionId") or (rr.json() or {}).get("id")
    st = None
    for _ in range(a.wait // 3):
        g = c.get(f"{a.compliance}/api/v1/submissions/{sid}", headers=ch)
        st = g.json().get("status") if g.status_code == 200 else None
        if st in ("ACCEPTED", "REJECTED", "FAILED"):
            break
        time.sleep(3)
    check(f"{label} submission ACCEPTED (needs rpcms-compliance-worker)", st == "ACCEPTED", f"status={st}")

# ---------------------------------------------------------------- 10
screen(10, "Outcome: traceable end to end")
print("    Partner verified (URSB + NGO Bureau) -> MoU signed (UGPass) -> refugee authenticated (ProGres, Yes/No only)")
print("    -> assistance recorded once, per record -> reported to AMP and NIMES.")
print("\nRESULT:", "ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
