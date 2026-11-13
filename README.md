# rpcms-pbms-bridge

Odoo 17 addon (`rpcms_pbms_bridge`) that makes OpenG2P PBMS send what RPCMS's Duplicate Guard expects
through the G2P Bridge. Depends on `g2p_payment_g2p_connect` from
[openg2p-program](https://github.com/OpenG2P/openg2p-program) (checked against 17.0-develop at `8a44dd5`).

## What PBMS already does, and what this changes

| Bridge field | Upstream PBMS behaviour | With RPCMS ProGres mode |
|---|---|---|
| `beneficiary_id` | `_get_payee_fa`: with *Payee ID Field = Registrant ID* it sends the registrant's ID value of the chosen ID type (for a group, the household head's) | **Unchanged code.** Configuration only: a `UNHCR ProGres ID` ID type is installed and the manager must use it with no prefix/suffix (enforced by a constraint) |
| `benefit_program_mnemonic` | `"<program name> #<manager id>"` | Unchanged; this is the assistance type |
| `cycle_code_mnemonic` | **hardcoded `"SEP"`** (a TODO upstream) | `<SLUG-OF-CYCLE-NAME>-C<cycle id>`, e.g. `NOV-C12` |

The `"SEP"` hardcode matters: with it, every cycle shares one code, so the Duplicate Guard treats a person's
next-cycle payment as a duplicate of the first. `tests/test_duplicate_guard_interaction.py` demonstrates this
against the real `rpcms-dedup` service (skipped if it is not installed).

## Use
1. Install the addon; it creates the ID type `UNHCR ProGres ID`.
2. Record each registrant's ProGres ID under that ID type.
3. On the G2P Connect payment manager: Payee ID Field = Registrant ID, ID Type = UNHCR ProGres ID, empty prefix/suffix, tick **RPCMS ProGres mode**.
4. Register the record-only connector in the Bridge (see rpcms-bridge-connectors).

## Tests
Two suites. Odoo-free rules (`rpcms_pbms_bridge/mapping.py`) plus the Duplicate Guard interaction: `pip install pytest` (and `rpcms-dedup` for the interaction test), then `pytest`. Odoo model, constraint and envelope behaviour: see [TESTING.md](TESTING.md).

## Bridge request-shape compatibility (checked, unresolved)
`scripts/check_bridge_contract.py` builds the create-envelope and create-disbursements requests the way
`g2p_payment_g2p_connect` does (envelope `message` from this addon's mapping) and validates them against the G2P Bridge's
own pydantic request schemas. Result against the Bridge `develop` clone I had (a shallow single-commit clone dated
2026-08-28; I could not see its history or release tags): **both requests are rejected**.
- The Bridge schemas expect `request_header` / `request_body.request_payload[...]`; the addon sends `signature` / `header` / `message`.
- Field names differ, for example `total_disbursement_amount` and `disbursement_currency_code` (envelope) and `disbursement_amount` and `mis_reference_number` (disbursement) have no field of the same name in the Bridge models.
- The addon's response parsing (`response["message"][...]`) does not match the Bridge's `response_body.response_payload` either (read from the schemas, not exercised).
- I checked the `payment_manager.py` of PBMS branches `17.0-develop`, `17.0-1.2` and `17.0-1.3`: all send the `message` shape. So the gap is not a branch I forgot to switch to.
- The Bridge also verifies a detached JWS over canonical (sorted-key) JSON in its sanity-suite signing helper; the addon signs `json.dumps(..., separators=(",", ":"))` without key sorting through the Keymanager. Whether the deployed Bridge enforces that is unconfirmed.

What this does **not** show: which Bridge release your environment runs (an older Bridge may still accept this shape), whether a compatibility layer sits in front of it, or that the Bridge cannot be configured otherwise. It checks request shape only; nothing was sent to a running Bridge. Until this is resolved, treat the end-to-end "PBMS → Bridge" path as unverified, including everything this addon claims about what reaches the Bridge.

## Known limits (read before relying on this)
- **Verified on a real Odoo 17 database, with limits.** The addon installs on top of `g2p_payment_g2p_connect` (about 60 modules) and its 8 Odoo tests pass; see [TESTING.md](TESTING.md) for the exact environment and the dependency-version deviations needed to get it running. Not exercised: the real JWT/encryption provider (`create_jwt_token` is mocked), a real Bridge (`requests.post` is mocked), the full cycle flow (`prepare_payments`, batches, `_send_payments`), and the form view in a browser (it only loads and inherits without error).
- `_create_envelope_g2p_bridge` is a **copy** of the upstream method with a changed message, so it can drift from upstream; the clean fix is an upstream change replacing the `"SEP"` hardcode. One deliberate difference: the copy calls `raise_for_status()` before reading the response body.
- Existing upstream behaviour kept: a payment whose registrant has no ProGres ID is silently marked `failed`; for groups only the household head's ID is used. Decide whether ProGres IDs are per head or per member before go-live.
- The view inherits `g2p_payment_g2p_connect.view_payment_manager_g2p_connect_form` after the `locale` field; it loads cleanly but was not rendered in a UI.
- The ProGres ID format is unknown, so it is only trimmed and upper-cased (same as the Duplicate Guard), not validated.
- Changing a cycle's name after its envelope exists does not change its code; the code is fixed at envelope creation.
