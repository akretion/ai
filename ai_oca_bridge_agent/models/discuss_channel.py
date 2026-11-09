# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import hashlib
import uuid
from datetime import timedelta

from odoo import _, fields, models


class DiscussChannel(models.Model):
    _inherit = "discuss.channel"

    agent_id = fields.Many2one(
        "ai.bridge.agent",
        string="AI agent",
        help="If set, messages in this channel will be forwarded to AI agent.",
    )
    agent_auto_respond = fields.Boolean(
        default=True,
        help="If checked, AI agent will automatically respond "
        "to messages in this channel.",
    )

    def _get_agent(self):
        """Find the AI agent for this channel.

        For channels with explicit agent set, return it.
        For DM chats with the AI user, find the first active agent.
        """
        self.ensure_one()
        if self.agent_id:
            return self.agent_id

        # For DM chats, check if one of the members is an AI user
        if self.channel_type == "chat":
            for member in self.sudo().channel_member_ids:
                if member.partner_id.user_ids.ai_bridge_id:
                    # Find a agent using this AI user
                    agent = (
                        self.env["ai.bridge.agent"]
                        .sudo()
                        .search(
                            [
                                ("ai_user_id", "in", member.partner_id.user_ids.ids),
                                ("active", "=", True),
                            ],
                            limit=1,
                        )
                    )
                    if agent:
                        return agent
        return False

    def _is_ai_mentioned(self, message, agent):
        """Check if the AI user is explicitly mentioned in the message.

        Returns True if the message partner_ids include the AI user's partner.
        This handles @mentions in Discuss channels.
        """
        if not message.partner_ids:
            return False
        return agent.ai_user_id.partner_id in message.partner_ids

    def _should_queue_for_agent(self, message, agent):
        """Determine whether this message should be queued for AI agent.

        Rules:
        - DM (chat): always respond (1-to-1 conversation)
        - Group/channel with explicit mention (@AI agent): respond
        - Group/channel without mention: only respond if auto-respond
          is enabled AND the channel is explicitly monitored
        """
        # DMs always get a response
        if self.channel_type == "chat":
            return True

        # If explicitly mentioned, always respond
        if self._is_ai_mentioned(message, agent):
            return True

        # In group channels without mention, only respond if:
        # - auto-respond is enabled
        # - the channel is explicitly in the agent's monitored channels
        if not self.agent_auto_respond:
            return False

        # If agent monitors specific channels, this one must be listed
        if agent.channel_ids and self not in agent.channel_ids:
            return False

        return True

    def _get_message_user(self, message):
        """Return the internal user behind the message author, if any."""
        return message.author_id.user_ids.filtered(lambda user: user.active)[:1]

    def _post_agent_access_denied(self, agent):
        """Answer once per cooldown that the user may not use AI agent."""
        body = agent.access_denied_message or _(
            "You are not allowed to use the AI assistant."
        )
        ai_user = agent.ai_user_id
        if not ai_user:
            return
        cooldown = agent.denial_cooldown_minutes or 0
        if cooldown:
            since = fields.Datetime.now() - timedelta(minutes=cooldown)
            already_answered = (
                self.env["mail.message"]
                .sudo()
                .search(
                    [
                        ("model", "=", "discuss.channel"),
                        ("res_id", "=", self.id),
                        ("author_id", "=", ai_user.partner_id.id),
                        ("body", "=", body),
                        ("date", ">=", fields.Datetime.to_string(since)),
                    ],
                    limit=1,
                )
            )
            if already_answered:
                return
        self.sudo().with_user(ai_user).with_context(
            mail_create_nosubscribe=True, mail_notrack=True
        ).message_post(
            body=body,
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )

    def message_post(self, **kwargs):
        """Override to queue messages for AI agent when a agent is configured."""
        message = super().message_post(**kwargs)

        # Skip if message is from an AI user (prevent loops)
        if message.author_id.user_ids.ai_bridge_id:
            return message

        # Check if this channel has a AI agent (direct or via DM)
        agent = self._get_agent()
        if not agent:
            return message

        # Validate agent is active and has an AI user
        if not agent.active or not agent.ai_user_id:
            return message

        # Decide whether to queue this message
        if not self._should_queue_for_agent(message, agent):
            return message

        user = self._get_message_user(message)
        if not agent._is_user_allowed(user):
            self._post_agent_access_denied(agent)
            return message

        # Queue the message for AI agent
        queued = (
            self.env["ai.bridge.agent.message"]
            .sudo()
            .create(
                {
                    "channel_id": self.id,
                    "body": message.body,
                    "author_id": message.author_id.id,
                    "agent_id": agent.id,
                    "user_id": user.id,
                    "user_has_api_key": bool(user.sudo().agent_has_api_key),
                    "session_key": agent._get_session_key(user=user, channel=self),
                    "company_id": self.env.company.id,
                    "db_hash": self._get_db_hash(),
                    "delivery_id": str(uuid.uuid4()),
                    "context_json": self.env["ai.bridge.user.context"]
                    .sudo()
                    ._get_for_user(user, self),
                    "mail_message_id": message.id,
                    "attachment_ids": [(6, 0, message.attachment_ids.ids)],
                }
            )
        )
        queued._schedule_push()

        # Trigger typing indicator on the AI user
        for member in self.sudo().channel_member_ids:
            if member.partner_id == agent.ai_user_id.partner_id:
                member._notify_typing(is_typing=True)

        return message

    def _get_db_hash(self):
        """Compute a hash identifying this database for AI agent session keys."""
        IrConfig = self.env["ir.config_parameter"].sudo()
        dbuuid = IrConfig.get_param("database.uuid")
        db_create_date = IrConfig.get_param("database.create_date")
        dbname = self.env.cr.dbname
        return hashlib.sha256(
            f"{dbuuid}:{db_create_date}:{dbname}".encode()
        ).hexdigest()[:16]
