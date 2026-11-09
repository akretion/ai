# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import json
import time

from odoo.exceptions import AccessError
from odoo.tests import HttpCase, TransactionCase, tagged


class TestDeclaredTools(TransactionCase):
    """A tool reaches an agent only when assigned, and runs as the user."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({"name": "Tool partner"})
        cls.user = cls.env["res.users"].create(
            {
                "name": "Tool user",
                "login": "tool_user",
                "email": "tool_user@example.com",
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
        cls.ai_user = cls.env["res.users"].create(
            {
                "name": "Tool AI",
                "login": "tool_ai",
                "email": "tool_ai@example.com",
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            }
        )
        cls.agent = cls.env["ai.bridge.agent"].create(
            {"name": "Tool agent", "ai_user_id": cls.ai_user.id}
        )
        cls.date_tool = cls.env.ref("ai_tool.current_date")
        cls.post_tool = cls.env.ref("ai_tool.post_message")

    def test_tool_reaches_agent_only_when_assigned(self):
        self.assertFalse(self.agent._get_declared_tools(self.user))
        self.agent.tool_ids = [(6, 0, self.date_tool.ids)]
        self.assertEqual(self.agent._get_declared_tools(self.user), self.date_tool)
        self.assertFalse(self.agent._find_declared_tool(self.user, "post_message"))

    def test_tool_definition_is_exposed_with_its_schema(self):
        self.agent.tool_ids = [(6, 0, self.date_tool.ids)]
        definition = self.agent._get_declared_tools(self.user)._get_tool_definition()
        self.assertEqual(definition["name"], "get_date")
        self.assertEqual(definition["inputSchema"]["type"], "object")

    def test_record_tool_runs_with_the_user_rights(self):
        tool = self.env["ai.tool"].create(
            {
                "name": "read_partner",
                "description": "Read one partner.",
                "model_id": self.env.ref("base.model_res_partner").id,
                "function_name": "read",
                "kind": "record",
            }
        )
        self.agent.tool_ids = [(6, 0, tool.ids)]
        result = tool._run_for_user(self.user, {}, record_id=self.partner.id)
        self.assertEqual(result[0]["id"], self.partner.id)
        self.assertEqual(result[0]["display_name"], "Tool partner")

    def test_record_tool_refuses_a_record_the_user_cannot_read(self):
        private = self.env["res.partner"].create(
            {"name": "Private", "user_id": self.env.user.id}
        )
        # No group gives this user access to a partner that is not public and
        # not theirs; the record rule applies when browsing as the user.
        self.env["ir.rule"].create(
            {
                "name": "Tool test: own partners only",
                "model_id": self.env.ref("base.model_res_partner").id,
                "domain_force": "[('id', '=', %d)]" % self.env.user.partner_id.id,
                "groups": [(6, 0, [self.env.ref("base.group_user").id])],
            }
        )
        with self.assertRaises(AccessError):
            self.post_tool._run_for_user(
                self.user,
                {"message": "should not land"},
                record_model="res.partner",
                record_id=private.id,
            )

    def test_duplicate_tool_names_are_refused_instead_of_guessed(self):
        copy = self.post_tool.copy({"name": "post_message"})
        self.agent.tool_ids = [(6, 0, (self.post_tool | copy).ids)]
        self.assertFalse(self.agent._find_declared_tool(self.user, "post_message"))

    def test_tool_on_a_model_the_user_cannot_read_is_not_offered(self):
        tool = self.env["ai.tool"].create(
            {
                "name": "secret_tool",
                "description": "Acts on a model the user cannot read.",
                "model_id": self.env.ref("base.model_ir_model").id,
                "function_name": "read",
                "kind": "record",
            }
        )
        self.agent.tool_ids = [(6, 0, tool.ids)]
        self.assertFalse(self.agent._get_declared_tools(self.user))

    def test_a_skill_brings_its_tools_to_the_agent(self):
        skill = self.env["ai.bridge.agent.skill"].create(
            {
                "name": "Date answers",
                "summary": "Tell the date",
                "instructions": "<p>Answer with the date.</p>",
                "tool_ids": [(6, 0, self.date_tool.ids)],
            }
        )
        self.agent.skill_ids = [(6, 0, skill.ids)]
        self.assertFalse(self.agent.tool_ids)
        self.assertEqual(self.agent._get_declared_tools(self.user), self.date_tool)
        self.assertEqual(
            self.agent._find_declared_tool(self.user, "get_date"), self.date_tool
        )
        self.assertIn("get_date", skill._as_skill_dict()["tools"])
        self.assertEqual(
            skill._as_skill_dict()["declared_tools"][0]["name"], "get_date"
        )

    def test_a_tool_found_twice_is_offered_once(self):
        skill = self.env["ai.bridge.agent.skill"].create(
            {
                "name": "Date again",
                "instructions": "<p>Again.</p>",
                "tool_ids": [(6, 0, self.date_tool.ids)],
            }
        )
        self.agent.write(
            {
                "tool_ids": [(6, 0, self.date_tool.ids)],
                "skill_ids": [(6, 0, skill.ids)],
            }
        )
        self.assertEqual(len(self.agent._get_declared_tools(self.user)), 1)

    def test_readonly_agent_refuses_a_mutating_tool(self):
        self.agent.tool_ids = [(6, 0, self.post_tool.ids)]
        self.assertFalse(self.post_tool.readonly)
        self.agent.readonly_tools = True
        self.assertFalse(self.agent._get_declared_tools(self.user))
        self.assertFalse(self.agent._find_declared_tool(self.user, "post_message"))
        self.post_tool.readonly = True
        self.assertEqual(self.agent._get_declared_tools(self.user), self.post_tool)


@tagged("post_install", "-at_install")
class TestDeclaredToolEndpoint(HttpCase):
    """The two endpoints the responder calls, over HTTP."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ai_user = cls.env["res.users"].create(
            {
                "name": "Endpoint AI",
                "login": "endpoint_ai",
                "email": "endpoint_ai@example.com",
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            }
        )
        cls.user = cls.env["res.users"].create(
            {
                "name": "Endpoint human",
                "login": "endpoint_human",
                "email": "endpoint_human@example.com",
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
            {"name": "Endpoint agent", "ai_user_id": cls.ai_user.id}
        )

    def _post(self, path, payload):
        answer = self.url_open(
            path,
            data=json.dumps(payload),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.agent.webhook_token}",
                "X-AI-Agent-Timestamp": str(int(time.time())),
            },
        ).json()
        # type="json" routes answer inside the JSON-RPC envelope.
        return answer.get("result", answer)

    def test_declared_and_call(self):
        tool = self.env.ref("ai_tool.current_date")
        self.agent.tool_ids = [(6, 0, tool.ids)]
        declared = self._post("/ai_agent/tools/declared", {"user_id": self.user.id})
        self.assertEqual(declared["status"], "ok")
        self.assertEqual([t["name"] for t in declared["tools"]], ["get_date"])
        called = self._post(
            "/ai_agent/tools/call",
            {"user_id": self.user.id, "tool": "get_date", "arguments": {}},
        )
        self.assertEqual(called["status"], "ok")
        self.assertIn("date", called["result"])

    def test_call_refuses_an_unassigned_tool(self):
        answer = self._post(
            "/ai_agent/tools/call",
            {"user_id": self.user.id, "tool": "post_message", "arguments": {}},
        )
        self.assertEqual(answer["error"], "tool_not_assigned")

    def test_bad_token_is_refused(self):
        answer = self.url_open(
            "/ai_agent/tools/declared",
            data=json.dumps({"user_id": self.user.id}),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer nope",
            },
        ).json()
        answer = answer.get("result", answer)
        self.assertEqual(answer["error"], "invalid_token")
