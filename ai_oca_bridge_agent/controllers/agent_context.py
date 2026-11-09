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
        user = request.env.user
        # Only users who may talk to the assistant are tracked at all: no
        # browsing data is collected for the rest of the company.
        if not (
            user.has_group("ai_oca_bridge_agent.group_agent_user")
            or user.has_group("base.group_system")
        ):
            return {"status": "ignored"}
        data = request.get_json_data() or {}
        channel = None
        if data.get("channel_id"):
            channel = (
                request.env["discuss.channel"].browse(int(data["channel_id"])).exists()
            )
            # The browser may only report a thread the user actually belongs to.
            if channel and user.partner_id not in channel.channel_member_ids.mapped(
                "partner_id"
            ):
                channel = None
        context = request.env["ai.bridge.user.context"]._record(user, data, channel)
        return {
            "status": "ok",
            "model": context.model_name,
            "res_id": context.res_id,
        }
