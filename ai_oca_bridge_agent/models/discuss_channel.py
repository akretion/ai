# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import hashlib
import uuid
from datetime import timedelta

from odoo import _, fields, models


class DiscussChannel(models.Model):
    _inherit = "discuss.channel"

    hermes_gateway_id = fields.Many2one(
        "hermes.gateway",
        string="Hermes Gateway",
        help="If set, messages in this channel will be forwarded to Hermes.",
    )
    hermes_auto_respond = fields.Boolean(
        default=True,
        help="If checked, Hermes will automatically respond "
        "to messages in this channel.",
    )

    def _get_hermes_gateway(self):
        """Find the Hermes gateway for this channel.

        For channels with explicit gateway set, return it.
        For DM chats with Hermes AI user, find the first active gateway.
        """
        self.ensure_one()
        if self.hermes_gateway_id:
            return self.hermes_gateway_id

        # For DM chats, check if one of the members is an AI user
        if self.channel_type == "chat":
            for member in self.sudo().channel_member_ids:
                if member.partner_id.user_ids.ai_bridge_id:
                    # Find a gateway using this AI user
                    gateway = (
                        self.env["hermes.gateway"]
                        .sudo()
                        .search(
                            [
                                ("ai_user_id", "in", member.partner_id.user_ids.ids),
                                ("active", "=", True),
                            ],
                            limit=1,
                        )
                    )
                    if gateway:
                        return gateway
        return False

    def _is_ai_mentioned(self, message, gateway):
        """Check if the AI user is explicitly mentioned in the message.

        Returns True if the message partner_ids include the AI user's partner.
        This handles @mentions in Discuss channels.
        """
        if not message.partner_ids:
            return False
        return gateway.ai_user_id.partner_id in message.partner_ids

    def _should_queue_for_hermes(self, message, gateway):
        """Determine whether this message should be queued for Hermes.

        Rules:
        - DM (chat): always respond (1-to-1 conversation)
        - Group/channel with explicit mention (@Hermes): respond
        - Group/channel without mention: only respond if auto-respond
          is enabled AND the channel is explicitly monitored
        """
        # DMs always get a response
        if self.channel_type == "chat":
            return True

        # If explicitly mentioned, always respond
        if self._is_ai_mentioned(message, gateway):
            return True

        # In group channels without mention, only respond if:
        # - auto-respond is enabled
        # - the channel is explicitly in the gateway's monitored channels
        if not self.hermes_auto_respond:
            return False

        # If gateway monitors specific channels, this one must be listed
        if gateway.channel_ids and self not in gateway.channel_ids:
            return False

        return True

    def _get_message_user(self, message):
        """Return the internal user behind the message author, if any."""
        return message.author_id.user_ids.filtered(lambda user: user.active)[:1]

    def _post_hermes_access_denied(self, gateway):
        """Answer once per cooldown that the user may not use Hermes."""
        body = gateway.access_denied_message or _(
            "You are not allowed to use the Hermes AI assistant."
        )
        ai_user = gateway.ai_user_id
        if not ai_user:
            return
        cooldown = gateway.denial_cooldown_minutes or 0
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
        """Override to queue messages for Hermes when a gateway is configured."""
        message = super().message_post(**kwargs)

        # Skip if message is from an AI user (prevent loops)
        if message.author_id.user_ids.ai_bridge_id:
            return message

        # Check if this channel has a Hermes gateway (direct or via DM)
        gateway = self._get_hermes_gateway()
        if not gateway:
            return message

        # Validate gateway is active and has an AI user
        if not gateway.active or not gateway.ai_user_id:
            return message

        # Decide whether to queue this message
        if not self._should_queue_for_hermes(message, gateway):
            return message

        user = self._get_message_user(message)
        if not gateway._is_user_allowed(user):
            self._post_hermes_access_denied(gateway)
            return message

        # Queue the message for Hermes
        queued = (
            self.env["hermes.message.queue"]
            .sudo()
            .create(
                {
                    "channel_id": self.id,
                    "body": message.body,
                    "author_id": message.author_id.id,
                    "gateway_id": gateway.id,
                    "user_id": user.id,
                    "user_has_api_key": bool(user.sudo().hermes_has_api_key),
                    "session_key": gateway._get_session_key(user=user, channel=self),
                    "company_id": self.env.company.id,
                    "db_hash": self._get_db_hash(),
                    "delivery_id": str(uuid.uuid4()),
                }
            )
        )
        queued._schedule_push()

        # Trigger typing indicator on the AI user
        for member in self.sudo().channel_member_ids:
            if member.partner_id == gateway.ai_user_id.partner_id:
                member._notify_typing(is_typing=True)

        return message

    def _get_db_hash(self):
        """Compute a hash identifying this database for Hermes session keys."""
        IrConfig = self.env["ir.config_parameter"].sudo()
        dbuuid = IrConfig.get_param("database.uuid")
        db_create_date = IrConfig.get_param("database.create_date")
        dbname = self.env.cr.dbname
        return hashlib.sha256(
            f"{dbuuid}:{db_create_date}:{dbname}".encode()
        ).hexdigest()[:16]
