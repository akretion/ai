# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import http
from odoo.http import request


class AgentCredentials(http.Controller):
    """Hand-off of user API keys to the agent.

    Odoo cannot read an API key back, so the plaintext exists only at creation
    time: the user triggers it from their preferences, it waits here for a few
    minutes, and the agent collects it with its own token. The row keeps the
    secret only until the collection, then wipes it.
    """

    @http.route("/ai_agent/credentials", type="json", auth="public", csrf=False)
    def fetch_credentials(self, **kwargs):
        auth_header = request.httprequest.headers.get("Authorization", "")
        token = auth_header.replace("Bearer ", "").strip() if auth_header else ""
        agent = request.env["ai.bridge.agent"].sudo()._find_by_token(token)
        if not agent:
            return {"status": "error", "error": "invalid_token", "credentials": []}
        credentials = (
            request.env["ai.bridge.agent.credential"].sudo()._fetch_pending(agent)
        )
        return {"status": "ok", "credentials": credentials}
