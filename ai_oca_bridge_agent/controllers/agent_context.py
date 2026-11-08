# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import http
from odoo.http import request


class AgentContextController(http.Controller):
    """Receive the page a user is browsing, so the assistant has context."""

    @http.route(
        "/ai_agent/web_context",
        type="json",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def report_web_context(self, **kwargs):
        data = request.get_json_data() or {}
        context = request.env["ai.bridge.user.context"]._record(request.env.user, data)
        return {
            "status": "ok",
            "model": context.model_name,
            "res_id": context.res_id,
        }
