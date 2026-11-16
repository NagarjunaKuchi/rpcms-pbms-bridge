# RPCMS use case walkthrough (development only, invented data)

The use case is the stakeholder demo `RPCMS-Refugee-Coordination-Demo_2_2.html`: one partner (Nile Hope Uganda) and one refugee
(Mary Poni, ProGres ID `UGA-00423318`) through a season in Bidibidi. **RPCMS records and tracks assistance; it does not make
payments** (the demo says so), so Odoo and the payment bridge are not part of it.

## Run it

```
source ~/rpcms/secrets.sh
~/rpcms/venv/bin/python dev/usecase_demo.py [--suffix r1]
```
Needs the five services, `rpcms-sync-worker` and `rpcms-compliance-worker` running. One-shot per database (use `--suffix` to rehearse).

| Demo screen | What the script does against the real dev services | Covered? |
|---|---|---|
| 0 Scene | narrative only | - |
| 1 Partner compliance | URSB + NGO Bureau + AMP record check for Nile Hope (80020003318742, NGO/PMT/2025/4418) | Yes (mocks). Not covered: permit-area check, open-issue check, 60-day expiry alert |
| 2 UGPass e-MoU | MoU OPM/DoR/NHU/2026/014; 3 signatories in order (Achan, Okello, Mugisha) approved via signed callbacks; 2 e-Seals; public verification; tamper = different hash fails | Workflow yes. Not covered: real ECDSA signatures and QR contents, UGPass certificates |
| 3 Project, AMP, map | reads AMP-UG-2026-00731 | Partly: donor name, Q1 amount, 1,800 households, GIS map, overlap warning are not in any service |
| 4 ProGres v4 auth | Mary UGA-00423318 authenticates YES; response has only the four allowed fields; a non-matching capture is NO | Yes (mock) |
| 5 Double dipping | WNRP's 02 Mar record for UGA-00419907 (DST-WNRP-0302-0188); Mary clear; UGA-00419907 flagged with a referral | Yes |
| 6 Handover and sync | 214 offline records (Mary's is DST-NHU-0305-00214) in one batch; 3 become CONFLICT, 211 SYNCED; Mary then visible to other partners | Yes. Demo wording on value (UGX 32.1M vs 3 duplicates) is ambiguous; see script note |
| 7 Online services / ID renewal | only the ProGres authentication at the service centre | **No** service for expiry flag, appointment, SMS |
| 8 Self-reliance survey | nothing | **No** service |
| 9 Dashboard, AMP, NIMES | expenditure report to AMP and three indicators to NIMES, both reach ACCEPTED | Submissions yes; dashboard figures are the demo's illustration, no dashboard exists. NIMES indicator codes are invented |
| 10 Outcome | summary | - |

Everything upstream (ProGres, UGPass, URSB, NGO Bureau, AMP, NIMES) is a development mock.

---

# Appendix: payment-side rehearsal (NOT part of the demo use case)

`seed_usecase.py` and `usecase_scenario.py` rehearse Odoo/OpenG2P paying households through the PBMS bridge. The demo
says RPCMS does not make payments, so this is an optional extra for the bridge adapter only.

**Story.** Eight refugee households in Bidibidi are identified by a (fake) UNHCR ProGres ID, `UGA-DEV-0001` … `0008`.
Partner **A (WFP-DEV)** pays them cash through OpenG2P (Odoo) and the Bridge. Partner **B (DRC-DEV)** then tries to assist some of
the same households in the same cycle - online and from an offline tablet. The Duplicate Guard must catch this and turn it into
referrals a programme officer resolves.

**What is real and what is not.** Odoo, the PBMS addon, the dev signer, the Duplicate Guard and the offline-sync service are the
real code. The Bridge is the dev mock (`dev/mock_bridge`), which does **not** call the Duplicate Guard - in production the
Bridge connector does that. Step B1 below does it on the connector's behalf with the same values. ProGres, UGPass, AMP, NIMES are
mocks and are not part of this walkthrough. The compliance service is not covered here.

## Part A - the payment side (Odoo + mock Bridge)

On the server, in `~/rpcms-pbms-bridge` (after `git pull`), with the mock Bridge running on 8010:

```
curl -s http://127.0.0.1:8010/healthz
~/odoo/shell.sh < dev/seed_usecase.py          # takes a few minutes; prints [PASS]/[FAIL] lines
```

It creates, through OpenG2P's own code paths: 8 households (a group, a head carrying the ProGres ID, one member), the programme
"Bidibidi Cash <MMDDHHMM>" with an RPCMS payment manager in ProGres mode (Partner API, dev signer, mock Bridge), funding,
enrolment, one cycle, entitlements, approval, payment batch, sending to the mock Bridge, and settlement of 7 of the 8 payments
(the last stays pending on purpose). It writes `~/rpcms/usecase.json` for Part B. Expect `RESULT: ALL CHECKS PASSED`.
`USECASE_STEPS=prepare` stops after the cycle has its beneficiaries, so you can do the workflow by hand in the UI.

**Look at it in the Odoo UI** (labels are taken from the view files; I have not clicked through them): open the programme, then
its cycle. The cycle form's buttons run in this order: *Copy Beneficiaries from Program* → *Prepare Entitlement* → *To Approve* →
*Approve* → *Approve Entitlement* → *Prepare Payments* → *Send Payments*. Check on a head registrant that the ID type is
"UNHCR ProGres ID". In the payment manager, "RPCMS ProGres mode" is ticked. Payments show their disbursement id and
status; the pending one has no remittance statement yet.

**Why the ID is on the head, not the group.** Upstream's payment manager pays a group's *head individual*
(`get_registrant_or_group_head`), so the ProGres ID that goes to the Bridge as `beneficiary_id` is the head's. A group without a head
member has no payee and its payment is silently marked failed. This was found while writing this seed.

**Look at the mock Bridge:** `curl -s http://127.0.0.1:8010/dev/state` shows the envelope and the 8 disbursements; the
`beneficiary_id` of each is its ProGres ID.

## Part B - the duplicate side (Guard + offline sync)

Needs the five services and `rpcms-sync-worker` running (`~/rpcms/start.sh`), then:

```
source ~/rpcms/secrets.sh
python dev/usecase_scenario.py        # use the same Python (venv) you used for scripts/e2e_smoke.py
```

| Step | What happens | Expected |
|---|---|---|
| B1 | Partner A's 8 payments are recorded in the Guard (what the Bridge connector would do) | 8 × recorded |
| B2 | Partner B asks the Guard about `UGA-DEV-0003`, and about a household nobody assisted (`UGA-DEV-0009`) | 0003: duplicate, assisted by WFP-DEV, referral opened; 0009: not a duplicate |
| B3 | Partner B records assistance for `UGA-DEV-0004` online | refused (409), referral |
| B4 | Partner B's offline tablet syncs a batch: 0009 (new), 0005 and 0006 (already assisted); then sends the same batch again | batch 202, resend 200; 0009 SYNCED, 0005/0006 CONFLICT |
| B5 | A programme officer lists OPEN referrals and resolves one as CONFIRMED_DUPLICATE and one as FALSE_POSITIVE | referrals for this programme ≥ 4 |

Run Part B **once per seed run**. A second run finds `UGA-DEV-0009` already taken and reports FAIL - that is the Guard working.
To start over, run the seed again (new programme name, new assistance type).

Explore by hand: `curl -s -H "X-API-Key: $RPCMS_DEDUP_API_KEY" "http://127.0.0.1:8001/v1/referrals?status=OPEN"`.
The Guard upper-cases codes, so assistance types appear in capitals there.

## Verified how

Both scripts were run end to end in a sandbox (fresh Odoo 17 DB with the addons, mock Bridge, Guard, sync API and worker with SQLite)
with all checks passing. They have not yet been run on your server. Differences that could appear there: different OpenG2P
checkout, `UGX` currency handling, and the real service environment.
