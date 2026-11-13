from datetime import timedelta
from unittest.mock import MagicMock, patch

import requests

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.rpcms_pbms_bridge.models.payment_manager import mapping


@tagged("post_install", "-at_install")
class TestRpcmsProgresMode(TransactionCase):
    def setUp(self):
        super().setUp()
        self.progres = self.env.ref("rpcms_pbms_bridge.id_type_unhcr_progres")
        self.program = self.env["g2p.program"].create({"name": "Bidibidi Cash"})
        self.manager = self.env["g2p.program.payment.manager.g2p.connect"].create(
            {
                "name": "RPCMS manager",
                "payment_endpoint_url": "http://example.com/payment",
                "status_endpoint_url": "http://example.com/status",
                "envelope_creation_url": "http://example.com/envelope",
                "envelope_status_url": "http://example.com/envelope_status",
                "program_id": self.program.id,
                "payee_id_type": "reg_id",
                "reg_id_type_for_payee_id": self.progres.id,
                "create_batch": True,
                "locale": "en_US",
                "rpcms_progres_mode": True,
            }
        )
        self.cycle = self._cycle("Nov 2026")

    def _cycle(self, name):
        cycle = self.env["g2p.cycle"].create(
            {
                "name": name,
                "program_id": self.program.id,
                "start_date": fields.Datetime.now(),
                "end_date": fields.Datetime.now() + timedelta(days=7),
            }
        )
        partner = self.env["res.partner"].create({"name": f"Person {name}"})
        self.env["g2p.entitlement"].create(
            {"program_id": self.program.id, "partner_id": partner.id, "cycle_id": cycle.id,
             "state": "draft", "initial_amount": 100.0}
        )
        return cycle

    def _post_mock(self, envelope_id="ENV-1"):
        resp = MagicMock()
        resp.json.return_value = {"message": {"disbursement_envelope_id": envelope_id}}
        resp.content = b"{}"
        return resp

    def _sent_message(self, post):
        return post.call_args.kwargs["json"]["message"]

    def test_progres_id_type_is_installed(self):
        self.assertEqual(self.progres.name, mapping.PROGRES_ID_TYPE_NAME)

    def test_constraint_rejects_wrong_configuration(self):
        for vals in (
            {"payee_id_type": "phone"},
            {"payee_prefix": "reg_id:"},
            {"payee_suffix": "@x"},
        ):
            with self.assertRaises(ValidationError, msg=str(vals)):
                self.manager.write(vals)
        other = self.env["g2p.id.type"].create({"name": "National ID"})
        with self.assertRaises(ValidationError):
            self.manager.write({"reg_id_type_for_payee_id": other.id})

    def test_constraint_not_applied_without_the_flag(self):
        self.manager.write({"rpcms_progres_mode": False})
        self.manager.write({"payee_id_type": "phone"})  # no error: upstream behaviour

    def test_beneficiary_id_is_the_progres_id(self):
        partner = self.env["res.partner"].create({"name": "Mary"})
        partner.reg_ids = [(0, 0, {"id_type": self.progres.id, "value": "UGA-00423318"})]
        payment = self.env["g2p.payment"].new({"partner_id": partner.id})
        self.assertEqual(self.manager._get_payee_fa(payment), "UGA-00423318")

    def test_envelope_gets_a_per_cycle_code(self):
        with patch("requests.post", return_value=self._post_mock()) as post, patch.object(
            type(self.manager), "create_jwt_token", return_value="jwt"
        ):
            self.assertTrue(self.manager._create_envelope_g2p_bridge(self.cycle))
        msg = self._sent_message(post)
        self.assertEqual(msg["cycle_code_mnemonic"], mapping.cycle_code("Nov 2026", self.cycle.id))
        self.assertNotEqual(msg["cycle_code_mnemonic"], "SEP")
        self.assertEqual(msg["benefit_program_mnemonic"], f"Bidibidi Cash #{self.manager.id}")
        self.assertEqual(msg["number_of_disbursements"], 1)
        self.assertEqual(self.cycle.disbursement_envelope_id, "ENV-1")

    def test_two_cycles_get_distinct_codes(self):
        second = self._cycle("Dec 2026")
        codes = []
        with patch("requests.post", return_value=self._post_mock()) as post, patch.object(
            type(self.manager), "create_jwt_token", return_value="jwt"
        ):
            for cycle in (self.cycle, second):
                self.manager._create_envelope_g2p_bridge(cycle)
                codes.append(self._sent_message(post)["cycle_code_mnemonic"])
        self.assertEqual(len(set(codes)), 2)

    def test_upstream_behaviour_without_the_flag(self):
        self.manager.write({"rpcms_progres_mode": False})
        with patch("requests.post", return_value=self._post_mock("ENV-U")) as post, patch.object(
            type(self.manager), "create_jwt_token", return_value="jwt"
        ):
            self.manager._create_envelope_g2p_bridge(self.cycle)
        self.assertEqual(self._sent_message(post)["cycle_code_mnemonic"], "SEP")

    def test_failed_envelope_call_stores_nothing(self):
        resp = self._post_mock()
        resp.raise_for_status.side_effect = requests.HTTPError("500")
        with patch("requests.post", return_value=resp), patch.object(
            type(self.manager), "create_jwt_token", return_value="jwt"
        ):
            self.assertFalse(self.manager._create_envelope_g2p_bridge(self.cycle))
        self.assertFalse(self.cycle.disbursement_envelope_id)
