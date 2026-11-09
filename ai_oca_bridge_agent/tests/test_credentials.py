# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase


class TestAgentCredentials(TransactionCase):
    """User-consented API key hand-off to the agent."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ResUsers = cls.env["res.users"]
        cls.Agent = cls.env["ai.bridge.agent"]
        cls.Credential = cls.env["ai.bridge.agent.credential"]
        cls.Partner = cls.env["res.partner"]

        ai_partner = cls.Partner.create({"name": "Cred AI"})
        cls.ai_user = cls.ResUsers.create(
            {"name": "Cred AI", "login": "cred_ai", "partner_id": ai_partner.id}
        )
        bridge = cls.env["ai.bridge"].create({"name": "Cred Bridge", "usage": "agent"})
        cls.ai_user.ai_bridge_id = bridge.id
        cls.agent = cls.Agent.create(
            {
                "name": "Cred Agent",
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
        self.user.with_user(self.user).action_agent_connect()
        self.assertTrue(self.user.agent_credential_key_id)
        self.assertTrue(self.user.agent_has_api_key)
        self.assertTrue(self.user.agent_credential_expiry)
        handoff = self.Credential.search([("user_id", "=", self.user.id)])
        self.assertEqual(len(handoff), 1)
        self.assertEqual(handoff.state, "pending")
        self.assertTrue(handoff.api_key)
        self.assertEqual(
            handoff.session_key,
            self.agent._get_session_key(user=self.user),
        )

    def test_connect_creates_a_real_api_key(self):
        self.user.with_user(self.user).action_agent_connect()
        key = (
            self.env["res.users.apikeys"]
            .sudo()
            .browse(self.user.agent_credential_key_id)
        )
        self.assertTrue(key.exists())
        self.assertEqual(key.user_id, self.user)
        self.assertEqual(key.scope, "rpc")

    def test_connect_is_idempotent(self):
        self.user.with_user(self.user).action_agent_connect()
        key_id = self.user.agent_credential_key_id
        self.user.with_user(self.user).action_agent_connect()
        self.assertEqual(self.user.agent_credential_key_id, key_id)
        self.assertEqual(
            self.Credential.search_count([("user_id", "=", self.user.id)]), 1
        )

    def test_fetch_pending_returns_then_wipes_the_secret(self):
        self.user.with_user(self.user).action_agent_connect()
        handoff = self.Credential.search([("user_id", "=", self.user.id)])
        payload = self.Credential._fetch_pending(self.agent)
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["user_id"], self.user.id)
        self.assertTrue(payload[0]["api_key"])
        self.assertEqual(handoff.state, "delivered")
        self.assertFalse(handoff.api_key)
        self.assertEqual(self.Credential._fetch_pending(self.agent), [])

    def test_fetch_is_scoped_per_gateway(self):
        other = self.Agent.create(
            {"name": "Other Agent", "ai_user_id": self.ai_user.id}
        )
        self.user.with_user(self.user).action_agent_connect()
        self.assertEqual(self.Credential._fetch_pending(other), [])
        self.assertEqual(len(self.Credential._fetch_pending(self.agent)), 1)

    def test_expired_handoff_is_not_delivered(self):
        self.user.with_user(self.user).action_agent_connect()
        handoff = self.Credential.search([("user_id", "=", self.user.id)])
        handoff.expires_at = fields.Datetime.now() - timedelta(minutes=1)
        self.assertEqual(self.Credential._fetch_pending(self.agent), [])
        self.assertEqual(handoff.state, "expired")
        self.assertFalse(handoff.api_key)

    def test_disconnect_revokes_the_key(self):
        self.user.with_user(self.user).action_agent_connect()
        key_id = self.user.agent_credential_key_id
        self.user.with_user(self.user).action_agent_disconnect()
        self.assertFalse(self.env["res.users.apikeys"].sudo().browse(key_id).exists())
        self.assertFalse(self.user.agent_has_api_key)
        self.assertFalse(self.user.agent_credential_key_id)

    def test_connect_refused_without_the_group(self):
        outsider = self.ResUsers.create({"name": "No Group", "login": "cred_outsider"})
        with self.assertRaises(UserError):
            outsider.with_user(outsider).action_agent_connect()

    def test_connect_refused_when_gateway_disallows(self):
        self.agent.allow_user_connect = False
        with self.assertRaises(UserError):
            self.user.with_user(self.user).action_agent_connect()

    def test_gateway_flag_gates_the_capability(self):
        self.user.with_user(self.user).action_agent_connect()
        queue = self.env["ai.bridge.agent.message"].create(
            {
                "channel_id": self.env["discuss.channel"]
                .create({"name": "cred", "channel_type": "channel"})
                .id,
                "body": "hello",
                "agent_id": self.agent.id,
                "user_id": self.user.id,
                "user_has_api_key": self.user.sudo().agent_has_api_key,
            }
        )
        capabilities = self.agent._get_capabilities(queue)
        self.assertTrue(capabilities["odoo_rpc"])
        self.assertEqual(capabilities["tier"], "full")

    def test_payload_carries_the_key_expiry_not_the_handoff_ttl(self):
        """The responder must judge the key by its own expiry.

        The hand-off is collected within minutes; the key lives for weeks. Sending
        only ``expires_at`` made every credential look dead after ten minutes.
        """
        admin = self.env.ref("base.user_admin")
        admin.action_agent_connect()
        credential = self.env["ai.bridge.agent.credential"].search(
            [("user_id", "=", admin.id)], order="id desc", limit=1
        )
        payload = credential._fetch_pending(self.agent)[0]
        self.assertIn("key_expires_at", payload)
        key = self.env["res.users.apikeys"].sudo().browse(credential.key_id)
        if "expiration_date" in key._fields and key.expiration_date:
            self.assertTrue(payload["key_expires_at"])
            self.assertNotEqual(payload["key_expires_at"], payload["expires_at"])

    def test_key_is_renewed_before_it_expires(self):
        """A consenting user keeps working without clicking Connect again."""
        user = self.ResUsers.create(
            {
                "name": "Renew me",
                "login": "renew_me",
                "email": "renew_me@example.com",
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
        self.agent.allow_user_connect = True
        user.action_agent_connect()
        old_key = user.agent_credential_key_id
        self.assertTrue(user.agent_credential_consent_at)
        # Pretend the key is about to expire, and force past the 12h guard.
        user.write(
            {
                "agent_credential_expiry": fields.Date.add(
                    fields.Date.context_today(user), days=1
                )
            }
        )
        key = self.env["res.users.apikeys"].sudo().browse(old_key)
        key.write({"create_date": fields.Datetime.now() - timedelta(days=2)})
        # The first hand-off is still waiting for the responder: mark it
        # collected, otherwise the renewal would stack a second one.
        self.Credential.search([("user_id", "=", user.id)]).write(
            {"state": "delivered"}
        )
        self.env["res.users"]._cron_renew_agent_keys()
        user.invalidate_recordset()
        self.assertNotEqual(user.agent_credential_key_id, old_key)
        self.assertFalse(self.env["res.users.apikeys"].sudo().browse(old_key).exists())
        handoff = self.Credential.search(
            [("user_id", "=", user.id)], order="id desc", limit=1
        )
        self.assertEqual(handoff.state, "pending")

    def test_no_renewal_without_consent(self):
        before = self.env["res.users.apikeys"].sudo().search_count([])
        self.agent.allow_user_connect = True
        self.env["res.users"]._cron_renew_agent_keys()
        self.assertEqual(self.env["res.users.apikeys"].sudo().search_count([]), before)

    def test_disconnect_clears_the_consent(self):
        user = self.ResUsers.create(
            {
                "name": "Disconnect me",
                "login": "disconnect_me",
                "email": "disconnect_me@example.com",
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
        self.agent.allow_user_connect = True
        user.action_agent_connect()
        user.action_agent_disconnect()
        self.assertFalse(user.agent_credential_consent_at)
        self.assertFalse(user.agent_credential_key_id)
