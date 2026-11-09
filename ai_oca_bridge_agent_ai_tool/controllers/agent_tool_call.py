# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Declared tools of an agent, executed under the calling user's rights."""

import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class AgentToolCall(http.Controller):
    @http.route(
        "/ai_agent/tools/declared",
        type="json",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def agent_tools_declared(self, **kwargs):
        """Tools assigned to this agent and usable by that user."""
        agent = self._get_agent()
        if not agent:
            return {"status": "error", "error": "invalid_token"}
        user = self._get_user((request.get_json_data() or {}).get("user_id"))
        if isinstance(user, dict):
            return user
        if not agent._is_user_allowed(user):
            return {"status": "error", "error": "user_not_allowed"}
        tools = agent._get_declared_tools(user)
        return {
            "status": "ok",
            "tools": [tool._get_tool_definition() for tool in tools],
        }

    @http.route(
        "/ai_agent/tools/call",
        type="json",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def agent_tools_call(self, **kwargs):
        """Run one declared tool; the result must be JSON serialisable."""
        agent = self._get_agent()
        if not agent:
            return {"status": "error", "error": "invalid_token"}
        data = request.get_json_data() or {}
        user = self._get_user(data.get("user_id"))
        if isinstance(user, dict):
            return user
        if not agent._is_user_allowed(user):
            return {"status": "error", "error": "user_not_allowed"}
        name = (data.get("tool") or "").strip()
        if not name:
            return {"status": "error", "error": "missing_tool"}
        tool = agent._find_declared_tool(user, name)
        if not tool:
            return {"status": "error", "error": "tool_not_assigned"}
        arguments = data.get("arguments") or {}
        if not isinstance(arguments, dict):
            return {"status": "error", "error": "bad_arguments"}
        try:
            result = tool._run_for_user(
                user,
                arguments,
                record_model=data.get("record_model"),
                record_id=data.get("record_id"),
            )
        except Exception as error:  # noqa: BLE001 - surfaced to the responder
            _logger.warning(
                "tool %s failed for user %s on agent %s: %s",
                name,
                user.id,
                agent.id,
                error,
                exc_info=True,
            )
            return {
                "status": "error",
                "error": "tool_failed",
                "message": str(error)[:500],
            }
        try:
            json.dumps(result)
        except TypeError:
            result = {"result": str(result)}
        _logger.info(
            "tool %s run by user %s on agent %s", name, user.id, agent.id
        )
        return {"status": "ok", "result": result or {}}

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
