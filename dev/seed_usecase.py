"""DEVELOPMENT ONLY. Seeds the RPCMS use case in Odoo: synthetic refugee households identified by a (fake) ProGres ID, an
RPCMS cash programme wired to the dev mock Bridge, one cycle, and (by default) the full cycle workflow up to a settled payment.

Run inside Odoo (needs rpcms_pbms_bridge + rpcms_dev_signer installed and the mock Bridge running on MOCK_BRIDGE_URL):

    ~/odoo/shell.sh < dev/seed_usecase.py

Environment: MOCK_BRIDGE_URL (default http://127.0.0.1:8010); USECASE_OUT (default ~/rpcms/usecase.json) - a JSON file the HTTP
scenario script (dev/usecase_scenario.py) reads; USECASE_STEPS = "prepare" (stop after enrolment + cycle with beneficiaries)
or "all" (default: entitlements, approval, payments, send, settle).

All people are INVENTED. Never put real people's data into this development setup. Creates records every run and commits.
"""
import json
import os
import time
from datetime import timedelta

import requests

from odoo import fields
from odoo.addons.rpcms_pbms_bridge import mapping

base = os.environ.get("MOCK_BRIDGE_URL", "http://127.0.0.1:8010").rstrip("/")
out_path = os.path.expanduser(os.environ.get("USECASE_OUT", "~/rpcms/usecase.json"))
steps = os.environ.get("USECASE_STEPS", "all")
tag = time.strftime("%m%d%H%M")
ok = True


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not cond else ""))


# Invented households: (ProGres ID, household name, size, zone)
HOUSEHOLDS = [
    ("UGA-DEV-0001", "Household Akello", 5, "Zone 1"), ("UGA-DEV-0002", "Household Nyaka", 7, "Zone 1"),
    ("UGA-DEV-0003", "Household Lokwii", 4, "Zone 2"), ("UGA-DEV-0004", "Household Duku", 6, "Zone 2"),
    ("UGA-DEV-0005", "Household Ayoo", 3, "Zone 3"), ("UGA-DEV-0006", "Household Mawa", 8, "Zone 3"),
    ("UGA-DEV-0007", "Household Opio", 5, "Zone 4"), ("UGA-DEV-0008", "Household Tabu", 2, "Zone 4"),
]
AMOUNT_PER_HOUSEHOLD = 150000.0  # UGX, invented

print("mock bridge:", requests.get(base + "/healthz", timeout=5).json())
progres = env.ref("rpcms_pbms_bridge.id_type_unhcr_progres")  # noqa: F821  (env is provided by odoo shell)
provider = env.ref("rpcms_dev_signer.provider_dev_signer")  # noqa: F821
currency = env["res.currency"].with_context(active_test=False).search([("name", "=", "UGX")], limit=1)  # noqa: F821
if currency:
    currency.active = True  # Uganda shilling ships with Odoo but is inactive by default
else:
    currency = env.company.currency_id  # noqa: F821

# 1. Households (groups). Upstream's payment manager pays the group HEAD, so the ProGres ID sits on the head (the
# ProGres principal applicant) - not on the group. Each household also gets one member without an ID.
head_kind = env.ref("g2p_registry_membership.group_membership_kind_head")  # noqa: F821
partners = []
for pid, name, size, zone in HOUSEHOLDS:
    group = env["res.partner"].create({  # noqa: F821
        "name": f"{name} ({tag})", "is_registrant": True, "is_group": True, "street": f"Bidibidi {zone} (dev data)"})
    head = env["res.partner"].create({  # noqa: F821
        "name": f"{name.replace('Household ', '')} (head, {tag})", "is_registrant": True, "is_group": False,
        "reg_ids": [(0, 0, {"id_type": progres.id, "value": pid})]})
    member = env["res.partner"].create({"name": f"{name.replace('Household ', '')} (member, {tag})",  # noqa: F821
                                        "is_registrant": True, "is_group": False})
    env["g2p.group.membership"].create({"group": group.id, "individual": head.id, "kind": [(4, head_kind.id)]})  # noqa: F821
    env["g2p.group.membership"].create({"group": group.id, "individual": member.id})  # noqa: F821
    partners.append(group)
check("households created, heads carry ProGres IDs", len(partners) == len(HOUSEHOLDS))

# 2. Programme through OpenG2P's own wizard (creates the default managers), then our payment manager
wiz = env["g2p.program.create.wizard"].create({  # noqa: F821
    "name": f"Bidibidi Cash {tag}", "currency_id": currency.id, "target_type": "group",
    "amount_per_cycle": AMOUNT_PER_HOUSEHOLD, "amount_per_individual_in_group": 0.0,
    "approver_group_id": env.ref("base.group_system").id,  # noqa: F821  dev: administrators approve cycles
    "entitlement_validation_group_id": env.ref("base.group_system").id})  # noqa: F821
program = env["g2p.program"].browse(wiz.create_program()["res_id"])  # noqa: F821
program.write({"target_type": "group"})
manager = env["g2p.program.payment.manager.g2p.connect"].create({  # noqa: F821
    "name": f"RPCMS ProGres payments {tag}", "program_id": program.id, "create_batch": True, "locale": "en_US",
    "payment_endpoint_url": base + "/create_disbursements", "status_endpoint_url": base + "/get_disbursement_status",
    "envelope_creation_url": base + "/create_disbursement_envelopes",
    "envelope_status_url": base + "/get_disbursement_envelope_status",
    "payee_id_type": "reg_id", "reg_id_type_for_payee_id": progres.id, "rpcms_progres_mode": True,
    "encryption_provider_id": provider.id, "rpcms_bridge_api": "partner_api", "sender_id": f"rpcms-dev-{tag}",
    "rpcms_sender_app_url": "http://localhost:8069", "rpcms_benefit_program_id": 1, "rpcms_benefit_code_id": 1,
    "rpcms_benefit_code_mnemonic": "CASH-UGX", "rpcms_target_registry": "PROGRES", "rpcms_disbursement_frequency": "Monthly"})
