# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import hmac
import html as html_module
import logging
import re
import time

from markupsafe import Markup

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

SIGNATURE_MAX_SKEW = 300


class HermesWebhook(http.Controller):
    """Endpoints used by the Hermes gateway to answer into Odoo.

    Every endpoint answers a JSON object with a ``status`` key: the JSON-RPC
    transport always returns HTTP 200, so the status must travel in the body.
    """

    # ------------------------------------------------------------------ auth
    def _get_gateway_from_token(self, token):
        return request.env["hermes.gateway"].sudo()._find_by_token(token)

    def _get_gateway_from_headers(self):
        """Authenticate the gateway with its HMAC signature."""
        headers = request.httprequest.headers
        tenant_key = headers.get("X-Hermes-Tenant")
        signature = headers.get("X-Hermes-Signature", "")
        timestamp = headers.get("X-Hermes-Timestamp", "")
        if not tenant_key or not signature or not timestamp:
            return request.env["hermes.gateway"].sudo().browse()
        try:
            skew = abs(time.time() - int(timestamp))
        except (TypeError, ValueError):
            return request.env["hermes.gateway"].sudo().browse()
        if skew > SIGNATURE_MAX_SKEW:
            _logger.warning("Hermes signature expired for tenant %s", tenant_key)
            return request.env["hermes.gateway"].sudo().browse()
        gateway = (
            request.env["hermes.gateway"]
            .sudo()
            .search([("tenant_key", "=", tenant_key), ("active", "=", True)], limit=1)
        )
        if not gateway:
            return request.env["hermes.gateway"].sudo().browse()
        body = request.httprequest.get_data(as_text=True)
        expected = "sha256=" + gateway._compute_signature(timestamp, body or "")
        if not hmac.compare_digest(expected, signature):
            _logger.warning("Invalid Hermes signature for tenant %s", tenant_key)
            return request.env["hermes.gateway"].sudo().browse()
        return gateway

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
    @http.route("/hermes/reply", type="json", auth="public", csrf=False)
    def hermes_reply(self, **kwargs):
        """Receive an answer from the gateway (HMAC authenticated)."""
        gateway = self._get_gateway_from_headers()
        if not gateway:
            return self._error("Invalid signature", "invalid_signature")
        data = request.get_json_data()
        if not data:
            return self._error("No JSON data")
        return self._dispatch(gateway, data)

    @http.route(
        "/hermes/webhook/<string:token>", type="json", auth="public", csrf=False
    )
    def hermes_webhook(self, token, **kwargs):
        """Legacy token authenticated answer endpoint."""
        gateway = self._get_gateway_from_token(token)
        if not gateway:
            return self._error("Invalid token", "invalid_token")
        data = request.get_json_data()
        if not data:
            return self._error("No JSON data")
        return self._dispatch(gateway, data)

    @http.route("/hermes/push/<string:token>", type="json", auth="public", csrf=False)
    def hermes_push(self, token, **kwargs):
        """Proactive message from the gateway (legacy token auth)."""
        gateway = self._get_gateway_from_token(token)
        if not gateway:
            return self._error("Invalid token", "invalid_token")
        data = request.get_json_data()
        if not data:
            return self._error("No JSON data")
        return self._dispatch(gateway, data)

    def _dispatch(self, gateway, data):
        action = data.get("action", "post_message")
        if action == "post_message":
            return self._action_post_message(gateway, data)
        if action == "typing":
            return self._action_typing(
                gateway, data.get("channel_id"), data.get("is_typing", False)
            )
        if action == "escalate":
            return self._action_escalate(gateway, data)
        return self._error(f"Unknown action: {action}", "unknown_action")

    # -------------------------------------------------------------- actions
    def _get_target_message(self, gateway, data):
        """Find the queued message the answer belongs to."""
        queue = request.env["hermes.message.queue"].sudo()
        queue_id = data.get("queue_id")
        if queue_id:
            try:
                message = queue.browse(int(queue_id))
            except (TypeError, ValueError):
                return queue.browse()
            if message.exists() and message.gateway_id == gateway:
                return message
        channel_id = data.get("channel_id")
        if channel_id:
            try:
                channel_id = int(channel_id)
            except (TypeError, ValueError):
                return queue.browse()
            return queue.search(
                [
                    ("gateway_id", "=", gateway.id),
                    ("channel_id", "=", channel_id),
                    ("state", "in", ("processing", "pushed")),
                ],
                order="id desc",
                limit=1,
            )
        return queue.browse()

    def _action_post_message(self, gateway, data):
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

        ai_user = gateway.ai_user_id
        if not ai_user:
            return self._error("No AI user configured", "no_ai_user")

        delivery_id = data.get("delivery_id")
        target = self._get_target_message(gateway, data)

        # Replay protection: the same delivery must not post twice.
        if delivery_id:
            previous = (
                request.env["hermes.message.queue"]
                .sudo()
                ._find_by_delivery(gateway, delivery_id)
            )
            if previous and previous.state == "done":
                return self._ok(
                    duplicate=True,
                    message_id=previous.response_message_id.id or False,
                )

        body_html = self._format_body(body)
        try:
            message = (
                channel.with_user(ai_user.id)
                .with_context(
                    mail_create_nosubscribe=True,
                    mail_notrack=True,
                )
                .message_post(
                    body=Markup(body_html),
                    message_type="comment",
                    subtype_xmlid="mail.mt_comment",
                )
            )
        except Exception as exc:  # noqa: BLE001 - reported to the gateway
            _logger.error("Hermes failed to post in channel %s: %s", channel.id, exc)
            return self._error(str(exc), "post_failed")

        if target:
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

    def _action_typing(self, gateway, channel_id, is_typing):
        if not channel_id:
            return self._error("Missing channel_id", "missing_channel")
        channel = request.env["discuss.channel"].sudo().browse(int(channel_id))
        if not channel.exists():
            return self._error("Channel not found", "channel_not_found")
        for member in channel.channel_member_ids:
            if member.partner_id == gateway.ai_user_id.partner_id:
                member._notify_typing(is_typing=is_typing)
        return self._ok()

    def _action_escalate(self, gateway, data):
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
    @http.route("/hermes/notify/<string:token>", type="json", auth="public", csrf=False)
    def hermes_notify(self, token, **kwargs):
        """Send a web notification to an Odoo user."""
        gateway = self._get_gateway_from_token(token)
        if not gateway:
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
        notif_title = data.get("title", "Hermes")
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

        return self._notify_fallback(gateway, user, notif_title, notif_message)

    def _notify_fallback(self, gateway, user, title, message):
        """Fallback notification via DM when web_notify is not available."""
        try:
            channel_model = request.env["discuss.channel"].sudo()
            channel_info = channel_model.with_user(gateway.ai_user_id.id).channel_get(
                [user.partner_id.id]
            )
            channel = channel_model.browse(channel_info["id"])
            posted = channel.with_user(gateway.ai_user_id.id).message_post(
                body=Markup(f"<p><b>{title}</b></p><p>{message}</p>"),
                message_type="comment",
                subtype_xmlid="mail.mt_comment",
            )
            return self._ok(
                method="dm_fallback",
                message_id=posted.id,
                channel_id=channel.id,
            )
        except Exception as exc:  # noqa: BLE001 - reported to the gateway
            _logger.error("Fallback notification failed: %s", exc)
            return self._error(str(exc), "notify_failed")

    # ----------------------------------------------------------- formatting
    def _format_body(self, body):
        """Format the gateway answer as Odoo HTML.

        Markdown-like output from the agent is kept as text with links; the
        rest is escaped so a model answer can never inject HTML in Discuss.
        """
        if not body:
            return "<p></p>"
        text = re.sub(r"<[^>]+>", "", body)
        text = html_module.unescape(text)
        if not text.strip():
            return "<p></p>"
        text = html_module.escape(text)
        url_pattern = re.compile(r"(https?://[^\s<>\"{}|\\^`\[\]]+)")
        text = url_pattern.sub(r'<a href="\1" target="_blank">\1</a>', text)
        return f"<span>{text}</span>"
