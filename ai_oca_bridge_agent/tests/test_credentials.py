# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase


class TestHermesCredentials(TransactionCase):
    """User-consented API key hand-off to the gateway."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ResUsers = cls.env["res.users"]
        cls.Gateway = cls.env["hermes.gateway"]
        cls.Credential = cls.env["hermes.credential"]
        cls.Partner = cls.env["res.partner"]

        ai_partner = cls.Partner.create({"name": "Cred AI"})
        cls.ai_user = cls.ResUsers.create(
            {"name": "Cred AI", "login": "cred_ai", "partner_id": ai_partner.id}
        )
        bridge = cls.env["ai.bridge"].create({"name": "Cred Bridge", "usage": "hermes"})
        cls.ai_user.ai_bridge_id = bridge.id
        cls.gateway = cls.Gateway.create(
            {
                "name": "Cred Gateway",
                "ai_user_id": cls.ai_user.id,
                "allow_user_connect": True,
            }
        )
        group = cls.env.ref("ai_oca_bridge_agent.group_agent_user")
        cls.user = cls.ResUsers.create(
            {
                "name": "Cred User",
                "login": "cred_user",
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id, group.id])],
            }
        )

    def test_connect_creates_key_and_handoff(self):
        self.user.with_user(self.user).action_hermes_connect()
        self.assertTrue(self.user.hermes_credential_key_id)
        self.assertTrue(self.user.hermes_has_api_key)
        self.assertTrue(self.user.hermes_credential_expiry)
        handoff = self.Credential.search([("user_id", "=", self.user.id)])
        self.assertEqual(len(handoff), 1)
        self.assertEqual(handoff.state, "pending")
        self.assertTrue(handoff.api_key)
        self.assertEqual(
            handoff.session_key,
            self.gateway._get_session_key(user=self.user),
        )

    def test_connect_creates_a_real_api_key(self):
        self.user.with_user(self.user).action_hermes_connect()
        key = (
            self.env["res.users.apikeys"]
            .sudo()
            .browse(self.user.hermes_credential_key_id)
        )
        self.assertTrue(key.exists())
        self.assertEqual(key.user_id, self.user)
        self.assertEqual(key.scope, "rpc")

    def test_connect_is_idempotent(self):
        self.user.with_user(self.user).action_hermes_connect()
        key_id = self.user.hermes_credential_key_id
        self.user.with_user(self.user).action_hermes_connect()
        self.assertEqual(self.user.hermes_credential_key_id, key_id)
        self.assertEqual(
            self.Credential.search_count([("user_id", "=", self.user.id)]), 1
        )

    def test_fetch_pending_returns_then_wipes_the_secret(self):
        self.user.with_user(self.user).action_hermes_connect()
        handoff = self.Credential.search([("user_id", "=", self.user.id)])
        payload = self.Credential._fetch_pending(self.gateway)
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["user_id"], self.user.id)
        self.assertTrue(payload[0]["api_key"])
        self.assertEqual(handoff.state, "delivered")
        self.assertFalse(handoff.api_key)
        self.assertEqual(self.Credential._fetch_pending(self.gateway), [])

    def test_fetch_is_scoped_per_gateway(self):
        other = self.Gateway.create(
            {"name": "Other Gateway", "ai_user_id": self.ai_user.id}
        )
        self.user.with_user(self.user).action_hermes_connect()
        self.assertEqual(self.Credential._fetch_pending(other), [])
        self.assertEqual(len(self.Credential._fetch_pending(self.gateway)), 1)

    def test_expired_handoff_is_not_delivered(self):
        self.user.with_user(self.user).action_hermes_connect()
        handoff = self.Credential.search([("user_id", "=", self.user.id)])
        handoff.expires_at = fields.Datetime.now() - timedelta(minutes=1)
        self.assertEqual(self.Credential._fetch_pending(self.gateway), [])
        self.assertEqual(handoff.state, "expired")
        self.assertFalse(handoff.api_key)

    def test_disconnect_revokes_the_key(self):
        self.user.with_user(self.user).action_hermes_connect()
        key_id = self.user.hermes_credential_key_id
        self.user.with_user(self.user).action_hermes_disconnect()
        self.assertFalse(self.env["res.users.apikeys"].sudo().browse(key_id).exists())
        self.assertFalse(self.user.hermes_has_api_key)
        self.assertFalse(self.user.hermes_credential_key_id)

    def test_connect_refused_without_the_group(self):
        outsider = self.ResUsers.create({"name": "No Group", "login": "cred_outsider"})
        with self.assertRaises(UserError):
            outsider.with_user(outsider).action_hermes_connect()

    def test_connect_refused_when_gateway_disallows(self):
        self.gateway.allow_user_connect = False
        with self.assertRaises(UserError):
            self.user.with_user(self.user).action_hermes_connect()

    def test_gateway_flag_gates_the_capability(self):
        self.user.with_user(self.user).action_hermes_connect()
        queue = self.env["hermes.message.queue"].create(
            {
                "channel_id": self.env["discuss.channel"]
                .create({"name": "cred", "channel_type": "channel"})
                .id,
                "body": "hello",
                "gateway_id": self.gateway.id,
                "user_id": self.user.id,
                "user_has_api_key": self.user.sudo().hermes_has_api_key,
            }
        )
        capabilities = self.gateway._get_capabilities(queue)
        self.assertTrue(capabilities["odoo_rpc"])
        self.assertEqual(capabilities["tier"], "full")
