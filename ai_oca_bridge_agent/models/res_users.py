# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import datetime
from datetime import time as dtime

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ResUsers(models.Model):
    _inherit = "res.users"

    agent_ids = fields.Many2many(
        "ai.bridge.agent",
        string="AI agents",
        help="AI agents this user is linked to.",
    )
    agent_has_api_key = fields.Boolean(
        compute="_compute_agent_has_api_key",
        string="AI agent: Odoo API Key",
        help="Checked when an API key has been handed over to the AI agent "
        "agent, so the assistant may call Odoo on this user's behalf.",
    )
    agent_credential_key_id = fields.Integer(
        readonly=True, copy=False, help="res.users.apikeys row handed to AI agent."
    )
    agent_credential_expiry = fields.Date(readonly=True, copy=False)

    @api.depends("agent_credential_key_id", "agent_credential_expiry")
    def _compute_agent_has_api_key(self):
        keys = self.env["res.users.apikeys"].sudo()
        today = fields.Date.context_today(self)
        for user in self:
            key_id = user.agent_credential_key_id
            alive = key_id and keys.browse(key_id).exists()
            expiry = user.agent_credential_expiry
            user.agent_has_api_key = bool(alive) and (not expiry or expiry >= today)

    def _get_agent_connect_gateway(self):
        agent = (
            self.env["ai.bridge.agent"]
            .sudo()
            .search(
                [("allow_user_connect", "=", True), ("active", "=", True)],
                limit=1,
            )
        )
        if not agent:
            raise UserError(_("No AI agent is configured to accept user connections."))
        if not agent._is_user_allowed(self):
            raise UserError(
                _(
                    "You are not allowed to use the %(agent)s assistant.",
                    agent=agent.name,
                )
            )
        return agent

    def action_agent_connect(self):
        """Create a dedicated API key for this user and hand it to the agent."""
        self.ensure_one()
        agent = self._get_agent_connect_gateway()
        if self.agent_has_api_key:
            return self._agent_notification(
                _(
                    "Your Odoo account is already connected to %(agent)s.",
                    agent=agent.name,
                ),
                "info",
            )
        ttl_days = agent.credential_ttl_days or 30
        key_name = f"AI agent {agent.name}"
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
                "agent_credential_key_id": key.id,
                "agent_credential_expiry": expiry,
            }
        )
        self.env["ai.bridge.agent.credential"].sudo()._create_handoff(
            agent, self, api_key, key.id
        )
        return self._agent_notification(
            _(
                "Connected to %(agent)s. The assistant can now read your "
                "records with your own access rights for %(days)s days.",
                agent=agent.name,
                days=ttl_days,
            ),
            "success",
        )

    def action_agent_disconnect(self):
        """Revoke the key handed to the agent."""
        self.ensure_one()
        key_id = self.agent_credential_key_id
        if key_id:
            key = self.env["res.users.apikeys"].sudo().browse(key_id)
            if key.exists():
                key.unlink()
        self.sudo().write(
            {"agent_credential_key_id": False, "agent_credential_expiry": False}
        )
        self.env["ai.bridge.agent.credential"].sudo().search(
            [("user_id", "=", self.id), ("state", "=", "pending")]
        ).write({"state": "expired", "api_key": False})
        return self._agent_notification(
            _("Disconnected from the AI agent assistant."), "success"
        )

    def _agent_notification(self, message, notif_type):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("AI assistant"),
                "message": message,
                "type": notif_type,
                "sticky": False,
            },
        }
