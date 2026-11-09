# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hmac
import html as html_module
import logging
import re
import time

from markupsafe import Markup

from odoo import http
from odoo.http import request
from odoo.tools.mail import html_sanitize

try:  # optional, renders tables/lists instead of falling back to plain text
    import markdown
except ImportError:  # pragma: no cover - depends on the customer's environment
    markdown = None

_logger = logging.getLogger(__name__)

SIGNATURE_MAX_SKEW = 300

#: A reply may carry a few generated files, not a data dump.
ATTACHMENT_MAX_COUNT = 5
ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024

MARKDOWN_EXTENSIONS = ["tables", "fenced_code", "sane_lists", "nl2br"]


class AgentWebhook(http.Controller):
    """Endpoints used by the AI agent to answer into Odoo.

    Every endpoint answers a JSON object with a ``status`` key: the JSON-RPC
    transport always returns HTTP 200, so the status must travel in the body.
    """

    # ------------------------------------------------------------------ auth
    def _get_gateway_from_token(self, token):
        return request.env["ai.bridge.agent"].sudo()._find_by_token(token)

    def _get_gateway_from_headers(self):
        """Authenticate the agent with its HMAC signature."""
        headers = request.httprequest.headers
        tenant_key = headers.get("X-AI-Agent-Tenant")
        signature = headers.get("X-AI-Agent-Signature", "")
        timestamp = headers.get("X-AI-Agent-Timestamp", "")
        if not tenant_key or not signature or not timestamp:
            return request.env["ai.bridge.agent"].sudo().browse()
        try:
            skew = abs(time.time() - int(timestamp))
        except (TypeError, ValueError):
            return request.env["ai.bridge.agent"].sudo().browse()
        if skew > SIGNATURE_MAX_SKEW:
            _logger.warning("AI agent signature expired for tenant %s", tenant_key)
            return request.env["ai.bridge.agent"].sudo().browse()
        agent = (
            request.env["ai.bridge.agent"]
            .sudo()
            .search([("tenant_key", "=", tenant_key), ("active", "=", True)], limit=1)
        )
        if not agent:
            return request.env["ai.bridge.agent"].sudo().browse()
        body = request.httprequest.get_data(as_text=True)
        expected = "sha256=" + agent._compute_signature(timestamp, body or "")
        if not hmac.compare_digest(expected, signature):
            _logger.warning("Invalid AI agent signature for tenant %s", tenant_key)
            return request.env["ai.bridge.agent"].sudo().browse()
        return agent

    # --------------------------------------------------------------- payload
    @staticmethod
    def _error(message, code="invalid_request", **extra):
        result = {"status": "error", "error": code, "message": message}
        result.update(extra)
        return result

    @staticmethod
    def _ok(**extra):
        result = {"status": "ok"}
        result.update(extra)
        return result

    # -------------------------------------------------------------- routing
    @http.route("/ai_agent/reply", type="json", auth="public", csrf=False)
    def ai_agent_reply(self, **kwargs):
        """Receive an answer from the agent (HMAC authenticated)."""
        agent = self._get_gateway_from_headers()
        if not agent:
            return self._error("Invalid signature", "invalid_signature")
        data = request.get_json_data()
        if not data:
            return self._error("No JSON data")
        return self._dispatch(agent, data)

    @http.route(
        "/ai_agent/webhook/<string:token>", type="json", auth="public", csrf=False
    )
    def ai_agent_webhook(self, token, **kwargs):
        """Legacy token authenticated answer endpoint."""
        agent = self._get_gateway_from_token(token)
        if not agent:
            return self._error("Invalid token", "invalid_token")
        data = request.get_json_data()
        if not data:
            return self._error("No JSON data")
        return self._dispatch(agent, data)

    @http.route("/ai_agent/push/<string:token>", type="json", auth="public", csrf=False)
    def ai_agent_push(self, token, **kwargs):
        """Proactive message from the agent (legacy token auth)."""
        agent = self._get_gateway_from_token(token)
        if not agent:
            return self._error("Invalid token", "invalid_token")
        data = request.get_json_data()
        if not data:
            return self._error("No JSON data")
        return self._dispatch(agent, data)

    def _dispatch(self, agent, data):
        action = data.get("action", "post_message")
        if action == "post_message":
            return self._action_post_message(agent, data)
        if action == "typing":
            return self._action_typing(
                agent, data.get("channel_id"), data.get("is_typing", False)
            )
        if action == "escalate":
            return self._action_escalate(agent, data)
        return self._error(f"Unknown action: {action}", "unknown_action")

    # -------------------------------------------------------------- actions
    def _get_target_message(self, agent, data):
        """Find the queued message the answer belongs to."""
        queue = request.env["ai.bridge.agent.message"].sudo()
        queue_id = data.get("queue_id")
        if queue_id:
            try:
                message = queue.browse(int(queue_id))
            except (TypeError, ValueError):
                return queue.browse()
            if message.exists() and message.agent_id == agent:
                return message
        channel_id = data.get("channel_id")
        if channel_id:
            try:
                channel_id = int(channel_id)
            except (TypeError, ValueError):
                return queue.browse()
            return queue.search(
                [
                    ("agent_id", "=", agent.id),
                    ("channel_id", "=", channel_id),
                    ("state", "in", ("processing", "pushed")),
                ],
                order="id desc",
                limit=1,
            )
        return queue.browse()

    def _action_post_message(self, agent, data):
        body = data.get("body")
        if not body:
            return self._error("Missing body", "missing_body")

        channel_id = data.get("channel_id") or (data.get("message") or {}).get(
            "channel_id"
        )
        if not channel_id:
            return self._error("Missing channel_id", "missing_channel")
        channel = request.env["discuss.channel"].sudo().browse(int(channel_id))
        if not channel.exists():
            return self._error("Channel not found", "channel_not_found")

        ai_user = agent.ai_user_id
        if not ai_user:
            return self._error("No AI user configured", "no_ai_user")

        delivery_id = data.get("delivery_id")
        target = self._get_target_message(agent, data)

        # Replay protection: the same delivery must not post twice.
        if delivery_id:
            previous = (
                request.env["ai.bridge.agent.message"]
                .sudo()
                ._find_by_delivery(agent, delivery_id)
            )
            if previous and previous.state == "done":
                return self._ok(
                    duplicate=True,
                    message_id=previous.response_message_id.id or False,
                )

        body_html = self._format_body(body, data.get("format"))
        attachment_ids = self._create_attachments(request.env, channel, data)
        try:
            message = (
                channel.with_user(ai_user.id)
                .with_context(
                    mail_create_nosubscribe=True,
                    mail_notrack=True,
                )
                .message_post(
                    body=Markup(body_html),
                    # 18.0 message_post expects a plain list of ids here.
                    attachment_ids=attachment_ids.ids,
                    message_type="comment",
                    subtype_xmlid="mail.mt_comment",
                )
            )
        except Exception as exc:  # noqa: BLE001 - reported to the agent
            _logger.error("AI agent failed to post in channel %s: %s", channel.id, exc)
            return self._error(str(exc), "post_failed")

        if target:
            target._clear_progress()
            values = {"delivery_id": delivery_id or target.delivery_id}
            target.write(values)
            target._mark_done(message.id)
            usage = data.get("usage") or {}
            if usage:
                target.write(
                    {
                        "tokens_in": usage.get("tokens_in") or 0,
                        "tokens_out": usage.get("tokens_out") or 0,
                    }
                )
        return self._ok(message_id=message.id)

    def _action_typing(self, agent, channel_id, is_typing):
        if not channel_id:
            return self._error("Missing channel_id", "missing_channel")
        channel = request.env["discuss.channel"].sudo().browse(int(channel_id))
        if not channel.exists():
            return self._error("Channel not found", "channel_not_found")
        for member in channel.channel_member_ids:
            if member.partner_id == agent.ai_user_id.partner_id:
                member._notify_typing(is_typing=is_typing)
        return self._ok()

    def _action_escalate(self, agent, data):
        """Escalation hook.

        EXTENSION POINT: override in a child module to create a ticket, notify
        a support team, or route the conversation to a human. The OCA module
        deliberately implements no escalation backend.
        """
        return self._ok(
            message="Escalation not implemented in base module.",
            channel_id=data.get("channel_id"),
        )

    # ------------------------------------------------------------ notifying
    @http.route(
        "/ai_agent/notify/<string:token>", type="json", auth="public", csrf=False
    )
    def ai_agent_notify(self, token, **kwargs):
        """Send a web notification to an Odoo user."""
        agent = self._get_gateway_from_token(token)
        if not agent:
            return self._error("Invalid token", "invalid_token")
        data = request.get_json_data()
        if not data:
            return self._error("No JSON data")
        user_id = data.get("user_id")
        if not user_id:
            return self._error("Missing user_id", "missing_user")

        user = request.env["res.users"].sudo().browse(int(user_id))
        if not user.exists():
            return self._error("User not found", "user_not_found")

        notif_message = data.get("message", "")
        notif_title = data.get("title", "AI agent")
        notif_type = data.get("type", "info")
        sticky = data.get("sticky", False)
        action = data.get("action")

        # web_notify is a soft dependency
        notify_method = getattr(user, f"notify_{notif_type}", None)
        if notify_method:
            try:
                notify_method(
                    notif_message,
                    title=notif_title,
                    sticky=sticky,
                    action=action,
                )
                return self._ok(method="web_notify", user_id=user.id)
            except Exception as exc:  # noqa: BLE001 - fall back to DM
                _logger.warning("web_notify failed, falling back to DM: %s", exc)

        return self._notify_fallback(agent, user, notif_title, notif_message)

    def _notify_fallback(self, agent, user, title, message):
        """Fallback notification via DM when web_notify is not available."""
        try:
            channel_model = request.env["discuss.channel"].sudo()
            channel_info = channel_model.with_user(agent.ai_user_id.id).channel_get(
                [user.partner_id.id]
            )
            channel = channel_model.browse(channel_info["id"])
            posted = channel.with_user(agent.ai_user_id.id).message_post(
                body=Markup(f"<p><b>{title}</b></p><p>{message}</p>"),
                message_type="comment",
                subtype_xmlid="mail.mt_comment",
            )
            return self._ok(
                method="dm_fallback",
                message_id=posted.id,
                channel_id=channel.id,
            )
        except Exception as exc:  # noqa: BLE001 - reported to the agent
            _logger.error("Fallback notification failed: %s", exc)
            return self._error(str(exc), "notify_failed")

    # ----------------------------------------------------------- formatting
    def _format_body(self, body, fmt=None):
        """Render the responder answer as sanitized Odoo HTML.

        ``format`` is ``plain`` (default), ``markdown`` or ``html``. Whatever
        the responder sends, the result is sanitized, so a model answer can
        never inject a script in Discuss. ``markdown`` degrades to ``plain``
        when the python library is not installed.
        """
        if not body:
            return Markup("<p></p>")
        fmt = (fmt or "plain").strip().lower()
        if fmt == "html":
            return Markup(html_sanitize(body, silent=True))
        if fmt == "markdown" and markdown is not None:
            rendered = markdown.markdown(body, extensions=MARKDOWN_EXTENSIONS)
            return Markup(html_sanitize(rendered, silent=True))
        return Markup(self._plain_to_html(body))

    def _plain_to_html(self, body):
        """Escape plain text, keep its line breaks, turn bare URLs into links."""
        paragraphs = [
            paragraph.strip()
            for paragraph in re.split(r"\n\s*\n", body)
            if paragraph.strip()
        ]
        rendered = []
        for paragraph in paragraphs:
            text = html_module.escape(paragraph)
            url_pattern = re.compile(r"(https?://[^\s<>\"{}|\\^`\[\]]+)")
            text = url_pattern.sub(r'<a href="\1" target="_blank">\1</a>', text)
            text = text.replace("\n", "<br/>")
            rendered.append(f"<p>{text}</p>")
        return "".join(rendered) or "<p></p>"

    # ---------------------------------------------------------- attachments
    def _create_attachments(self, env, channel, data):
        """Store the files the responder generated, as Discuss attachments."""
        items = data.get("attachments") or []
        if not isinstance(items, list):
            _logger.warning("Ignoring malformed attachments payload")
            return request.env["ir.attachment"]
        attachments = env["ir.attachment"].sudo()
        total = 0
        created = attachments.browse()
        for item in items[:ATTACHMENT_MAX_COUNT]:
            if not isinstance(item, dict):
                continue
            name = (item.get("name") or "attachment").replace("/", "_").strip()[:120]
            datas = item.get("b64") or ""
            try:
                size = len(base64.b64decode(datas, validate=True))
            except Exception:  # noqa: BLE001 - garbage payload, skip it
                _logger.warning("Ignoring attachment %s: invalid base64", name)
                continue
            total += size
            if total > ATTACHMENT_MAX_BYTES:
                _logger.warning("Attachment budget reached, ignoring %s", name)
                break
            created |= attachments.create(
                {
                    "name": name,
                    "datas": datas,
                    "mimetype": item.get("mimetype") or "application/octet-stream",
                    "res_model": "discuss.channel",
                    "res_id": channel.id,
                }
            )
        return created