# "Create batch" is normally switched on in the form, whose onchange adds the default batch tag; do the same here.
tag_rec = env["g2p.payment.batch.tag"].create({"name": f"Default {program.name}", "order": 1, "domain": "[]",  # noqa: F821
                                              "max_batch_size": 500})
manager.write({"batch_tag_ids": [(4, tag_rec.id)]})
# The wizard already created a "Default" payment manager; a programme may have only one, so point it at ours.
parent = program.payment_managers[:1]
parent.write({"manager_ref_id": f"g2p.program.payment.manager.g2p.connect,{manager.id}"})
check("payment manager attached to the programme", program.get_manager(program.MANAGER_PAYMENT) == manager)

# Fund the programme (entitlements can only be approved against posted funds)
fund = env["g2p.program.fund"].create({"program_id": program.id, "amount": AMOUNT_PER_HOUSEHOLD * len(HOUSEHOLDS) * 2,  # noqa: F821
                                       "remarks": "DEV funding"})
fund.post_fund()
env.flush_all()  # noqa: F821  the balance check reads the table with raw SQL
check("programme funded", fund.state == "posted", fund.state)

# 3. Enrol the households
program.write({"program_membership_ids": [(0, 0, {"partner_id": p.id}) for p in partners]})
program.enroll_eligible_registrants()
enrolled = program.get_beneficiaries("enrolled", count=True)
check("all households enrolled", enrolled == len(partners), f"enrolled={enrolled}")

# 4. Cycle with beneficiaries
program.create_new_cycle()  # returns a notification action, not the cycle
cycle = env["g2p.cycle"].search([("program_id", "=", program.id)], order="id desc", limit=1)  # noqa: F821
if not cycle.cycle_membership_ids:  # the programme manager may already have copied them
    cycle.copy_beneficiaries_from_program()
check("cycle has the beneficiaries", cycle.get_beneficiaries("draft", count=True) == len(partners) or
      len(cycle.cycle_membership_ids) == len(partners), f"{len(cycle.cycle_membership_ids)}")

assistance_type = f"{program.name} #{manager.id}"
info = {"tag": tag, "program": program.name, "program_id": program.id, "cycle_id": cycle.id,
        "assistance_type": assistance_type, "cycle": mapping.cycle_code(cycle.name, cycle.id),
        "currency": currency.name, "amount": AMOUNT_PER_HOUSEHOLD,
        "households": [{"progres_id": h[0], "name": h[1]} for h in HOUSEHOLDS]}

if steps == "all":
    cycle.prepare_entitlement()
    ents = env["g2p.entitlement"].search([("cycle_id", "=", cycle.id)])  # noqa: F821
    check("entitlements prepared", len(ents) == len(partners), f"{len(ents)}")
    cycle.to_approve()
    cycle.approve()
    check("cycle approved", cycle.state == "approved", cycle.state)
    cycle.validate_entitlement()  # the "Validate entitlements" step; approves them against the posted funds
    ents.invalidate_recordset()
    check("entitlements validated and approved", set(ents.mapped("state")) == {"approved"}, str(ents.mapped("state")))
    cycle.prepare_payment()
    pays = env["g2p.payment"].search([("cycle_id", "=", cycle.id)])  # noqa: F821
    check("payments prepared", len(pays) == len(partners), f"{len(pays)}")
    cycle.invalidate_recordset()
    check("cycle has payment batches", len(cycle.payment_batch_ids) >= 1, str(len(cycle.payment_batch_ids)))
    cycle.send_payment()
    pays.invalidate_recordset()
    check("payments sent to the mock Bridge", set(pays.mapped("dispatch_status")) == {"sent"}, str(pays.mapped("dispatch_status")))
    sent = requests.get(base + "/dev/state", timeout=5).json()["disbursements"]
    ids = [h for h in pays.mapped("disbursement_id") if h]
    check("mock Bridge holds one disbursement per household", len(ids) == len(partners) and all(i in sent for i in ids))
    mine = [sent[i] if isinstance(sent, dict) else None for i in ids[:1]]
    print("sample disbursement at the mock:", json.dumps(mine[0], default=str)[:300] if mine and mine[0] else "n/a")
    requests.post(base + "/dev/settle", json=ids[: len(ids) - 1], timeout=5)  # settle all but the last: one stays 'pending'
    manager.payments_status_check(manager.id)
    pays.invalidate_recordset()
    n_set = len(pays.filtered("remittance_statement_id"))
    check("settled payments copied into Odoo (all but one)", n_set == len(partners) - 1, f"{n_set}")
    info["payments"] = [{"id": p.id, "progres_id": manager._get_payee_fa(p),
                         "disbursement_id": p.disbursement_id, "settled": bool(p.remittance_statement_id)} for p in pays]

os.makedirs(os.path.dirname(out_path), exist_ok=True)
with open(out_path, "w") as fh:
    json.dump(info, fh, indent=2)
print("wrote", out_path)
print("\nRESULT:", "ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED")
env.cr.commit()  # noqa: F821
