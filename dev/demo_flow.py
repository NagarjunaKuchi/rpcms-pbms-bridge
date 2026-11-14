"""DEVELOPMENT ONLY. Runs one payment flow through Odoo and the dev mock Bridge, over real HTTP with real signing.

Needs: rpcms_pbms_bridge and rpcms_dev_signer installed, and the mock Bridge running (dev/mock_bridge). Run inside Odoo:

    odoo shell -d <db> <addons options> < dev/demo_flow.py          (or: ~/odoo/shell.sh < dev/demo_flow.py)

Environment: MOCK_BRIDGE_URL (default http://127.0.0.1:8010). It CREATES demo records named "RPCMS DEV DEMO ..." in
the database (a program, a cycle, two registrants, entitlements, payments, a batch, a payment manager) every time it
runs, and commits them. Use a development database only.
"""
import os
import time
from datetime import timedelta

import requests

from odoo import fields

base = os.environ.get("MOCK_BRIDGE_URL", "http://127.0.0.1:8010").rstrip("/")
tag = time.strftime("%H%M%S")
ok = True


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not cond else ""))


print("mock bridge:", requests.get(base + "/healthz", timeout=5).json())
before = len(requests.get(base + "/dev/state", timeout=5).json()["disbursements"])
provider = env.ref("rpcms_dev_signer.provider_dev_signer")  # noqa: F821  (env is provided by odoo shell)
program = env["g2p.program"].create({"name": f"RPCMS DEV DEMO program {tag}"})  # noqa: F821
manager = env["g2p.program.payment.manager.g2p.connect"].create({  # noqa: F821
    "name": f"RPCMS DEV DEMO manager {tag}", "program_id": program.id, "create_batch": True, "locale": "en_US",
    "payment_endpoint_url": base + "/create_disbursements", "status_endpoint_url": base + "/get_disbursement_status",
    "envelope_creation_url": base + "/create_disbursement_envelopes", "envelope_status_url": base + "/get_disbursement_envelope_status",
    "payee_id_type": "bank_acc_no", "encryption_provider_id": provider.id,
    "rpcms_bridge_api": "partner_api", "sender_id": f"rpcms-dev-{tag}", "rpcms_sender_app_url": "http://localhost:8069",
    "rpcms_benefit_program_id": 1, "rpcms_benefit_code_id": 1, "rpcms_benefit_code_mnemonic": "CASH-UGX",
    "rpcms_target_registry": "PROGRES", "rpcms_disbursement_frequency": "Monthly"})
cycle = env["g2p.cycle"].create({  # noqa: F821
    "name": f"RPCMS DEV DEMO cycle {tag}", "program_id": program.id, "start_date": fields.Datetime.now(),
    "end_date": fields.Datetime.now() + timedelta(days=7)})
payments = env["g2p.payment"]  # noqa: F821
for i in (1, 2):
    partner = env["res.partner"].create({"name": f"DEV DEMO person {i} {tag}", "bank_ids": [(0, 0, {"acc_number": f"ACC{tag}{i}"})]})  # noqa: F821
    ent = env["g2p.entitlement"].create({"program_id": program.id, "partner_id": partner.id, "cycle_id": cycle.id,  # noqa: F821
                                         "state": "draft", "initial_amount": 50000.0})
    payments |= env["g2p.payment"].create({"name": f"DEV DEMO pay {i}", "amount_issued": 50000.0, "cycle_id": cycle.id,  # noqa: F821
                                           "state": "issued", "entitlement_id": ent.id,
                                           "currency_id": env.ref("base.USD").id})  # noqa: F821
batch = env["g2p.payment.batch"].create({"program_id": program.id, "cycle_id": cycle.id, "payment_ids": [(6, 0, payments.ids)]})  # noqa: F821

check("envelope created at the mock", manager._create_envelope_g2p_bridge(cycle))
check("cycle stored the envelope id", bool(cycle.disbursement_envelope_id), cycle.disbursement_envelope_id)
manager._send_payments(batch)
payments.invalidate_recordset()
check("both payments marked sent", set(payments.mapped("dispatch_status")) == {"sent"}, str(payments.mapped("dispatch_status")))
check("payments carry a disbursement id", all(payments.mapped("disbursement_id")))
state = requests.get(base + "/dev/state", timeout=5).json()
check("mock holds the two disbursements", len(state["disbursements"]) == before + 2)
manager.payments_status_check(manager.id)
payments.invalidate_recordset()
check("before settling, no remittance yet", not any(payments.mapped("remittance_statement_id")))
requests.post(base + "/dev/settle", json=payments.mapped("disbursement_id"), timeout=5)
manager.payments_status_check(manager.id)
payments.invalidate_recordset()
check("after settling, remittance copied into Odoo", all(payments.mapped("remittance_statement_id")),
      str(payments.mapped("remittance_statement_id")))
manager._send_payments(batch)  # sending the same batch again must not create duplicates
check("a second send creates no duplicates at the mock",
      len(requests.get(base + "/dev/state", timeout=5).json()["disbursements"]) == before + 2)
print("\nRESULT:", "ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED")
env.cr.commit()  # noqa: F821
