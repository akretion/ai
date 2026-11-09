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
from odoo.tools.mail import html2plaintext


class AiBridgeAgent(models.Model):
    _name = "ai.bridge.agent"
    _description = "AI agent Configuration"
    _inherit = ["mail.thread"]

    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company)
    odoo_url = fields.Char(
        string="Odoo Base URL",
        default=lambda self: (
            self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        ),
        help="The base URL of this Odoo instance that AI agent will connect to.",
    )
    webhook_token = fields.Char(
        default=lambda self: secrets.token_hex(32),
        groups="base.group_system",
        help="Secret token shared with the AI agent for authentication.",
        readonly=True,
        copy=False,
    )
    agent_url = fields.Char(
        string="AI agent URL",
        help="URL of the AI agent (optional, for reference).",
    )
    channel_ids = fields.Many2many(
        "discuss.channel",
        string="Monitored Channels",
        help="Channels where the assistant answers. Leave empty to monitor "
        "every channel.",
    )
    ai_user_id = fields.Many2one(
        "res.users",
        string="AI User",
        required=True,
        domain=[("ai_bridge_id", "!=", False)],
        help="The Odoo user that AI agent will post responses as.",
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
        help="How AI agent connects to Odoo. "
        "Polling = AI agent polls Odoo. "
        "Webhook = Odoo pushes to AI agent.",
    )
    agent_version = fields.Char(
        help="Version of the AI agent script (for compatibility checks).",
    )
    last_error = fields.Text(readonly=True)
    last_error_date = fields.Datetime(readonly=True)
    tenant_key = fields.Char(
        default=lambda self: self._default_tenant_key(),
        readonly=True,
        copy=False,
        index=True,
        help="Stable identifier of this database inside a shared AI agent "
        "instance. Shared by every agent of the database.",
    )
    environment = fields.Selection(
        [("prod", "Production"), ("staging", "Staging"), ("dev", "Development")],
        default="prod",
        required=True,
        help="Deployment stage. It is part of the AI agent session key, so a "
        "staging database restored from production never shares a "
        "conversation with it.",
    )
    session_scope = fields.Selection(
        [("channel", "Per conversation"), ("user", "Per user")],
        default="channel",
        required=True,
        help="Granularity of the persistent AI agent session. 'Per "
        "conversation' gives each Discuss thread its own memory, like the "
        "sessions OCA ai_agent ties to a channel: a new thread is a new "
        "conversation, and nothing has to be reset. 'Per user' keeps one "
        "memory per person across all their threads.",
    )
    credential_auto_renew = fields.Boolean(
        default=True,
        help="Renew the API key of consenting users before it expires, so an "
        "active conversation never stops working. Disconnect still revokes.",
    )
    credential_renew_before_days = fields.Integer(
        default=7,
        help="Renew a key this many days before it expires.",
    )
    skill_loading = fields.Selection(
        [("inline", "In the prompt"), ("lazy", "On demand")],
        default="inline",
        required=True,
        help="How skills are handed to the responder. 'In the prompt' sends "
        "every skill text with each message; 'On demand' sends the summaries "
        "and lets the model load a skill with the odoo_skill tool, the way "
        "OCA ai_tool_skill lets a skill be called like a tool.",
    )
    push_enabled = fields.Boolean(
        help="Push new messages to the AI agent in real time. When "
        "disabled, the agent falls back to polling.",
    )
    push_url = fields.Char(
        help="Endpoint receiving the Odoo events, for instance "
        "https://responder.example.com/ai_agent/events.",
    )
    push_secret = fields.Char(
        groups="base.group_system",
        default=lambda self: secrets.token_hex(32),
        copy=False,
        help="Secret used to sign the events pushed to the agent.",
    )
    max_batch_size = fields.Integer(default=50)
    claim_ttl_minutes = fields.Integer(
        default=5,
        help="Delay after which a message claimed by a agent but never "
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
        help="Groups allowed to chat with AI agent through this agent. "
        "Leave empty to fall back on the 'Use the AI assistant' group.",
    )
    access_denied_message = fields.Text(
        translate=True,
        default=lambda self: _(
            "You are not allowed to use the AI assistant. Please ask "
            "your administrator to add you to the 'Use the AI assistant' group."
        ),
    )
    denial_cooldown_minutes = fields.Integer(
        default=30,
        help="Minimum delay between two access-denied answers in the same "
        "channel, to avoid flooding it.",
    )
    allow_user_connect = fields.Boolean(
        help="Let users hand their own Odoo API key over to the agent, so the "
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
        "agent_excluded_model_rel",
        "agent_id",
        "model_id",
        string="Models hidden from the assistant",
        help="Models the assistant may not query even when the user can read "
        "them, e.g. because answers are posted in a channel other people read.",
    )
    require_api_key_for_tools = fields.Boolean(
        default=True,
        help="Only tell the agent that it may call Odoo on behalf of a user "
        "when that user owns an Odoo API key. Users without a key keep the "
        "generic assistant capabilities.",
    )

    # ------------------------------------------------------- agent definition
    agent_key = fields.Char(
        required=True,
        copy=False,
        index=True,
        default=lambda self: secrets.token_hex(6),
        help="Stable key published to the responder, e.g. 'support'.",
    )
    backend = fields.Selection(
        [
            ("hermes", "Hermes Agent"),
            ("n8n", "n8n workflow"),
            ("custom", "Another responder"),
        ],
        default="hermes",
        required=True,
        help="Which runtime answers for this agent. Purely informational for "
        "the transport, but it tells the responder how to route the agent.",
    )
    instructions = fields.Html(
        help="What this agent is for, written by the customer administrator.",
    )
    operator_instructions = fields.Text(
        help="Instructions owned by whoever operates the responder: safety "
        "rules, tone, escalation. Sent after the customer instructions.",
    )
    skill_ids = fields.Many2many(
        "ai.bridge.agent.skill",
        "ai_bridge_agent_skill_rel",
        "agent_id",
        "skill_id",
        string="Skills",
    )
    grant_ids = fields.Many2many(
        "ai.bridge.agent.grant",
        "ai_bridge_agent_grant_agent_rel",
        "agent_id",
        "grant_id",
        string="Data access grants",
        help="Data this agent may touch. Empty grants list means: none "
        "specific, the policy of the gateway applies.",
    )
    knowledge_ids = fields.One2many(
        "ai.bridge.agent.knowledge",
        "agent_id",
        string="Knowledge",
    )
    llm_provider = fields.Char(
        help="Provider the responder should use for this agent, e.g. deepseek. "
        "Leave empty to keep the responder default. The provider credential "
        "stays in the responder, never here.",
    )
    llm_model = fields.Char(
        help="Model override, e.g. deepseek-v4-pro. Empty keeps the default.",
    )
    knowledge_max_chars = fields.Integer(
        default=8000,
        help="Budget for the knowledge sent with each conversation.",
    )

    _sql_constraints = [
        (
            "agent_key_uniq",
            "unique(agent_key)",
            "The agent key must be unique: it identifies the agent for the "
            "responder.",
        ),
    ]

    def _agent_instructions_plaintext(self, value, limit=20000):
        text = html2plaintext(value or "") if value else ""
        text = text.strip()
        return text[:limit]

    def _get_agent_definition(self):
        """Describe this agent for the responder.

        Composed in a fixed order so an operator rule can never be weakened by
        customer text: the responder applies operator instructions last.
        """
        self.ensure_one()
        budget = self.knowledge_max_chars or 8000
        knowledge = []
        used = 0
        for item in self.knowledge_ids.filtered("active"):
            text = self._agent_instructions_plaintext(item.body, limit=budget)
            if not text:
                continue
            if used + len(text) > budget:
                break
            knowledge.append({"name": item.name, "text": text})
            used += len(text)
        return {
            "key": self.agent_key,
            "name": self.name,
            "backend": self.backend,
            "instructions": self._agent_instructions_plaintext(self.instructions),
            "operator_instructions": (self.operator_instructions or "").strip(),
            "skill_loading": self.skill_loading,
            "skills": [
                skill._as_skill_dict(lazy=self.skill_loading == "lazy")
                for skill in self.skill_ids.filtered("active")
            ],
            "knowledge": knowledge,
            "llm": {
                "provider": (self.llm_provider or "").strip(),
                "model": (self.llm_model or "").strip(),
            },
        }

    @api.model
    def _default_tenant_key(self):
        """Return the tenant key of this database, creating it once."""
        params = self.env["ir.config_parameter"].sudo()
        key = params.get_param("ai_bridge_agent.tenant_key")
        if not key:
            # Keep the identity of a database upgraded from the previous name,
            # so running conversations are not orphaned by the rename.
            key = params.get_param("hermes.tenant_key")
        if not key:
            key = secrets.token_hex(8)
        params.set_param("ai_bridge_agent.tenant_key", key)
        return key

    def _compute_message_count(self):
        queue = self.env["ai.bridge.agent.message"]
        for agent in self:
            agent.message_count = queue.search_count([("agent_id", "=", agent.id)])

    def _compute_pending_count(self):
        queue = self.env["ai.bridge.agent.message"]
        for agent in self:
            agent.pending_count = queue.search_count(
                [
                    ("agent_id", "=", agent.id),
                    ("state", "in", ("pending", "processing", "pushed")),
                ]
            )

    def _get_session_key(self, user=None, channel=None):
        """Build the AI agent session key for this tenant and scope."""
        self.ensure_one()
        # The user id stays in the key whatever the scope: the responder maps
        # a session back to a person (credentials, tools) from that id.
        user_part = f"user_{user.id if user else 0}"
        if self.session_scope == "channel":
            return (
                f"odoo:{self.tenant_key}:{self.environment}:"
                f"{user_part}:channel_{channel.id if channel else 0}"
            )
        return f"odoo:{self.tenant_key}:{self.environment}:{user_part}"

    @api.model
    def _find_by_token(self, token):
        """Return the active agent owning ``token`` (grace included)."""
        if not token:
            return self.browse()
        agent = self.search(
            [
                ("active", "=", True),
                "|",
                ("webhook_token", "=", token),
                ("webhook_token_previous", "=", token),
            ],
            limit=1,
        )
        if agent and agent._is_token_valid(token):
            return agent
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
            "ai_oca_bridge_agent.group_agent_user", raise_if_not_found=False
        )
        return group

    def _is_user_allowed(self, user):
        """Tell whether ``user`` may chat with AI agent through this agent."""
        self.ensure_one()
        if not user:
            return False
        # Administrators always keep access, otherwise a fresh install could
        # not be configured by the very person setting it up.
        if user.has_group("base.group_system"):
            return True
        if self.allowed_group_ids:
            return bool(self.allowed_group_ids & user.groups_id)
        return user.has_group("ai_oca_bridge_agent.group_agent_user")

    def _get_capabilities(self, message):
        """Describe what the agent may do on behalf of the user.

        Extension point: child modules refine the tiers without touching the
        transport. The agent keeps the actual policy (tools, prompts).
        """
        self.ensure_one()
        has_key = bool(message.user_has_api_key)
        return {
            "tier": "full" if has_key else "basic",
            "odoo_rpc": has_key or not self.require_api_key_for_tools,
            "tools": [],
        }

    def _get_tenant_info(self):
        """Public description of this Odoo instance inside a AI agent install."""
        self.ensure_one()
        return {
            "key": self.tenant_key,
            "name": self.name,
            "environment": self.environment,
            "odoo_url": self.odoo_url,
            "odoo_version": odoo_version,
        }

    def _build_event_payload(self, message):
        """Build the event pushed to the agent for one queued message.

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
            "agent_id": self.id,
            "agent_key": self.agent_key,
            "agent": self._get_agent_definition(),
            "attachments": message._get_attachments_payload(),
            "view_context": message.context_json
            or self.env["ai.bridge.user.context"]
            .sudo()
            ._get_for_user(message.user_id, message.channel_id),
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
                "url": f"{(self.odoo_url or '').rstrip('/')}/ai_agent/reply",
                "auth": "hmac",
            },
        }

    def _get_push_headers(self, timestamp, body, delivery_id):
        self.ensure_one()
        return {
            "Content-Type": "application/json",
            "X-AI-Agent-Tenant": self.tenant_key or "",
            "X-AI-Agent-Timestamp": timestamp,
            "X-AI-Agent-Delivery": delivery_id or "",
            "X-AI-Agent-Signature": "sha256="
            + self._compute_signature(timestamp, body),
        }

    def _push_message(self, message, timeout=3):
        """Push one queued message to the agent, never raise."""
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
        for agent in self:
            agent.write(
                {
                    "webhook_token_previous": agent.webhook_token,
                    "webhook_token": secrets.token_hex(32),
                    "token_rotated_at": fields.Datetime.now(),
                }
            )

    def action_test_connection(self):
        """Test connectivity by polling the agent health endpoint."""
        self.ensure_one()
        if not self.agent_url:
            return self._notify(_("No AI agent URL configured"), "warning")
        try:
            import requests

            resp = requests.get(
                f"{self.agent_url}/health",
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
            "params": {"title": "AI agent", "message": message, "type": type_},
        }
