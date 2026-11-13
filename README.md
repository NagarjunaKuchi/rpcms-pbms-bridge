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

## Bridge request-shape compatibility, and the opt-in Partner API mode
**Finding.** `scripts/check_bridge_contract.py` builds the requests `g2p_payment_g2p_connect` sends and validates them against the G2P Bridge's own pydantic schemas. Against the Bridge `develop` clone I had (shallow, single commit dated 2026-08-28, no history or tags), **both requests are rejected**: the Bridge expects `request_header` / `request_body.request_payload[...]`, the addon sends `signature` / `header` / `message`; field names differ (`total_disbursement_amount` vs `total_disbursement_quantity`, `disbursement_amount` vs `disbursement_quantity`, ...); the envelope's frequency `OnDemand` is not a Bridge value; the Bridge requires `benefit_program_id`, `benefit_code_id` and `target_registry`, which the addon has no field for. PBMS branches `17.0-develop`, `17.0-1.2` and `17.0-1.3` all send the old shape. I could not tell which Bridge release you run: an older Bridge may still accept the old shape. **The default (`RPCMS Bridge API` = Legacy) therefore changes nothing.**

**Adapter.** Set **RPCMS Bridge API** to *Bridge Partner API* on the payment manager to send the Bridge's shape. Then fill the extra fields (all required, validated on save): sender ID (existing field; it becomes the Bridge `sender_app_mnemonic`), sender app URL, Bridge benefit program ID, benefit code ID, benefit code mnemonic, target registry, disbursement frequency. Point the manager URLs at the Bridge endpoints: *Envelope creation URL* -> `.../create_disbursement_envelopes`, *Payment endpoint URL* -> `.../create_disbursements`, *Status endpoint URL* -> `.../get_disbursement_status`.
- Envelope: one `CASH_DIGITAL` envelope per cycle, with the per-cycle code from this addon (never `SEP`), `measurement_unit` = the company currency code, `disbursement_cycle_id` = the PBMS cycle id.
- Disbursements: `disbursement_id` = `<sender id>-P<payment id>` (the Bridge enforces uniqueness, so a resend of the same payment is refused rather than paid twice), `disbursement_batch_control_id` = `<sender id>-B<batch id>`, `beneficiary_id` = the payee value the manager already computes (the ProGres ID in ProGres mode).
- Results: per-disbursement `response_error_codes` decide what is marked `sent`. A payment with errors stays `pending` and the codes are posted on the manager's chatter. If the Bridge rejects the whole request, the batch can be sent again; if the outcome is unknown (timeout, 5xx), the batch is **not** re-sent automatically and needs a person to check the Bridge first.
- Status check: asks for `sent` payments in chunks of 200 and copies the remittance/reversal fields. Like upstream it keeps polling a payment until both a remittance and a reversal statement are set, so polling grows with the number of sent payments.
- Signing: the request is signed (detached JWS through the manager's encryption provider) over compact, sorted-key JSON, because that is what the Bridge source rebuilds before verifying. The Bridge only checks it when its `signature_validation_enabled` is true, and looks the key up under `PARTNER_<SENDER ID upper-cased, "-" -> "_">`: that partner must exist in the Bridge's key service.

**What is verified, and what is not.**
- Verified: the request builders against the Bridge's schemas (`tests/test_bridge_api_contract.py`, needs the Bridge models package installed; skipped otherwise), the response parsers against responses built by the Bridge's own response models, the signing text against `orjson`, and 14 Odoo tests of the wiring on a real Odoo 17 database (`TESTING.md`), including a real RS256 signature check and four deliberate breakages that the tests catch.
- **Not verified:** any of this against a running Bridge. The Odoo tests use a **fake Bridge written by me from reading the Bridge source** (validation rules, signature rule); it is not the Bridge. Not confirmed: that the real Bridge answers a resent disbursement with `DUPLICATE_DISBURSEMENT` (the code exists in its enum); that the `beneficiary_id` format matches the ID-to-account links held in the Bridge's SPAR (the Bridge sanity suite seeds those first); how `benefit_program_id` / `benefit_code_id` / `target_registry` are meant to be created and what values are valid; that the status-check payload fields map as I assumed; and the encryption provider used in production (only the Keymanager one exists locally and it needs the MOSIP Keymanager service, which I could not run).
- Not implemented: envelope or disbursement cancel/amend, the envelope-status endpoint, non-cash benefit types. `amount_paid` is set when the Bridge accepts a disbursement (as upstream does at send time), which is acceptance, not confirmation that money moved: use the remittance fields for that.

## Known limits (read before relying on this)
- **Verified on a real Odoo 17 database, with limits.** The addon installs on top of `g2p_payment_g2p_connect` (about 60 modules) and its 8 Odoo tests pass; see [TESTING.md](TESTING.md) for the exact environment and the dependency-version deviations needed to get it running. Not exercised: the real JWT/encryption provider (`create_jwt_token` is mocked), a real Bridge (`requests.post` is mocked), the full cycle flow (`prepare_payments`, batches, `_send_payments`), and the form view in a browser (it only loads and inherits without error).
- `_create_envelope_g2p_bridge` is a **copy** of the upstream method with a changed message, so it can drift from upstream; the clean fix is an upstream change replacing the `"SEP"` hardcode. One deliberate difference: the copy calls `raise_for_status()` before reading the response body.
- Existing upstream behaviour kept: a payment whose registrant has no ProGres ID is silently marked `failed`; for groups only the household head's ID is used. Decide whether ProGres IDs are per head or per member before go-live.
- The view inherits `g2p_payment_g2p_connect.view_payment_manager_g2p_connect_form` after the `locale` field; it loads cleanly but was not rendered in a UI.
- The ProGres ID format is unknown, so it is only trimmed and upper-cased (same as the Duplicate Guard), not validated.
- Changing a cycle's name after its envelope exists does not change its code; the code is fixed at envelope creation.
