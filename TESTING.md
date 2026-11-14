# Running this addon against a real Odoo database

This is how the addon was verified on 2026-11-13 (Linux sandbox, PostgreSQL 16, Python 3.11, no Docker).
Result: the addon installs cleanly on top of `g2p_payment_g2p_connect` and its 22 Odoo tests pass
(`0 failed, 0 error(s) of 22 tests`: 8 for ProGres mode, 14 for the Bridge Partner API mode). Re-introducing the hardcoded `"SEP"` makes tests fail in both groups; signing unsorted JSON, marking rejected payments as sent, and auto-resending after an unknown outcome were each tried and each makes at least one test fail.

The pure tests (`pytest tests`) also run without Odoo. `tests/test_bridge_api_contract.py` additionally needs the Bridge models package (`pip install -e <g2p-bridge>/core/models "sqlalchemy[asyncio]"` in a separate venv) and is skipped without it.

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


## Rebuilding the environment on another machine (tested 2026-11-14)
`scripts/odoo17_tested_requirements.txt` is the exact package set (117 packages, Python 3.11) that passed the 22 tests in a **fresh** virtual environment. It is a `pip freeze` of the working environment, minus `fastapi-openapi-overrides` (it demands a newer fastapi than the 0.110.3 the OCA `fastapi` module needs; nothing here uses it) and minus Odoo itself.

```bash
uv venv ~/odoo/venv --python 3.11 && . ~/odoo/venv/bin/activate
uv pip install -r scripts/odoo17_tested_requirements.txt
# OCA modules that are not in the cloned repos (versions that were tested):
uv pip install --no-deps --target /tmp/oca "odoo-addon-component==17.0.1.0.1" "odoo-addon-endpoint_route_handler==17.0.1.0.3" "odoo-addon-password_security==17.0.2.0.2" "odoo-addon-server_environment==17.0.1.2.6"
mkdir -p ~/odoo/extra && cp -r /tmp/oca/odoo/addons/* ~/odoo/extra/
```
Add `~/odoo/extra` to `--addons-path`. `psycopg2` builds from source, so `libpq-dev` and a compiler must be installed.

Checked in two environments:
1. **Sandbox (2026-11-14):** Odoo 17.0 nightly source package run with `python -m odoo`, PostgreSQL 16, Python 3.11. 22 of 22 tests pass.
2. **Second machine, run by the user (2026-11-14, output pasted to me, not run by me):** Ubuntu 20.04.2, **PostgreSQL 12.22** in its own cluster on port 5433, Odoo from a `git clone --depth 1 --branch 17.0 odoo/odoo` started with `odoo-bin`, Python 3.11 from `uv`. Result reported: `0 failed, 0 error(s) of 22 tests`.

**With a git clone of Odoo, `--addons-path` must include `odoo/addons` (the clone's own `addons/` folder), in addition to the OpenG2P and OCA folders.** `--addons-path` replaces the default, and without it the install fails with `No module named 'odoo.addons.web'` and a `ParseError` in `base/data/ir_module_module.xml`. The nightly package does not need this because all modules sit in one folder.

Still untested: Python versions other than 3.11, other Linux distributions, Odoo releases other than the two above, and PostgreSQL 13-15.
