# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import timedelta

from odoo import api, fields, models


class AiBridgeAgentCredential(models.Model):
    """Short-lived hand-off of a user API key to the agent.

    Odoo cannot read an API key back (it stores a hash), so the only moment the
    plaintext exists is its creation. The user generates it from their own
    preferences; the row keeps it for a few minutes until the agent collects
    it, then the plaintext is wiped.
    """

    _name = "ai.bridge.agent.credential"
    _description = "AI agent User Credential Hand-off"
    _order = "id desc"

    agent_id = fields.Many2one(
        "ai.bridge.agent", required=True, ondelete="cascade", index=True
    )
    user_id = fields.Many2one(
        "res.users", required=True, ondelete="cascade", index=True
    )
    session_key = fields.Char(required=True, index=True)
    api_key = fields.Char(groups="base.group_system", copy=False)
    key_id = fields.Integer(help="Id of the res.users.apikeys row created.")
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("delivered", "Delivered"),
            ("expired", "Expired"),
        ],
        default="pending",
        required=True,
        index=True,
    )
    origin = fields.Selection(
        [("connect", "User connected"), ("renewal", "Automatic renewal")],
        default="connect",
        required=True,
        readonly=True,
        help="Why this hand-off exists: the user asked for it, or the cron "
        "renewed an expiring key they had already consented to.",
    )
    expires_at = fields.Datetime(required=True)
    delivered_at = fields.Datetime(readonly=True)

    def _key_expiry(self, credential):
        """Expiry of the API key itself, or None when it never expires."""
        if not credential.key_id or "res.users.apikeys" not in self.env:
            return None
        keys = self.env["res.users.apikeys"].sudo().browse(credential.key_id)
        if not keys.exists() or "expiration_date" not in keys._fields:
            return None
        expiry = keys.expiration_date
        return fields.Datetime.to_string(expiry) if expiry else None

    @api.model
    def _fetch_pending(self, agent):
        """Return and burn the credentials waiting for this agent."""
        now = fields.Datetime.now()
        self.search(
            [
                ("agent_id", "=", agent.id),
                ("state", "=", "pending"),
                ("expires_at", "<=", now),
            ]
        ).write({"state": "expired", "api_key": False})
        pending = self.search(
            [
                ("agent_id", "=", agent.id),
                ("state", "=", "pending"),
                ("expires_at", ">", now),
            ],
            order="id asc",
        )
        payload = []
        for credential in pending:
            payload.append(
                {
                    "user_id": credential.user_id.id,
                    "user_login": credential.user_id.login,
                    "session_key": credential.session_key,
                    "api_key": credential.api_key,
                    "key_id": credential.key_id,
                    # ``expires_at`` is when the hand-off must be collected: a
                    # few minutes. The key lives much longer, and the responder
                    # must judge the credential by the key, not by the envelope.
                    "expires_at": fields.Datetime.to_string(credential.expires_at),
                    "key_expires_at": self._key_expiry(credential),
                }
            )
            credential.write(
                {
                    "state": "delivered",
                    "delivered_at": now,
                    # the agent owns the secret from now on
                    "api_key": False,
                }
            )
        return payload

    @api.model
    def _cron_expire_credentials(self):
        """Drop the hand-offs nobody collected in time."""
        now = fields.Datetime.now()
        self.search([("state", "=", "pending"), ("expires_at", "<=", now)]).write(
            {"state": "expired", "api_key": False}
        )

    @api.model
    def _create_handoff(
        self, agent, user, api_key, key_id, ttl_minutes=10, origin="connect"
    ):
        return self.create(
            {
                "agent_id": agent.id,
                "user_id": user.id,
                "session_key": agent._get_session_key(user=user),
                "api_key": api_key,
                "key_id": key_id,
                "origin": origin,
                "expires_at": fields.Datetime.now() + timedelta(minutes=ttl_minutes),
            }
        )
