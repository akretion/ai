# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

#: A context older than this is still sent, but marked as stale.
CONTEXT_FRESH_SECONDS = 3600


class AiBridgeUserContext(models.Model):
    """Where a user was working when they wrote to the assistant.

    The browser reports the URL; the model is a hint for the responder, never a
    permission: access decisions stay on the user's own API key.
    """

    _name = "ai.bridge.user.context"
    _description = "Browsing context of an AI agent user"
    _rec_name = "user_id"

    user_id = fields.Many2one(
        "res.users", required=True, ondelete="cascade", index=True
    )
    channel_id = fields.Many2one(
        "discuss.channel",
        ondelete="cascade",
        index=True,
        help="Conversation the user was in. Empty is the user's last context "
        "anywhere, used when the thread has none of its own.",
    )
    url = fields.Char(help="Full URL reported by the browser.")
    action_id = fields.Char()
    action_name = fields.Char()
    model_name = fields.Char(string="Model")
    res_id = fields.Integer(string="Record")
    view_type = fields.Char()
    updated_at = fields.Datetime()

    _sql_constraints = [
        (
            "user_channel_uniq",
            "unique(user_id, channel_id)",
            "One browsing context per user and conversation.",
        ),
    ]

    # ------------------------------------------------------------- URL parsing
    @api.model
    def _parse_url(self, url):
        """Extract what the web client put in the URL, best effort.

        Odoo has several URL shapes (``/web#action=1&model=x&id=2``,
        ``/odoo/action-1/2?view_type=form``); anything unrecognised simply
        leaves the fields empty.
        """
        values = {}
        if not url:
            return values
        parsed = urlparse(url)
        for chunk in (parsed.fragment, parsed.query):
            if not chunk:
                continue
            params = parse_qs(chunk)
            for key in ("action", "model", "id", "view_type", "action_id"):
                if key in params and params[key]:
                    values[key] = params[key][0]
        # /odoo/action-<action_id>/<res_id>
        path_parts = [part for part in parsed.path.split("/") if part]
        for index, part in enumerate(path_parts):
            if part.startswith("action-") and "action" not in values:
                values["action"] = part.split("action-", 1)[1]
                if index + 1 < len(path_parts):
                    values.setdefault("id", path_parts[index + 1])
        return values

    @api.model
    def _record(self, user, data, channel=None):
        """Store the browsing context reported by one user's browser."""
        if not user:
            return self.browse()
        url = (data.get("url") or "").strip()[:2000]
        if not url:
            return self.browse()
        parsed = self._parse_url(url)
        values = {
            "user_id": user.id,
            "url": url,
            "action_id": str(parsed.get("action") or parsed.get("action_id") or ""),
            "model_name": parsed.get("model") or "",
            "res_id": int(parsed["id"]) if str(parsed.get("id") or "").isdigit() else 0,
            "view_type": parsed.get("view_type") or "",
            "action_name": (data.get("action_name") or "").strip()[:200],
            "channel_id": channel.id if channel else False,
            "updated_at": fields.Datetime.now(),
        }
        context = self.sudo().search(
            [
                ("user_id", "=", user.id),
                ("channel_id", "=", channel.id if channel else False),
            ],
            limit=1,
        )
        if context:
            context.write(values)
        else:
            context = self.sudo().create(values)
        return context

    def _as_context_dict(self):
        self.ensure_one()
        age = 0
        if self.updated_at:
            age = int((fields.Datetime.now() - self.updated_at).total_seconds())
        return {
            "url": self.url,
            "model": self.model_name,
            "view_type": self.view_type,
            "action_id": self.action_id,
            "action_name": self.action_name,
            "res_id": self.res_id,
            "age_seconds": age,
            "stale": age > CONTEXT_FRESH_SECONDS,
            "source": "browser-reported hint, not an access control",
        }

    @api.model
    def _cron_cleanup(self):
        """Forget browsing contexts nobody refreshed for a month."""
        limit = fields.Datetime.now() - timedelta(days=30)
        stale = self.sudo().search([("updated_at", "<", limit)])
        stale.unlink()

    @api.model
    def _get_for_user(self, user, channel=None):
        """Context of that conversation, falling back to the user's last one.

        A thread keeps its own row so a conversation started from a form view
        does not inherit the context of another thread opened elsewhere.
        """
        if not user:
            return {}
        context = self.sudo().search(
            [("user_id", "=", user.id), ("channel_id", "=", channel.id)]
            if channel
            else [("user_id", "=", user.id), ("channel_id", "=", False)],
            limit=1,
        )
        if not context and channel:
            context = self.sudo().search(
                [("user_id", "=", user.id), ("channel_id", "=", False)], limit=1
            )
        return context._as_context_dict() if context else {}
