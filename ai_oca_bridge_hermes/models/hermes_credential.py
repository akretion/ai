# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import timedelta

from odoo import api, fields, models


class HermesCredential(models.Model):
    """Short-lived hand-off of a user API key to the gateway.

    Odoo cannot read an API key back (it stores a hash), so the only moment the
    plaintext exists is its creation. The user generates it from their own
    preferences; the row keeps it for a few minutes until the gateway collects
    it, then the plaintext is wiped.
    """

    _name = "hermes.credential"
    _description = "Hermes User Credential Hand-off"
    _order = "id desc"

    gateway_id = fields.Many2one(
        "hermes.gateway", required=True, ondelete="cascade", index=True
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
    expires_at = fields.Datetime(required=True)
    delivered_at = fields.Datetime(readonly=True)

    @api.model
    def _fetch_pending(self, gateway):
        """Return and burn the credentials waiting for this gateway."""
        now = fields.Datetime.now()
        self.search(
            [
                ("gateway_id", "=", gateway.id),
                ("state", "=", "pending"),
                ("expires_at", "<=", now),
            ]
        ).write({"state": "expired", "api_key": False})
        pending = self.search(
            [
                ("gateway_id", "=", gateway.id),
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
                    "expires_at": fields.Datetime.to_string(credential.expires_at),
                }
            )
            credential.write(
                {
                    "state": "delivered",
                    "delivered_at": now,
                    # the gateway owns the secret from now on
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
    def _create_handoff(self, gateway, user, api_key, key_id, ttl_minutes=10):
        return self.create(
            {
                "gateway_id": gateway.id,
                "user_id": user.id,
                "session_key": gateway._get_session_key(user=user),
                "api_key": api_key,
                "key_id": key_id,
                "expires_at": fields.Datetime.now() + timedelta(minutes=ttl_minutes),
            }
        )
