# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import datetime
from datetime import time as dtime

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ResUsers(models.Model):
    _inherit = "res.users"

    hermes_gateway_ids = fields.Many2many(
        "hermes.gateway",
        string="Hermes Gateways",
        help="Hermes gateways this user is linked to.",
    )
    hermes_has_api_key = fields.Boolean(
        compute="_compute_hermes_has_api_key",
        string="Hermes: Odoo API Key",
        help="Checked when an API key has been handed over to the Hermes "
        "gateway, so the assistant may call Odoo on this user's behalf.",
    )
    hermes_credential_key_id = fields.Integer(
        readonly=True, copy=False, help="res.users.apikeys row handed to Hermes."
    )
    hermes_credential_expiry = fields.Date(readonly=True, copy=False)

    @api.depends("hermes_credential_key_id", "hermes_credential_expiry")
    def _compute_hermes_has_api_key(self):
        keys = self.env["res.users.apikeys"].sudo()
        today = fields.Date.context_today(self)
        for user in self:
            key_id = user.hermes_credential_key_id
            alive = key_id and keys.browse(key_id).exists()
            expiry = user.hermes_credential_expiry
            user.hermes_has_api_key = bool(alive) and (not expiry or expiry >= today)

    def _get_hermes_connect_gateway(self):
        gateway = (
            self.env["hermes.gateway"]
            .sudo()
            .search(
                [("allow_user_connect", "=", True), ("active", "=", True)],
                limit=1,
            )
        )
        if not gateway:
            raise UserError(
                _("No Hermes gateway is configured to accept user connections.")
            )
        if not gateway._is_user_allowed(self):
            raise UserError(
                _(
                    "You are not allowed to use the %(gateway)s assistant.",
                    gateway=gateway.name,
                )
            )
        return gateway

    def action_hermes_connect(self):
        """Create a dedicated API key for this user and hand it to the gateway."""
        self.ensure_one()
        gateway = self._get_hermes_connect_gateway()
        if self.hermes_has_api_key:
            return self._hermes_notification(
                _(
                    "Your Odoo account is already connected to %(gateway)s.",
                    gateway=gateway.name,
                ),
                "info",
            )
        ttl_days = gateway.credential_ttl_days or 30
        key_name = f"Hermes {gateway.name}"
        expiry = fields.Date.add(fields.Date.context_today(self), days=ttl_days)
        expiry_dt = datetime.combine(expiry, dtime(23, 59, 59))
        api_key = (
            self.env["res.users.apikeys"]
            .with_user(self)
            .sudo()
            ._generate("rpc", key_name, expiry_dt)
        )
        key = (
            self.env["res.users.apikeys"]
            .sudo()
            .search(
                [("user_id", "=", self.id), ("name", "=", key_name)],
                order="id desc",
                limit=1,
            )
        )
        self.sudo().write(
            {
                "hermes_credential_key_id": key.id,
                "hermes_credential_expiry": expiry,
            }
        )
        self.env["hermes.credential"].sudo()._create_handoff(
            gateway, self, api_key, key.id
        )
        return self._hermes_notification(
            _(
                "Connected to %(gateway)s. The assistant can now read your "
                "records with your own access rights for %(days)s days.",
                gateway=gateway.name,
                days=ttl_days,
            ),
            "success",
        )

    def action_hermes_disconnect(self):
        """Revoke the key handed to the gateway."""
        self.ensure_one()
        key_id = self.hermes_credential_key_id
        if key_id:
            key = self.env["res.users.apikeys"].sudo().browse(key_id)
            if key.exists():
                key.unlink()
        self.sudo().write(
            {"hermes_credential_key_id": False, "hermes_credential_expiry": False}
        )
        self.env["hermes.credential"].sudo().search(
            [("user_id", "=", self.id), ("state", "=", "pending")]
        ).write({"state": "expired", "api_key": False})
        return self._hermes_notification(
            _("Disconnected from the Hermes assistant."), "success"
        )

    def _hermes_notification(self, message, notif_type):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Hermes AI"),
                "message": message,
                "type": notif_type,
                "sticky": False,
            },
        }
