# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests.common import TransactionCase


class TestAiBridgeAgent(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AiBridge = cls.env["ai.bridge"]
        cls.agents = cls.env["ai.bridge.agent"]
        cls.messages = cls.env["ai.bridge.agent.message"]
        cls.DiscussChannel = cls.env["discuss.channel"]
        cls.ResUsers = cls.env["res.users"]
        cls.ResPartner = cls.env["res.partner"]

        # Create test AI user - use existing partner
        cls.ai_partner = cls.env.ref("base.partner_admin")
        cls.ai_user = cls.ResUsers.create(
            {
                "name": "Test AI",
                "login": "test_ai",
                "partner_id": cls.ai_partner.id,
            }
        )
        # Create AI bridge for the user
        cls.ai_bridge = cls.AiBridge.create(
            {
                "name": "Test AI Bridge",
                "usage": "agent",
            }
        )
        cls.ai_user.ai_bridge_id = cls.ai_bridge.id

        # Create agent
        cls.agent = cls.agents.create(
            {
                "name": "Test Agent",
                "ai_user_id": cls.ai_user.id,
            }
        )

        # Create test channel
        cls.channel = cls.DiscussChannel.create(
            {
                "name": "Test Channel",
                "channel_type": "channel",
            }
        )

    def test_gateway_creation(self):
        """Test agent is created with auto-generated token."""
        self.assertTrue(self.agent.webhook_token)
        self.assertEqual(len(self.agent.webhook_token), 64)  # 32 bytes hex
        self.assertEqual(self.agent.state, "draft")

    def test_gateway_regenerate_token(self):
        """Test token regeneration."""
        old_token = self.agent.webhook_token
        self.agent.action_regenerate_token()
        self.assertNotEqual(self.agent.webhook_token, old_token)
        self.assertEqual(len(self.agent.webhook_token), 64)

    def test_gateway_test_connection_no_url(self):
        """Test connection without AI agent URL shows warning."""
        result = self.agent.action_test_connection()
        self.assertEqual(result["type"], "ir.actions.client")
        self.assertEqual(result["tag"], "display_notification")
        self.assertEqual(result["params"]["type"], "warning")

    def test_queue_message_creation(self):
        """Test message queue creation."""
        msg = self.messages.create(
            {
                "channel_id": self.channel.id,
                "body": "<p>Test message</p>",
                "agent_id": self.agent.id,
            }
        )
        self.assertEqual(msg.state, "pending")
        self.assertEqual(msg.message_type, "user")
        self.assertFalse(msg.is_escalated)

    def test_queue_message_retry(self):
        """Test retrying failed messages."""
        msg = self.messages.create(
            {
                "channel_id": self.channel.id,
                "body": "<p>Test</p>",
                "agent_id": self.agent.id,
                "state": "error",
                "error_message": "Test error",
            }
        )
        msg.action_retry()
        self.assertEqual(msg.state, "pending")
        self.assertFalse(msg.error_message)

    def test_queue_cleanup_old_messages(self):
        """Test cleanup of old messages."""
        # Create old done message
        old_msg = self.messages.create(
            {
                "channel_id": self.channel.id,
                "body": "<p>Old</p>",
                "agent_id": self.agent.id,
                "state": "done",
            }
        )
        # Manually set create_date to old
        old_msg.write({"create_date": "2020-01-01 00:00:00"})

        # Run cleanup
        self.messages._cleanup_old_messages()

        # Message should be deleted
        self.assertFalse(old_msg.exists())

    def test_discuss_channel_get_gateway(self):
        """Test finding agent for channel."""
        # Channel without agent
        channel_no_gateway = self.DiscussChannel.create(
            {
                "name": "No Agent",
                "channel_type": "channel",
            }
        )
        self.assertFalse(channel_no_gateway._get_agent())

        # Channel with explicit agent
        self.channel.agent_id = self.agent.id
        found = self.channel._get_agent()
        self.assertEqual(found, self.agent)

    def test_discuss_channel_message_post_queues(self):
        """Test that posting a message queues it for AI agent."""
        self.channel.agent_id = self.agent.id
        human = self.ResUsers.create(
            {
                "name": "Agent Human",
                "login": "gateway_human",
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

        # Post as regular user
        self.channel.with_user(human).message_post(
            body="<p>Hello AI agent</p>",
            message_type="comment",
        )

        # Check queue - search by channel only since body might be wrapped
        queued = self.messages.search(
            [
                ("channel_id", "=", self.channel.id),
            ]
        )
        self.assertTrue(queued)
        self.assertEqual(queued.state, "pending")

    def test_discuss_channel_ai_user_message_no_queue(self):
        """Test that AI user messages don't get queued (prevent loops)."""
        self.channel.agent_id = self.agent.id

        # Post as AI user
        self.channel.with_user(self.ai_user.id).message_post(
            body="<p>AI response</p>",
            message_type="comment",
        )

        # Should not be queued
        queued = self.messages.search(
            [
                ("channel_id", "=", self.channel.id),
                ("body", "=", "<p>AI response</p>"),
            ]
        )
        self.assertFalse(queued)

    def test_discuss_channel_auto_respect_disabled(self):
        """Test that auto-respect disabled prevents queuing."""
        self.channel.agent_id = self.agent.id
        self.channel.agent_auto_respond = False

        self.channel.message_post(
            body="<p>Should not queue</p>",
            message_type="comment",
        )

        queued = self.messages.search(
            [
                ("channel_id", "=", self.channel.id),
                ("body", "=", "<p>Should not queue</p>"),
            ]
        )
        self.assertFalse(queued)

    def test_webhook_format_body(self):
        """Plain text is wrapped and its URLs become links."""
        from ..controllers.agent_webhook import AgentWebhook

        webhook = AgentWebhook()

        result = str(webhook._format_body("Hello world"))
        self.assertIn("Hello world", result)
        self.assertTrue(result.startswith("<p>"))

        # No format means plain: an HTML tag is escaped, not interpreted.
        result = str(webhook._format_body("<p>Hello</p>"))
        self.assertIn("&lt;p&gt;Hello&lt;/p&gt;", result)

        # Explicit html keeps markup, sanitized.
        result = str(webhook._format_body("<p>Hello <b>there</b></p>", "html"))
        self.assertIn("<b>there</b>", result)

        # URL conversion
        result = str(webhook._format_body("Visit https://example.com"))
        self.assertIn('<a href="https://example.com"', result)

    def test_poll_claiming_is_delegated(self):
        """The poll endpoint relies on the atomic claim of the queue."""
        message = self.messages.create(
            {
                "channel_id": self.channel.id,
                "body": "<p>poll me</p>",
                "agent_id": self.agent.id,
            }
        )
        claimed = self.messages._claim_batch(self.agent)
        self.assertEqual(claimed, message)
        self.assertEqual(message.state, "processing")
