# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging
from datetime import datetime, timedelta
from datetime import time as dtime

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


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
    agent_credential_consent_at = fields.Datetime(
        readonly=True,
        copy=False,
        help="When the user accepted that the assistant may read their "
        "records with a dedicated key. Cleared on Disconnect, and the only "
        "thing that allows the key to be renewed automatically.",
    )

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

    def _agent_create_key(self, agent, origin="connect"):
        """Create the dedicated rpc key and hand it to the agent."""
        self.ensure_one()
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
            agent, self, api_key, key.id, origin=origin
        )
        return key, expiry

    def _agent_renew_key(self, agent, force=False):
        """Replace our own key with a fresh one, if the consent still holds.

        Consent is the whole justification: the user opted in once, the key is
        still ``rpc``-scoped, still theirs, still revoked by Disconnect, and
        this only ever touches the key *we* created for that agent.
        """
        self.ensure_one()
        key_id = self.agent_credential_key_id
        key = self.env["res.users.apikeys"].sudo().browse(key_id) if key_id else None
        if key and key.exists():
            created = key.create_date
            if (
                not force
                and created
                and fields.Datetime.now() - created < timedelta(hours=12)
            ):
                # A loop of renewals would be worse than an expiring key.
                return False
            key.unlink()
        self._agent_create_key(agent, origin="renewal")
        _logger.info("AI agent key renewed for user %s (agent %s)", self.id, agent.name)
        self._agent_notify_last_channel(
            agent,
            _(
                "Your Odoo access for %(agent)s was renewed until %(date)s. "
                "You can revoke it any time from your preferences.",
                agent=agent.name,
                date=self.agent_credential_expiry,
            ),
        )
        return True

    def _agent_revoke_idle_key(self, agent):
        """Drop the key and the consent of a user who stopped talking."""
        self.ensure_one()
        key_id = self.agent_credential_key_id
        if key_id:
            key = self.env["res.users.apikeys"].sudo().browse(key_id)
            if key.exists():
                key.unlink()
        self.sudo().write(
            {
                "agent_credential_key_id": False,
                "agent_credential_expiry": False,
                "agent_credential_consent_at": False,
            }
        )
        _logger.info(
            "AI agent key revoked after %s days of inactivity for user %s",
            agent.credential_idle_days,
            self.id,
        )
        self._agent_notify_last_channel(
            agent,
            _(
                "Your Odoo access for %(agent)s expired after %(days)s days "
                "without use. Open your preferences and connect again when you "
                "need the assistant.",
                agent=agent.name,
                days=agent.credential_idle_days,
            ),
        )

    def _agent_notify_last_channel(self, agent, body):
        """Tell the user in the last thread they used, not in a new one."""
        self.ensure_one()
        queued = (
            self.env["ai.bridge.agent.message"]
            .sudo()
            .search(
                [("user_id", "=", self.id), ("agent_id", "=", agent.id)],
                order="id desc",
                limit=1,
            )
        )
        channel = queued.channel_id
        if not channel or not agent.ai_user_id:
            return
        channel.with_user(agent.ai_user_id).with_context(
            mail_create_nosubscribe=True, mail_notrack=True
        ).message_post(
            body=body, message_type="notification", subtype_xmlid="mail.mt_note"
        )

    def _agent_last_activity(self, agent):
        """When this user last spoke to the agent, or None."""
        queued = (
            self.env["ai.bridge.agent.message"]
            .sudo()
            .search(
                [("user_id", "=", self.id), ("agent_id", "=", agent.id)],
                order="create_date desc",
                limit=1,
            )
        )
        return queued.create_date

    @api.model
    def _cron_renew_agent_keys(self):
        """Renew the key of consenting users shortly before it expires."""
        agents = (
            self.env["ai.bridge.agent"]
            .sudo()
            .search(
                [
                    ("credential_auto_renew", "=", True),
                    ("allow_user_connect", "=", True),
                ]
            )
        )
        for agent in agents:
            horizon = fields.Date.add(
                fields.Date.context_today(self),
                days=agent.credential_renew_before_days or 7,
            )
            candidates = self.sudo().search(
                [
                    ("agent_credential_consent_at", "!=", False),
                    ("agent_credential_key_id", "!=", False),
                    ("active", "=", True),
                ]
            )
            for user in candidates:
                if not agent._is_user_allowed(user):
                    continue
                idle_days = agent.credential_idle_days or 0
                last_seen = user._agent_last_activity(agent)
                if idle_days and (
                    not last_seen
                    or last_seen < fields.Datetime.now() - timedelta(days=idle_days)
                ):
                    # Renewal follows use, not habit: a dormant account keeps no
                    # live key, and the user simply reconnects when they return.
                    user._agent_revoke_idle_key(agent)
                    continue
                expiry = user.agent_credential_expiry
                if expiry and expiry > horizon:
                    continue
                pending = (
                    self.env["ai.bridge.agent.credential"]
                    .sudo()
                    .search_count(
                        [
                            ("user_id", "=", user.id),
                            ("agent_id", "=", agent.id),
                            ("state", "=", "pending"),
                        ]
                    )
                )
                if pending:
                    continue
                user._agent_renew_key(agent)

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
        self._agent_create_key(agent)
        self.sudo().write({"agent_credential_consent_at": fields.Datetime.now()})
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
            {
                "agent_credential_key_id": False,
                "agent_credential_expiry": False,
                "agent_credential_consent_at": False,
            }
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
