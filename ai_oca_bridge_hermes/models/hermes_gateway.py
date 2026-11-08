# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import hashlib
import hmac
import json
import secrets
import time
import uuid
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.release import version as odoo_version


class HermesGateway(models.Model):
    _name = "hermes.gateway"
    _description = "Hermes Gateway Configuration"
    _inherit = ["mail.thread"]

    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company)
    odoo_url = fields.Char(
        string="Odoo Base URL",
        default=lambda self: (
            self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        ),
        help="The base URL of this Odoo instance that Hermes will connect to.",
    )
    webhook_token = fields.Char(
        default=lambda self: secrets.token_hex(32),
        groups="base.group_system",
        help="Secret token shared with the Hermes gateway for authentication.",
        readonly=True,
        copy=False,
    )
    hermes_url = fields.Char(
        string="Hermes URL",
        help="URL of the Hermes gateway (optional, for reference).",
    )
    channel_ids = fields.Many2many(
        "discuss.channel",
        string="Monitored Channels",
        help="Channels where Hermes will respond. Leave empty to monitor all channels.",
    )
    ai_user_id = fields.Many2one(
        "res.users",
        string="AI User",
        required=True,
        domain=[("ai_bridge_id", "!=", False)],
        help="The Odoo user that Hermes will post responses as.",
    )
    state = fields.Selection(
        [("draft", "Draft"), ("connected", "Connected"), ("error", "Error")],
        default="draft",
    )
    last_poll = fields.Datetime(readonly=True)
    message_count = fields.Integer(compute="_compute_message_count")
    mode = fields.Selection(
        [
            ("polling", "Polling (pull)"),
            ("webhook", "Webhook (push)"),
        ],
        default="polling",
        required=True,
        help="How Hermes connects to Odoo. "
        "Polling = Hermes polls Odoo. "
        "Webhook = Odoo pushes to Hermes.",
    )
    gateway_version = fields.Char(
        help="Version of the Hermes gateway script (for compatibility checks).",
    )
    last_error = fields.Text(readonly=True)
    last_error_date = fields.Datetime(readonly=True)
    tenant_key = fields.Char(
        default=lambda self: self._default_tenant_key(),
        readonly=True,
        copy=False,
        index=True,
        help="Stable identifier of this database inside a shared Hermes "
        "instance. Shared by every gateway of the database.",
    )
    environment = fields.Selection(
        [("prod", "Production"), ("staging", "Staging"), ("dev", "Development")],
        default="prod",
        required=True,
        help="Deployment stage. It is part of the Hermes session key, so a "
        "staging database restored from production never shares a "
        "conversation with it.",
    )
    session_scope = fields.Selection(
        [("user", "Per user"), ("channel", "Per channel")],
        default="user",
        required=True,
        help="Granularity of the persistent Hermes session. 'Per user' keeps "
        "one conversation per user across all their channels.",
    )
    push_enabled = fields.Boolean(
        help="Push new messages to the Hermes gateway in real time. When "
        "disabled, the gateway falls back to polling.",
    )
    push_url = fields.Char(
        help="Endpoint receiving the Odoo events, for instance "
        "https://hermes.example.com/odoo/events.",
    )
    push_secret = fields.Char(
        groups="base.group_system",
        default=lambda self: secrets.token_hex(32),
        copy=False,
        help="Secret used to sign the events pushed to the gateway.",
    )
    max_batch_size = fields.Integer(default=50)
    claim_ttl_minutes = fields.Integer(
        default=5,
        help="Delay after which a message claimed by a gateway but never "
        "answered is handed back to the queue.",
    )
    max_attempts = fields.Integer(default=5)
    long_poll_timeout = fields.Integer(
        default=0,
        help="Maximum duration in seconds of a long poll. 0 disables it.",
    )
    webhook_token_previous = fields.Char(
        groups="base.group_system",
        readonly=True,
        copy=False,
        help="Previous token, still accepted during the rotation grace period.",
    )
    token_rotated_at = fields.Datetime(readonly=True, copy=False)
    token_grace_minutes = fields.Integer(default=60)
    last_push = fields.Datetime(readonly=True)
    pending_count = fields.Integer(compute="_compute_pending_count")
    allowed_group_ids = fields.Many2many(
        "res.groups",
        default=lambda self: self._default_allowed_groups(),
        help="Groups allowed to chat with Hermes through this gateway. "
        "Leave empty to fall back on the 'Use Hermes AI' group.",
    )
    access_denied_message = fields.Text(
        translate=True,
        default=lambda self: _(
            "You are not allowed to use the Hermes AI assistant. Please ask "
            "your administrator to add you to the 'Use Hermes AI' group."
        ),
    )
    denial_cooldown_minutes = fields.Integer(
        default=30,
        help="Minimum delay between two access-denied answers in the same "
        "channel, to avoid flooding it.",
    )
    allow_user_connect = fields.Boolean(
        help="Let users hand their own Odoo API key over to the gateway, so the "
        "assistant can read Odoo with their own access rights.",
    )
    credential_ttl_days = fields.Integer(
        default=30,
        help="Validity of the API key a user hands over from their preferences.",
    )
    tool_access_mode = fields.Selection(
        [
            ("open", "Any model the user may read"),
            ("whitelist", "Only the Tools list"),
        ],
        default="open",
        required=True,
        help="'Any model the user may read' trusts the user's own API key and "
        "access rights: the assistant can read whatever that user can read in "
        "the UI, read methods only. 'Only the Tools list' restricts the "
        "assistant to the explicit grants below, which is stricter than the "
        "user's own rights.",
    )
    tool_excluded_model_ids = fields.Many2many(
        "ir.model",
        "hermes_gateway_excluded_model_rel",
        "gateway_id",
        "model_id",
        string="Models hidden from the assistant",
        help="Models the assistant may not query even when the user can read "
        "them, e.g. because answers are posted in a channel other people read.",
    )
    require_api_key_for_tools = fields.Boolean(
        default=True,
        help="Only tell the gateway that it may call Odoo on behalf of a user "
        "when that user owns an Odoo API key. Users without a key keep the "
        "generic assistant capabilities.",
    )

    @api.model
    def _default_tenant_key(self):
        """Return the tenant key of this database, creating it once."""
        params = self.env["ir.config_parameter"].sudo()
        key = params.get_param("hermes.tenant_key")
        if not key:
            key = secrets.token_hex(8)
            params.set_param("hermes.tenant_key", key)
        return key

    def _compute_message_count(self):
        queue = self.env["hermes.message.queue"]
        for gateway in self:
            gateway.message_count = queue.search_count(
                [("gateway_id", "=", gateway.id)]
            )

    def _compute_pending_count(self):
        queue = self.env["hermes.message.queue"]
        for gateway in self:
            gateway.pending_count = queue.search_count(
                [
                    ("gateway_id", "=", gateway.id),
                    ("state", "in", ("pending", "processing", "pushed")),
                ]
            )

    def _get_session_key(self, user=None, channel=None):
        """Build the Hermes session key for this tenant and scope."""
        self.ensure_one()
        if self.session_scope == "channel" or not user:
            scope = f"channel_{channel.id if channel else 0}"
        else:
            scope = f"user_{user.id}"
        return f"odoo:{self.tenant_key}:{self.environment}:{scope}"

    @api.model
    def _find_by_token(self, token):
        """Return the active gateway owning ``token`` (grace included)."""
        if not token:
            return self.browse()
        gateway = self.search(
            [
                ("active", "=", True),
                "|",
                ("webhook_token", "=", token),
                ("webhook_token_previous", "=", token),
            ],
            limit=1,
        )
        if gateway and gateway._is_token_valid(token):
            return gateway
        return self.browse()

    def _is_token_valid(self, token):
        """Check a webhook token, accepting the rotated one during its grace."""
        self.ensure_one()
        if not token:
            return False
        if self.webhook_token and secrets.compare_digest(self.webhook_token, token):
            return True
        previous = self.webhook_token_previous
        if not previous or not self.token_rotated_at:
            return False
        if not secrets.compare_digest(previous, token):
            return False
        deadline = self.token_rotated_at + timedelta(
            minutes=self.token_grace_minutes or 0
        )
        return fields.Datetime.now() <= deadline

    def _compute_signature(self, timestamp, body):
        """Sign an outgoing event body (HMAC-SHA256 over "<ts>.<body>")."""
        self.ensure_one()
        if isinstance(body, str):
            body = body.encode()
        payload = f"{timestamp}.".encode() + body
        secret = (self.push_secret or "").encode()
        return hmac.new(secret, payload, hashlib.sha256).hexdigest()

    @api.model
    def _default_allowed_groups(self):
        group = self.env.ref(
            "ai_oca_bridge_hermes.group_hermes_user", raise_if_not_found=False
        )
        return group

    def _is_user_allowed(self, user):
        """Tell whether ``user`` may chat with Hermes through this gateway."""
        self.ensure_one()
        if not user:
            return False
        # Administrators always keep access, otherwise a fresh install could
        # not be configured by the very person setting it up.
        if user.has_group("base.group_system"):
            return True
        if self.allowed_group_ids:
            return bool(self.allowed_group_ids & user.groups_id)
        return user.has_group("ai_oca_bridge_hermes.group_hermes_user")

    def _get_capabilities(self, message):
        """Describe what the gateway may do on behalf of the user.

        Extension point: child modules refine the tiers without touching the
        transport. The gateway keeps the actual policy (tools, prompts).
        """
        self.ensure_one()
        has_key = bool(message.user_has_api_key)
        return {
            "tier": "full" if has_key else "basic",
            "odoo_rpc": has_key or not self.require_api_key_for_tools,
            "tools": [],
        }

    def _get_tenant_info(self):
        """Public description of this Odoo instance inside a Hermes install."""
        self.ensure_one()
        return {
            "key": self.tenant_key,
            "name": self.name,
            "environment": self.environment,
            "odoo_url": self.odoo_url,
            "odoo_version": odoo_version,
        }

    def _build_event_payload(self, message):
        """Build the event pushed to the gateway for one queued message.

        Extension point: override to enrich the payload (record context,
        business hints, ...) without leaking that logic into the OCA module.
        """
        self.ensure_one()
        session_key = message.session_key or self._get_session_key(
            user=message.user_id, channel=message.channel_id
        )
        return {
            "tenant": self._get_tenant_info(),
            "event": "message.created",
            "gateway_id": self.id,
            "session_key": session_key,
            "delivery_id": message.delivery_id,
            "message": {
                "id": message.id,
                "queue_id": message.id,
                "channel_id": message.channel_id.id,
                "channel_name": message.channel_id.name,
                "user_id": message.user_id.id,
                "partner_id": message.author_id.id,
                "author_name": message.author_name,
                "body": message.body,
                "body_text": message.body_text,
                "create_date": fields.Datetime.to_string(message.create_date)
                if message.create_date
                else None,
                "history": message._get_history(),
            },
            "capabilities": self._get_capabilities(message),
            "reply_to": {
                "url": f"{(self.odoo_url or '').rstrip('/')}/hermes/reply",
                "auth": "hmac",
            },
        }

    def _get_push_headers(self, timestamp, body, delivery_id):
        self.ensure_one()
        return {
            "Content-Type": "application/json",
            "X-Hermes-Tenant": self.tenant_key or "",
            "X-Hermes-Timestamp": timestamp,
            "X-Hermes-Delivery": delivery_id or "",
            "X-Hermes-Signature": "sha256=" + self._compute_signature(timestamp, body),
        }

    def _push_message(self, message, timeout=3):
        """Push one queued message to the gateway, never raise."""
        self.ensure_one()
        if not self.push_enabled or not self.push_url:
            return False
        import requests

        delivery_id = message.delivery_id or str(uuid.uuid4())
        payload = self._build_event_payload(message)
        payload["delivery_id"] = delivery_id
        body = json.dumps(payload, default=str)
        timestamp = str(int(time.time()))
        message.write(
            {
                "delivery_id": delivery_id,
                "attempts": message.attempts + 1,
                "claimed_at": fields.Datetime.now(),
                "claimed_by": "push",
            }
        )
        try:
            response = requests.post(
                self.push_url,
                data=body.encode(),
                headers=self._get_push_headers(timestamp, body, delivery_id),
                timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001 - transport must not raise
            message._mark_error(str(exc))
            self.write(
                {"last_error": str(exc), "last_error_date": fields.Datetime.now()}
            )
            return False
        if response.status_code >= 400:
            message._mark_error(f"HTTP {response.status_code}")
            self.write(
                {
                    "last_error": f"HTTP {response.status_code}: {response.text[:500]}",
                    "last_error_date": fields.Datetime.now(),
                }
            )
            return False
        message._mark_pushed(response.status_code)
        self.write({"last_push": fields.Datetime.now(), "state": "connected"})
        return True

    def action_regenerate_token(self):
        """Rotate the webhook token, keeping the previous one during the grace."""
        for gateway in self:
            gateway.write(
                {
                    "webhook_token_previous": gateway.webhook_token,
                    "webhook_token": secrets.token_hex(32),
                    "token_rotated_at": fields.Datetime.now(),
                }
            )

    def action_test_connection(self):
        """Test connectivity by polling the gateway health endpoint."""
        self.ensure_one()
        if not self.hermes_url:
            return self._notify(_("No Hermes URL configured"), "warning")
        try:
            import requests

            resp = requests.get(
                f"{self.hermes_url}/health",
                timeout=5,
                headers={"Authorization": f"Bearer {self.webhook_token}"},
            )
            if resp.status_code == 200:
                self.state = "connected"
                return self._notify(_("Connection successful!"), "success")
            else:
                self.state = "error"
                return self._notify(f"HTTP {resp.status_code}", "error")
        except Exception as e:
            self.state = "error"
            return self._notify(str(e), "error")

    def _notify(self, message, type_="info"):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"title": "Hermes Gateway", "message": message, "type": type_},
        }
