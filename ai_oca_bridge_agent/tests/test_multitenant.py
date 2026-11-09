# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import hashlib
import hmac
from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase


class TestAgentMultitenant(TransactionCase):
    """Tenant identity, atomic claiming and delivery bookkeeping."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ResUsers = cls.env["res.users"]
        cls.agents = cls.env["ai.bridge.agent"]
        cls.Queue = cls.env["ai.bridge.agent.message"]
        cls.Channel = cls.env["discuss.channel"]

        cls.ai_user = cls.ResUsers.create(
            {
                "name": "MT AI",
                "login": "mt_ai_user",
                "partner_id": cls.env.user.partner_id.id,
            }
        )
        cls.ai_bridge = cls.env["ai.bridge"].create(
            {"name": "MT Bridge", "usage": "agent"}
        )
        cls.ai_user.ai_bridge_id = cls.ai_bridge.id

        cls.agent = cls.agents.create(
            {"name": "Tenant A", "ai_user_id": cls.ai_user.id}
        )
        cls.channel = cls.Channel.create(
            {"name": "Tenant Channel", "channel_type": "channel"}
        )

    def _create_message(self, **kwargs):
        values = {
            "channel_id": self.channel.id,
            "body": "<p>hello</p>",
            "agent_id": self.agent.id,
            "user_id": self.env.user.id,
        }
        values.update(kwargs)
        return self.Queue.create(values)

    def test_tenant_key_shared_by_gateways(self):
        other = self.agents.create(
            {"name": "Tenant A bis", "ai_user_id": self.ai_user.id}
        )
        self.assertTrue(self.agent.tenant_key)
        self.assertEqual(len(self.agent.tenant_key), 16)
        self.assertEqual(other.tenant_key, self.agent.tenant_key)

    def test_tenant_key_survives_gateway_change(self):
        key = self.agent.tenant_key
        self.agent.write({"name": "Renamed"})
        self.assertEqual(self.agent.tenant_key, key)

    def test_session_key_user_scope(self):
        self.agent.session_scope = "user"
        expected = f"odoo:{self.agent.tenant_key}:prod:user_{self.env.user.id}"
        self.assertEqual(
            self.agent._get_session_key(user=self.env.user, channel=self.channel),
            expected,
        )

    def test_session_key_channel_scope(self):
        self.agent.session_scope = "channel"
        expected = (
            f"odoo:{self.agent.tenant_key}:prod:user_{self.env.user.id}"
            f":channel_{self.channel.id}"
        )
        self.assertEqual(
            self.agent._get_session_key(user=self.env.user, channel=self.channel),
            expected,
        )

    def test_session_scope_defaults_to_one_memory_per_conversation(self):
        agent = self.agents.create(
            {"name": "Scope default", "ai_user_id": self.ai_user.id}
        )
        self.assertEqual(agent.session_scope, "channel")
        other = self.Channel.create({"name": "Other", "channel_type": "channel"})
        first = agent._get_session_key(user=self.env.user, channel=self.channel)
        second = agent._get_session_key(user=self.env.user, channel=other)
        self.assertNotEqual(first, second)
        # The user id stays readable in both shapes: the responder resolves
        # credentials and tools from it.
        self.assertIn(f"user_{self.env.user.id}", first)
        self.assertIn(f"user_{self.env.user.id}", second)

    def test_session_key_includes_environment(self):
        self.agent.environment = "staging"
        self.assertIn(":staging:", self.agent._get_session_key(user=self.env.user))

    def test_claim_batch_is_atomic(self):
        messages = (
            self._create_message() | self._create_message() | self._create_message()
        )
        first = self.Queue._claim_batch(self.agent, limit=2, worker="worker-1")
        self.assertEqual(len(first), 2)
        self.assertTrue(all(msg.state == "processing" for msg in first))
        second = self.Queue._claim_batch(self.agent, limit=2, worker="worker-2")
        self.assertEqual(len(second), 1)
        self.assertFalse(first & second)
        self.assertEqual(len(messages), 3)

    def test_claim_batch_skips_messages_scheduled_later(self):
        message = self._create_message(
            next_attempt_at=fields.Datetime.now() + timedelta(minutes=10)
        )
        claimed = self.Queue._claim_batch(self.agent)
        self.assertNotIn(message, claimed)
        self.assertEqual(message.state, "pending")

    def test_claim_batch_increments_attempts(self):
        message = self._create_message()
        claimed = self.Queue._claim_batch(self.agent)
        self.assertEqual(claimed, message)
        self.assertEqual(message.attempts, 1)

    def test_requeue_stale_returns_message_to_pending(self):
        message = self._create_message()
        self.Queue._claim_batch(self.agent)
        message.write({"claimed_at": fields.Datetime.now() - timedelta(minutes=30)})
        self.Queue._requeue_stale(self.agent)
        self.assertEqual(message.state, "pending")
        self.assertFalse(message.claimed_at)

    def test_requeue_stale_marks_dead_after_max_attempts(self):
        message = self._create_message()
        self.Queue._claim_batch(self.agent)
        message.write(
            {
                "attempts": self.agent.max_attempts,
                "claimed_at": fields.Datetime.now() - timedelta(minutes=30),
            }
        )
        self.Queue._requeue_stale(self.agent)
        self.assertEqual(message.state, "dead")

    def test_mark_error_schedules_a_retry(self):
        message = self._create_message()
        self.Queue._claim_batch(self.agent)
        message._mark_error("agent timeout")
        self.assertEqual(message.state, "pending")
        self.assertTrue(message.next_attempt_at)
        self.assertEqual(message.error_message, "agent timeout")

    def test_mark_error_gives_up_after_max_attempts(self):
        message = self._create_message()
        self.Queue._claim_batch(self.agent)
        message.write({"attempts": self.agent.max_attempts})
        message._mark_error("agent down")
        self.assertEqual(message.state, "dead")

    def test_mark_done_closes_only_target(self):
        first = self._create_message()
        second = self._create_message()
        self.Queue._claim_batch(self.agent)
        first._mark_done()
        self.assertEqual(first.state, "done")
        self.assertEqual(second.state, "processing")

    def test_find_by_delivery_is_idempotent(self):
        message = self._create_message(delivery_id="delivery-1")
        found = self.Queue._find_by_delivery(self.agent, "delivery-1")
        self.assertEqual(found, message)
        self.assertFalse(self.Queue._find_by_delivery(self.agent, "delivery-2"))
        self.assertFalse(self.Queue._find_by_delivery(self.agent, False))

    def test_token_rotation_keeps_previous_valid(self):
        old_token = self.agent.webhook_token
        self.agent.action_regenerate_token()
        self.assertNotEqual(self.agent.webhook_token, old_token)
        self.assertTrue(self.agent._is_token_valid(old_token))
        self.assertTrue(self.agent._is_token_valid(self.agent.webhook_token))

    def test_token_rotation_grace_expires(self):
        old_token = self.agent.webhook_token
        self.agent.action_regenerate_token()
        self.agent.write(
            {"token_rotated_at": fields.Datetime.now() - timedelta(days=1)}
        )
        self.assertFalse(self.agent._is_token_valid(old_token))
        self.assertTrue(self.agent._is_token_valid(self.agent.webhook_token))

    def test_token_validation_rejects_unknown(self):
        self.assertFalse(self.agent._is_token_valid("nope"))
        self.assertFalse(self.agent._is_token_valid(""))

    def test_signature_matches_hmac_sha256(self):
        body = '{"event": "message.created"}'
        timestamp = "1700000000"
        expected = hmac.new(
            self.agent.push_secret.encode(),
            f"{timestamp}.{body}".encode(),
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(self.agent._compute_signature(timestamp, body), expected)

    def test_pending_count_counts_open_states(self):
        message = self._create_message()
        # Non stored compute: the env cache keeps the value within a
        # transaction, a real request computes it once per record.
        self.agent.invalidate_recordset(["pending_count"])
        self.assertEqual(self.agent.pending_count, 1)
        self.Queue._claim_batch(self.agent)
        message._mark_done()
        self.agent.invalidate_recordset(["pending_count"])
        self.assertEqual(self.agent.pending_count, 0)
