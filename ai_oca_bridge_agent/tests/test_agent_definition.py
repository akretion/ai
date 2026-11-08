# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestAgentDefinition(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ai_user = cls.env["res.users"].create(
            {
                "name": "Definition AI",
                "login": "definition_ai",
                "email": "definition_ai@example.com",
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            }
        )
        cls.agent = cls.env["ai.bridge.agent"].create(
            {"name": "Definition agent", "ai_user_id": cls.ai_user.id}
        )

    def test_agent_key_is_generated_and_unique(self):
        self.assertTrue(self.agent.agent_key)
        other = self.env["ai.bridge.agent"].create(
            {"name": "Other agent", "ai_user_id": self.ai_user.id}
        )
        self.assertNotEqual(self.agent.agent_key, other.agent_key)

    def test_backend_defaults_to_hermes(self):
        self.assertEqual(self.agent.backend, "hermes")

    def test_definition_composes_instructions_skills_and_knowledge(self):
        skill = self.env["ai.bridge.agent.skill"].create(
            {
                "name": "Accounting",
                "instructions": "<p>Use the chart of accounts.</p>",
                "tool_names": "odoo_search, odoo_count",
            }
        )
        self.agent.write(
            {
                "instructions": "<p>You answer accounting questions.</p>",
                "operator_instructions": "Never invent figures.",
                "skill_ids": [(6, 0, skill.ids)],
                "llm_provider": "deepseek",
                "llm_model": "deepseek-v4-pro",
            }
        )
        self.env["ai.bridge.agent.knowledge"].create(
            {
                "agent_id": self.agent.id,
                "name": "Fiscal calendar",
                "body": "<p>Closing on the 25th.</p>",
            }
        )
        definition = self.agent._get_agent_definition()
        self.assertEqual(definition["key"], self.agent.agent_key)
        self.assertIn("You answer accounting questions.", definition["instructions"])
        self.assertNotIn("<p>", definition["instructions"])
        self.assertEqual(definition["operator_instructions"], "Never invent figures.")
        self.assertEqual(definition["skills"][0]["name"], "Accounting")
        self.assertEqual(
            definition["skills"][0]["tools"], ["odoo_search", "odoo_count"]
        )
        self.assertIn("Closing on the 25th.", definition["knowledge"][0]["text"])
        self.assertEqual(definition["llm"]["provider"], "deepseek")
        self.assertEqual(definition["llm"]["model"], "deepseek-v4-pro")

    def test_knowledge_budget_truncates(self):
        self.agent.knowledge_max_chars = 20
        knowledge = self.env["ai.bridge.agent.knowledge"]
        for index in range(3):
            knowledge.create(
                {
                    "agent_id": self.agent.id,
                    "name": f"Doc {index}",
                    "body": "x" * 15,
                }
            )
        definition = self.agent._get_agent_definition()
        self.assertEqual(len(definition["knowledge"]), 1)

    def test_inactive_skills_and_knowledge_are_skipped(self):
        skill = self.env["ai.bridge.agent.skill"].create(
            {"name": "Archived skill", "instructions": "<p>nope</p>", "active": False}
        )
        self.env["ai.bridge.agent.knowledge"].create(
            {
                "agent_id": self.agent.id,
                "name": "Old",
                "body": "<p>old</p>",
                "active": False,
            }
        )
        self.agent.skill_ids = [(6, 0, skill.ids)]
        definition = self.agent._get_agent_definition()
        self.assertEqual(definition["skills"], [])
        self.assertEqual(definition["knowledge"], [])

    def test_poll_payload_carries_the_agent_definition(self):
        payload = self.agent._get_agent_definition()
        self.assertIn("instructions", payload)
        self.assertIn("backend", payload)
        self.assertIn("llm", payload)
