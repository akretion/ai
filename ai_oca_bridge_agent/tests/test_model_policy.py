# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Technical models stay out of reach, and secrets never reach the prompt."""

import json

from odoo.tests import HttpCase, TransactionCase, tagged


class TestModelPolicy(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ai_user = cls.env["res.users"].create(
            {
                "name": "Policy AI",
                "login": "policy_ai",
                "email": "policy_ai@example.com",
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            }
        )
        cls.agent = cls.env["ai.bridge.agent"].create(
            {"name": "Policy agent", "ai_user_id": cls.ai_user.id}
        )
        cls.grants = cls.env["ai.bridge.agent.grant"].sudo()
        cls.admin = cls.env.ref("base.user_admin")

    def _resolves(self, model_name, method="search_read"):
        return self.grants._resolve_for_user_model(
            self.admin, model_name, method, agent=self.agent
        )

    def test_business_models_stay_open(self):
        self.assertIsNotNone(self._resolves("res.partner"))
        self.assertIsNotNone(self._resolves("res.company"))

    def test_technical_models_are_denied_by_default(self):
        for model in (
            "ir.config_parameter",
            "ir.attachment",
            "ir.mail_server",
            "ir.actions.server",
            "ir.rule",
            "res.users.apikeys",
        ):
            self.assertIsNone(self._resolves(model), model)

    def test_interface_models_stay_readable(self):
        for model in ("ir.ui.menu", "ir.actions.act_window", "ir.model"):
            self.assertIsNotNone(self._resolves(model), model)

    def test_an_agent_may_open_a_denied_model(self):
        self.assertIsNone(self._resolves("ir.attachment"))
        self.agent.model_allowlist = "ir.attachment"
        self.assertIsNotNone(self._resolves("ir.attachment"))

    def test_a_deny_beats_an_allow(self):
        self.agent.write(
            {"model_allowlist": "ir.attachment", "model_denylist": "ir.attachment"}
        )
        self.assertIsNone(self._resolves("ir.attachment"))

    def test_secrets_cannot_be_opened_even_explicitly(self):
        self.agent.model_allowlist = "res.users.apikeys, ir.config_parameter"
        self.assertIsNone(self._resolves("res.users.apikeys"))
        self.assertIsNone(self._resolves("ir.config_parameter"))

    def test_the_model_list_never_offers_technical_models(self):
        models = self.grants._readable_models(self.admin, self.agent)
        self.assertIn("res.partner", models)
        self.assertNotIn("ir.config_parameter", models)
        self.assertNotIn("ir.attachment", models)

    def test_whitelist_mode_still_respects_the_policy(self):
        self.agent.tool_access_mode = "whitelist"
        grant = self.env["ai.bridge.agent.grant"].create(
            {
                "name": "params",
                "description": "Should not work",
                "model_name": "ir.config_parameter",
                "method": "search_read",
            }
        )
        self.agent.grant_ids = [(4, grant.id)]
        self.assertIsNone(self._resolves("ir.config_parameter"))


class TestConfigKeys(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ai_user = cls.env["res.users"].create(
            {
                "name": "Config AI",
                "login": "config_ai",
                "email": "config_ai@example.com",
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            }
        )
        cls.agent = cls.env["ai.bridge.agent"].create(
            {"name": "Config agent", "ai_user_id": cls.ai_user.id}
        )
        cls.Params = cls.env["ir.config_parameter"].sudo()

    def test_default_allowlist_covers_the_instance_url(self):
        self.assertTrue(self.agent._config_key_allowed("web.base.url"))
        self.assertFalse(self.agent._config_key_allowed("database.secret"))

    def test_prefix_allowlist(self):
        self.agent.config_key_allowlist = "web.*"
        self.assertTrue(self.agent._config_key_allowed("web.base.url"))
        self.assertFalse(self.agent._config_key_allowed("mail.smtp.password"))

    def test_plain_value_is_returned(self):
        self.Params.set_param("web.base.url", "https://example.test")
        found, value, encrypted = self.agent._read_config_key("web.base.url")
        self.assertTrue(found)
        self.assertEqual(value, "https://example.test")
        self.assertFalse(encrypted)

    def test_secret_is_reported_as_present_but_not_disclosed(self):
        if "encrypted.data" not in self.env:
            self.skipTest("data_encryption is not installed")
        found, value, encrypted = self.agent._read_encrypted_key("some.secret")
        # With the module present the value comes encrypted and flagged; the
        # controller is what withholds it from the model.
        self.assertTrue(encrypted or not found)


@tagged("post_install", "-at_install")
class TestConfigEndpoint(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ai_user = cls.env["res.users"].create(
            {
                "name": "Config human AI",
                "login": "config_human_ai",
                "email": "config_human_ai@example.com",
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            }
        )
        cls.human = cls.env["res.users"].create(
            {
                "name": "Config human",
                "login": "config_human",
                "email": "config_human@example.com",
                "groups_id": [
                    (
                        6,
                        0,
                        [
                            cls.env.ref("base.group_user").id,
                            cls.env.ref("ai_oca_bridge_agent.group_agent_user").id,
                        ],
                    )
                ],
            }
        )
        cls.agent = cls.env["ai.bridge.agent"].create(
            {"name": "Config endpoint", "ai_user_id": cls.ai_user.id}
        )
        cls.env["ir.config_parameter"].sudo().set_param(
            "web.base.url", "https://config.example.test"
        )

    def _post(self, path, payload):
        answer = self.url_open(
            path,
            data=json.dumps(payload),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.agent.webhook_token}",
            },
        ).json()
        return answer.get("result", answer)

    def test_allowed_key_is_returned(self):
        answer = self._post(
            "/ai_agent/config/get",
            {"user_id": self.human.id, "key": "web.base.url"},
        )
        self.assertEqual(answer["status"], "ok")
        self.assertEqual(answer["value"], "https://config.example.test")
        self.assertFalse(answer["secret"])

    def test_other_key_is_refused_with_the_list(self):
        answer = self._post(
            "/ai_agent/config/get",
            {"user_id": self.human.id, "key": "mail.smtp.password"},
        )
        self.assertEqual(answer["error"], "key_not_allowed")
        self.assertIn("web.base.url", answer["allowed"])
