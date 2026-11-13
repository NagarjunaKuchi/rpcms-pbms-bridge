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
`pip install pytest` (plus `rpcms-dedup` and its test deps for the interaction test) then `pytest`.
Covers the Odoo-free rules in `rpcms_pbms_bridge/mapping.py`.

## Known limits (read before relying on this)
- **The Odoo code has NOT been run.** No Odoo 17 environment was available; the model, view and data files are only syntax-checked (Python compile, XML well-formed). Install it on a test database and run the manager's envelope creation before trusting it. There are no Odoo tests yet.
- `_create_envelope_g2p_bridge` is a **copy** of the upstream method with a changed message, so it can drift from upstream; the clean fix is an upstream change replacing the `"SEP"` hardcode. One deliberate difference: the copy calls `raise_for_status()` before reading the response body.
- Existing upstream behaviour kept: a payment whose registrant has no ProGres ID is silently marked `failed`; for groups only the household head's ID is used. Decide whether ProGres IDs are per head or per member before go-live.
- The view inherits `g2p_payment_g2p_connect.view_payment_manager_g2p_connect_form` after the `locale` field; the `g2p.id.type` record uses only a `name` (as upstream tests do). Neither was run.
- The ProGres ID format is unknown, so it is only trimmed and upper-cased (same as the Duplicate Guard), not validated.
- Changing a cycle's name after its envelope exists does not change its code; the code is fixed at envelope creation.
