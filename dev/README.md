# Development-only tools (not for production)

Two things live here so the whole RPCMS flow can run on a development machine when no real G2P Bridge or Keymanager is available. **Neither is the real thing and neither is secure.**

| Tool | Where | What it is |
| --- | --- | --- |
| Mock Bridge | `dev/mock_bridge/mock_bridge.py` | A small FastAPI service that answers the three Bridge Partner API calls using rules read from the Bridge source (one shallow clone dated 2026-08-28). Keeps state in a JSON file. Lets you "settle" payments by hand. |
| Dev signer | `rpcms_dev_signer/` (an Odoo addon) | An encryption-provider type that signs with an RSA key generated on first use and stored **unprotected** in the database. Replaces the Keymanager provider in dev. |
| Demo flow | `dev/demo_flow.py` | Runs one payment flow from Odoo to the mock over real HTTP with real signing, and prints PASS/FAIL per step. |

## What this does and does not prove
It proves this addon builds, signs, sends and reads the Bridge request shapes consistently, and that Odoo copies the mock's remittance data back. It does **not** prove anything about the real Bridge: the mock's behaviour comes from reading source, the real Bridge was never run, and it does not know the real Bridge's SPAR/ID mapping, bank, funds checks, or which benefit program / code ids exist. The mock answers a resent `disbursement_id` with `DUPLICATE_DISBURSEMENT`; that is unconfirmed for the real Bridge.

## Run it
1. Install the dev signer: `-i rpcms_dev_signer` (needs `g2p_encryption`, which comes with the other modules).
2. Export its public key so the mock can verify signatures (optional but recommended). In an Odoo shell:
   `open("/tmp/dev_signer_pub.pem","w").write(env.ref("rpcms_dev_signer.provider_dev_signer").rpcms_dev_public_key_pem()); env.cr.commit()`
3. Start the mock (needs `fastapi`, `uvicorn`, `cryptography`):
   `cd dev/mock_bridge && MOCK_BRIDGE_PUBLIC_KEY_FILE=/tmp/dev_signer_pub.pem uvicorn --factory mock_bridge:create_app --host 127.0.0.1 --port 8010`
4. In Odoo, on the payment manager: set **RPCMS Bridge API** to *Bridge Partner API*, fill the extra fields, set **Encryption provider** to *RPCMS dev signer*, and point the three URLs at `http://127.0.0.1:8010/create_disbursement_envelopes`, `/create_disbursements`, `/get_disbursement_status`.
5. Settle accepted payments so the status check has data: `curl -X POST http://127.0.0.1:8010/dev/settle` (add `?reverse=true` with a JSON list of ids to simulate a reversal). Inspect everything at `GET /dev/state`; wipe with `POST /dev/reset`.
6. Or run everything at once: `odoo shell -d <db> ... < dev/demo_flow.py` (creates records named "RPCMS DEV DEMO ..." and commits them; development databases only).

## Tests
- Mock: `pytest dev/mock_bridge/tests` (8 tests; the last needs the real Bridge models installed, see `TESTING.md`, otherwise it is skipped).
- Dev signer: Odoo tests, `--test-tags /rpcms_dev_signer` (4 tests).
- Verified 2026-11-14 in the sandbox: demo flow all steps PASS; with a mock configured with the wrong public key the same flow fails at the first call.
- Second machine (user-reported 2026-11-14, output pasted to me, not run by me): Ubuntu 20.04, PostgreSQL 12, git-cloned Odoo 17.0, Python 3.11. Dev signer installed with no errors; demo flow: 8 of 8 steps PASS, with the mock checking signatures against the dev signer's public key.

## Use case walkthrough

`usecase_demo.py` plays the stakeholder demo screen by screen against the dev services. `seed_usecase.py` and
`usecase_scenario.py` are an optional payment-side rehearsal. See [USECASE.md](USECASE.md).
