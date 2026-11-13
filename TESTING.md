# Running this addon against a real Odoo database

This is how the addon was verified on 2026-11-13 (Linux sandbox, PostgreSQL 16, Python 3.11, no Docker).
Result: the addon installs cleanly on top of `g2p_payment_g2p_connect` and its 8 Odoo tests pass
(`0 failed, 0 error(s) of 8 tests`). Re-introducing the hardcoded `"SEP"` makes 2 of them fail.

## Environment
- **Odoo 17.0 nightly** `17.0-20261021` from `nightly.odoo.com` (source tarball), Python 3.11 venv.
- **OpenG2P addons** (shallow clones): `openg2p-program` @ `8a44dd5` (17.0-develop), `openg2p-registry` @ `4d3568e` (**branch `17.0-develop`**, not `develop`).
- **Third-party addons** needed by that dependency tree (about 60 modules in total are installed):
  `OCA/queue` 17.0 @ `983b6ad`; **`OpenG2P/rest-framework` 17.0 @ `b708028`** (OCA's own 17.0 marks `extendable_fastapi` not installable, so OpenG2P's fork is required); `OpenG2P/odoo-formio` 17.0 @ `b791952`; **`OpenG2P/storage` 17.0 @ `41bdacf`** (provides `storage_file` / `storage_backend_s3`, absent from OCA/storage 17.0); OCA wheels from PyPI for `component`, `endpoint_route_handler`, `password_security`, `server_environment`.
- `scripts/fetch_deps.py` resolves the OCA wheels; `scripts/run_odoo.sh` is the launcher (edit its paths).

## Python deviations from the upstream pins (needed to make it start)
Upstream's `requirements.txt` pins `cryptography>36,<37` and `boto3<=1.15.18`; these conflict with Odoo 17's own requirements on this setup, so they were installed **unpinned**
(cryptography 50.0.2, boto3 1.44.2) and these were upgraded to match: `pyOpenSSL` 26.4.0, `urllib3` 2.8.0. `fastapi` had to be **pinned to 0.110.3** (the OCA `fastapi` module uses `Query(gte=...)`, rejected by newer releases). Test-only extras: `httpx`, `vcrpy-unittest`. `pip check` reports only that `fastapi-openapi-overrides` wants a newer fastapi; nothing in the install path used it.

## Commands
```
createdb rpcms_test
scripts/run_odoo.sh -d rpcms_test -i g2p_payment_g2p_connect --stop-after-init   # ~90 s
scripts/run_odoo.sh -d rpcms_test -i rpcms_pbms_bridge
scripts/run_odoo.sh -d rpcms_test -u rpcms_pbms_bridge --test-enable --test-tags /rpcms_pbms_bridge --log-level=test
```
Note: upstream `g2p_payment_g2p_connect/tests/__init__.py` has its test import commented out, so there is no upstream suite to run.
