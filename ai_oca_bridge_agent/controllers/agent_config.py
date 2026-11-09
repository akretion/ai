# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Read one instance parameter, key by key.

``ir.config_parameter`` is denied as a model: it is where Odoo keeps SMTP
passwords and every provider key a module ever needed, in clear text. An
assistant still needs a few of them ("what is this instance's URL?"), so the
agent carries a list of allowed keys and the lookup happens here, one key at a
time, never through a generic read.

When OCA ``data_encryption`` is installed the value may live in
``encrypted.data`` instead; we use it if it is there, without depending on it,
and such values are reported as secrets: the model may confirm they are set,
never read them.
"""

import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class AgentConfig(http.Controller):
    @http.route(
        "/ai_agent/config/get",
        type="json",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def agent_config_get(self, **kwargs):
        agent = self._get_agent()
        if not agent:
            return {"status": "error", "error": "invalid_token"}
        data = request.get_json_data() or {}
        user = self._get_user(data.get("user_id"))
        if isinstance(user, dict):
            return user
        if not agent._is_user_allowed(user):
            return {"status": "error", "error": "user_not_allowed"}
        key = (data.get("key") or "").strip()
        if not key:
            return {"status": "error", "error": "missing_key"}
        if not agent._config_key_allowed(key):
            return {
                "status": "error",
                "error": "key_not_allowed",
                "allowed": agent._config_allowed_keys(),
            }
        found, value, encrypted = agent._read_config_key(key)
        if not found:
            return {"status": "error", "error": "key_not_found"}
        if encrypted:
            # A secret stays server-side: the assistant may report that it is
            # configured, never echo it into a language model's context.
            return {
                "status": "ok",
                "key": key,
                "secret": True,
                "present": bool(value),
            }
        return {"status": "ok", "key": key, "secret": False, "value": value}

    def _get_agent(self):
        auth_header = request.httprequest.headers.get("Authorization", "")
        token = auth_header.replace("Bearer ", "").strip() if auth_header else ""
        if not token:
            return None
        return request.env["ai.bridge.agent"].sudo()._find_by_token(token)

    def _get_user(self, user_id):
        try:
            user_id = int(user_id)
        except (TypeError, ValueError):
            return {"status": "error", "error": "unknown_user"}
        user = request.env["res.users"].sudo().browse(user_id).exists()
        if not user or not user.active:
            return {"status": "error", "error": "unknown_user"}
        return user
