# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import json
from unittest.mock import Mock, patch

from odoo.tests.common import TransactionCase


class TestHermesTransport(TransactionCase):
    """Queueing, access control, payload and push delivery."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Queue = cls.env["hermes.message.queue"]
        cls.Channel = cls.env["discuss.channel"]
        cls.ResUsers = cls.env["res.users"]
        cls.HermesGateway = cls.env["hermes.gateway"]
        cls.Partner = cls.env["res.partner"]

        ai_partner = cls.Partner.create({"name": "Transport AI"})
        cls.ai_user = cls.ResUsers.create(
            {
                "name": "Transport AI",
                "login": "transport_ai",
                "partner_id": ai_partner.id,
            }
        )
        cls.ai_bridge = cls.env["ai.bridge"].create(
            {"name": "Transport Bridge", "usage": "hermes"}
        )
        cls.ai_user.ai_bridge_id = cls.ai_bridge.id

        cls.gateway = cls.HermesGateway.create(
            {"name": "Transport Tenant", "ai_user_id": cls.ai_user.id}
        )
        cls.human = cls.ResUsers.create(
            {
                "name": "Hermes Human",
                "login": "hermes_human",
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
        cls.channel = cls.Channel.create(
            {
                "name": "Transport Channel",
                "channel_type": "channel",
                "hermes_gateway_id": cls.gateway.id,
            }
        )
        cls.env["discuss.channel.member"].create(
            {"channel_id": cls.channel.id, "partner_id": cls.human.partner_id.id}
        )

    def _post(self, body="hello", user=None):
        channel = self.channel.with_user(user or self.human)
        return channel.message_post(body=body, message_type="comment")

    def _queued(self):
        return self.Queue.search([("channel_id", "=", self.channel.id)])

    def _add_member(self, user):
        self.env["discuss.channel.member"].create(
            {"channel_id": self.channel.id, "partner_id": user.partner_id.id}
        )

    def test_message_queues_user_and_session_key(self):
        self._post()
        queued = self._queued()
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued.user_id, self.human)
        self.assertTrue(queued.session_key.startswith("odoo:"))
        self.assertTrue(queued.session_key.endswith(f"user_{self.human.id}"))
        self.assertTrue(queued.delivery_id)
        self.assertEqual(queued.company_id, self.env.company)
        self.assertEqual(queued.body_text, "hello")

    def test_user_outside_group_gets_standard_answer(self):
        user = self.ResUsers.create({"name": "No Hermes", "login": "no_hermes_user"})
        self._add_member(user)
        self._post(body="<p>hi Hermes</p>", user=user)
        self.assertFalse(self._queued())
        answer = self.env["mail.message"].search(
            [
                ("model", "=", "discuss.channel"),
                ("res_id", "=", self.channel.id),
                ("author_id", "=", self.ai_user.partner_id.id),
            ],
            order="id desc",
            limit=1,
        )
        self.assertTrue(answer)
        self.assertIn("not allowed", answer.body)

    def test_access_denied_answer_is_not_repeated(self):
        user = self.ResUsers.create(
            {"name": "No Hermes bis", "login": "no_hermes_user_bis"}
        )
        self._add_member(user)
        self._post(body="<p>first</p>", user=user)
        self._post(body="<p>second</p>", user=user)
        answers = self.env["mail.message"].search(
            [
                ("model", "=", "discuss.channel"),
                ("res_id", "=", self.channel.id),
                ("author_id", "=", self.ai_user.partner_id.id),
            ]
        )
        self.assertEqual(len(answers), 1)

    def test_gateway_can_use_custom_denial_message(self):
        self.gateway.access_denied_message = "Ask the admin for access."
        user = self.ResUsers.create(
            {"name": "No Hermes ter", "login": "no_hermes_user_ter"}
        )
        self._add_member(user)
        self._post(body="<p>hi</p>", user=user)
        answer = self.env["mail.message"].search(
            [
                ("model", "=", "discuss.channel"),
                ("res_id", "=", self.channel.id),
                ("author_id", "=", self.ai_user.partner_id.id),
            ],
            order="id desc",
            limit=1,
        )
        self.assertIn("Ask the admin", answer.body)

    def test_group_member_is_allowed(self):
        user = self.ResUsers.create(
            {
                "name": "Hermes User",
                "login": "hermes_group_user",
                "groups_id": [
                    (
                        6,
                        0,
                        [
                            self.env.ref("base.group_user").id,
                            self.env.ref("ai_oca_bridge_agent.group_agent_user").id,
                        ],
                    )
                ],
            }
        )
        self._add_member(user)
        self._post(body="<p>hi Hermes</p>", user=user)
        queued = self._queued()
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued.user_id, user)

    def test_payload_describes_tenant_and_capabilities(self):
        self._post()
        queued = self._queued()
        payload = self.gateway._build_event_payload(queued)
        self.assertEqual(payload["tenant"]["key"], self.gateway.tenant_key)
        self.assertEqual(payload["tenant"]["environment"], "prod")
        self.assertEqual(payload["event"], "message.created")
        self.assertEqual(payload["session_key"], queued.session_key)
        self.assertEqual(payload["message"]["queue_id"], queued.id)
        self.assertEqual(payload["message"]["body_text"], "hello")
        self.assertIn("tier", payload["capabilities"])
        self.assertTrue(payload["reply_to"]["url"].endswith("/hermes/reply"))

    def test_capabilities_follow_api_key_presence(self):
        self._post()
        queued = self._queued()
        queued.user_has_api_key = True
        self.assertEqual(self.gateway._get_capabilities(queued)["tier"], "full")
        queued.user_has_api_key = False
        capabilities = self.gateway._get_capabilities(queued)
        self.assertEqual(capabilities["tier"], "basic")
        self.assertFalse(capabilities["odoo_rpc"])
        self.gateway.require_api_key_for_tools = False
        self.assertTrue(self.gateway._get_capabilities(queued)["odoo_rpc"])

    def test_push_sends_signed_event(self):
        self.gateway.write(
            {"push_enabled": True, "push_url": "http://hermes.test/odoo/events"}
        )
        self._post()
        queued = self._queued()
        response = Mock(status_code=202, text="")
        with patch("requests.post", return_value=response) as post:
            pushed = self.gateway._push_message(queued)
        self.assertTrue(pushed)
        self.assertEqual(queued.state, "pushed")
        headers = post.call_args.kwargs["headers"]
        self.assertEqual(headers["X-Hermes-Tenant"], self.gateway.tenant_key)
        self.assertTrue(headers["X-Hermes-Signature"].startswith("sha256="))
        body = json.loads(post.call_args.kwargs["data"].decode())
        self.assertEqual(body["delivery_id"], queued.delivery_id)
        self.assertTrue(self.gateway.last_push)

    def test_push_failure_schedules_a_retry(self):
        self.gateway.write(
            {"push_enabled": True, "push_url": "http://hermes.test/odoo/events"}
        )
        self._post()
        queued = self._queued()
        with patch("requests.post", side_effect=Exception("boom")):
            pushed = self.gateway._push_message(queued)
        self.assertFalse(pushed)
        self.assertEqual(queued.state, "pending")
        self.assertTrue(queued.next_attempt_at)
        self.assertEqual(queued.attempts, 1)

    def test_cron_pushes_pending_messages(self):
        self.gateway.write(
            {"push_enabled": True, "push_url": "http://hermes.test/odoo/events"}
        )
        self._post()
        queued = self._queued()
        response = Mock(status_code=202, text="")
        with patch("requests.post", return_value=response):
            self.Queue._cron_push_queue()
        self.assertEqual(queued.state, "pushed")

    def test_push_is_skipped_when_disabled(self):
        self._post()
        queued = self._queued()
        with patch("requests.post") as post:
            self.assertFalse(self.gateway._push_message(queued))
        post.assert_not_called()
        self.assertEqual(queued.state, "pending")
