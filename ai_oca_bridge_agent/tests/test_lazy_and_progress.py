# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Lazy skills, per-conversation context and transient progress notes.

The three ideas come from the OCA agent framework being built in
OCA/ai#116 (``ai_tool_skill``: a skill that behaves like a tool, so it can be
loaded on demand) and OCA/ai#117 (``ai_agent``: one session per conversation,
view context attached server-side, and a live note per working step). They are
implemented here on our own models for now; if those modules land, the same
abstractions can be shared instead of duplicated behind the same endpoints.
"""

import json
import time

from odoo.tests import HttpCase, TransactionCase, tagged


class TestLazySkills(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.agent = cls.env["ai.bridge.agent"].create(
            {"name": "Lazy agent", "ai_user_id": cls.env.user.id}
        )
        cls.skill = cls.env["ai.bridge.agent.skill"].create(
            {
                "name": "Accounting answers",
                "summary": "How to answer accounting questions",
                "instructions": "<p>Always mention the currency.</p>",
            }
        )
        cls.agent.skill_ids = [(6, 0, cls.skill.ids)]

    def test_inline_skill_carries_its_text(self):
        definition = self.agent._get_agent_definition()
        skill = definition["skills"][0]
        self.assertEqual(definition["skill_loading"], "inline")
        self.assertEqual(skill["instructions"], "<p>Always mention the currency.</p>")
        self.assertFalse(skill["lazy"])

    def test_lazy_skill_withholds_its_text(self):
        self.agent.skill_loading = "lazy"
        definition = self.agent._get_agent_definition()
        skill = definition["skills"][0]
        self.assertEqual(definition["skill_loading"], "lazy")
        self.assertEqual(skill["instructions"], "")
        self.assertTrue(skill["lazy"])
        self.assertEqual(skill["summary"], "How to answer accounting questions")


@tagged("post_install", "-at_install")
class TestSkillsAndProgressEndpoints(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ai_user = cls.env["res.users"].create(
            {
                "name": "Progress AI",
                "login": "progress_ai",
                "email": "progress_ai@example.com",
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            }
        )
        cls.human = cls.env["res.users"].create(
            {
                "name": "Progress human",
                "login": "progress_human",
                "email": "progress_human@example.com",
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
            {"name": "Progress agent", "ai_user_id": cls.ai_user.id}
        )
        cls.channel = cls.env["discuss.channel"].create(
            {"name": "Progress channel", "channel_type": "channel"}
        )
        cls.skill = cls.env["ai.bridge.agent.skill"].create(
            {
                "name": "Support tone",
                "summary": "Be brief",
                "instructions": "<p>Answer in three lines at most.</p>",
            }
        )
        cls.agent.skill_ids = [(6, 0, cls.skill.ids)]

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

    def test_skill_is_loaded_on_demand(self):
        answer = self._post(
            "/ai_agent/skills/get",
            {"user_id": self.human.id, "name": "Support tone"},
        )
        self.assertEqual(answer["status"], "ok")
        self.assertIn("three lines", answer["skill"]["instructions"])

    def test_unknown_skill_is_refused(self):
        answer = self._post(
            "/ai_agent/skills/get", {"user_id": self.human.id, "name": "nope"}
        )
        self.assertEqual(answer["error"], "skill_not_found")

    def test_progress_note_is_posted_and_removed_with_the_answer(self):
        queued = self.env["ai.bridge.agent.message"].create(
            {
                "channel_id": self.channel.id,
                "body": "hello",
                "agent_id": self.agent.id,
                "user_id": self.human.id,
                "session_key": self.agent._get_session_key(
                    user=self.human, channel=self.channel
                ),
                "delivery_id": "progress-1",
            }
        )
        answer = self._post(
            "/ai_agent/progress", {"queue_id": queued.id, "text": "Thinking..."}
        )
        self.assertEqual(answer["status"], "ok")
        note_id = answer["message_id"]
        self.assertTrue(self.env["mail.message"].browse(note_id).exists())
        queued.invalidate_recordset()
        self.assertEqual(queued.progress_message_ids, [note_id])

        # The answer lands: the transient note goes away.
        body = json.dumps(
            {
                "queue_id": queued.id,
                "delivery_id": "progress-1",
                "channel_id": self.channel.id,
                "body": "Done.",
                "format": "plain",
            }
        )
        timestamp = str(int(time.time()))
        signature = self.agent._compute_signature(timestamp, body)
        self.url_open(
            f"/ai_agent/webhook/{self.agent.webhook_token}",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-AI-Agent-Timestamp": timestamp,
                "X-AI-Agent-Signature": f"sha256={signature}",
            },
        )
        self.assertFalse(self.env["mail.message"].browse(note_id).exists())
