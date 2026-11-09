# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Load one skill on demand.

Progressive disclosure: with ``skill_loading = lazy`` the agent definition only
carries the summaries, and the responder fetches a skill body when the
conversation needs it. OCA ``ai_tool_skill`` reaches the same result from the
other side, by making a skill a callable ``ai.tool``; if that module lands, the
two implementations can share one abstraction behind this endpoint.
"""

import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class AgentSkills(http.Controller):
    @http.route(
        "/ai_agent/skills/get",
        type="json",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def agent_skills_get(self, **kwargs):
        agent = self._get_agent()
        if not agent:
            return {"status": "error", "error": "invalid_token"}
        data = request.get_json_data() or {}
        user = self._get_user(data.get("user_id"))
        if isinstance(user, dict):
            return user
        if not agent._is_user_allowed(user):
            return {"status": "error", "error": "user_not_allowed"}
        name = (data.get("name") or "").strip()
        if not name:
            return {"status": "error", "error": "missing_name"}
        skills = agent.skill_ids.filtered(
            lambda skill: skill.active and skill.name == name
        )
        if not skills:
            return {"status": "error", "error": "skill_not_found"}
        skill = skills[:1]
        return {
            "status": "ok",
            "skill": {
                "name": skill.name,
                "summary": skill.summary or "",
                "instructions": skill.instructions or "",
                "tools": [
                    tool.strip()
                    for tool in (skill.tool_names or "").split(",")
                    if tool.strip()
                ],
            },
        }

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
