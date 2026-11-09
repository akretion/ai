# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Grant endpoints: what an agent may read for one user, with which limits."""

import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class AgentGrants(http.Controller):
    @http.route(
        "/ai_agent/tools/available",
        type="json",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def agent_grants_available(self, **kwargs):
        """Grants opened to one user for this agent."""
        agent = self._get_agent()
        if not agent:
            return {"status": "error", "error": "invalid_token"}
        user = self._get_user((request.get_json_data() or {}).get("user_id"))
        if isinstance(user, dict):
            return user
        if not agent._is_user_allowed(user):
            return {"status": "error", "error": "user_not_allowed"}
        grants = request.env["ai.bridge.agent.grant"].sudo()
        return {
            "status": "ok",
            "mode": agent.tool_access_mode,
            "grants": [
                grant._as_grant_dict() for grant in grants._for_user(user, agent)
            ],
            "models": grants._readable_models(user, agent)[:500],
        }

    @http.route(
        "/ai_agent/tools/models",
        type="json",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def agent_grants_models(self, **kwargs):
        """Models the assistant may query for this user, so it can discover."""
        agent = self._get_agent()
        if not agent:
            return {"status": "error", "error": "invalid_token"}
        user = self._get_user((request.get_json_data() or {}).get("user_id"))
        if isinstance(user, dict):
            return user
        if not agent._is_user_allowed(user):
            return {"status": "error", "error": "user_not_allowed"}
        models = (
            request.env["ai.bridge.agent.grant"]
            .sudo()
            ._readable_models(user, agent)[:500]
        )
        return {"status": "ok", "mode": agent.tool_access_mode, "models": models}

    @http.route(
        "/ai_agent/tools/resolve",
        type="json",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def agent_grants_resolve(self, **kwargs):
        """May this user use this grant, and with which limits?

        The agent checks every call here before running the query with the
        user's own API key, so a stale cache or a prompt injection cannot widen
        what the grants allow.
        """
        agent = self._get_agent()
        if not agent:
            return {"status": "error", "error": "invalid_token"}
        data = request.get_json_data() or {}
        user = self._get_user(data.get("user_id"))
        if isinstance(user, dict):
            return user
        if not agent._is_user_allowed(user):
            return {"status": "error", "error": "user_not_allowed"}
        grants = request.env["ai.bridge.agent.grant"].sudo()
        if data.get("grant") or data.get("tool"):
            grant = grants._resolve_for_user(
                user, (data.get("grant") or data.get("tool") or "").strip(), agent
            )
        else:
            grant = grants._resolve_for_user_model(
                user,
                (data.get("model") or "").strip(),
                (data.get("method") or "search_read").strip(),
                agent=agent,
            )
        if not grant:
            return {"status": "error", "error": "grant_not_allowed"}
        return {"status": "ok", "grant": grant, "tool": grant}

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
