# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging
from urllib.parse import quote

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class AgentAttachment(http.Controller):
    """Serve the files a user attached to a message, to that agent only."""

    @http.route(
        "/ai_agent/attachment/<string:token>/<int:attachment_id>",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
    )
    def download(self, token, attachment_id, **kwargs):
        agent = request.env["ai.bridge.agent"].sudo()._find_by_token(token)
        if not agent:
            return request.not_found()
        attachment = request.env["ir.attachment"].sudo().browse(attachment_id)
        if not attachment.exists() or not self._belongs_to_agent(agent, attachment):
            return request.not_found()
        content = attachment.raw or b""
        filename = attachment.name or "attachment"
        return request.make_response(
            content,
            headers=[
                ("Content-Type", attachment.mimetype or "application/octet-stream"),
                ("Content-Length", str(len(content))),
                (
                    "Content-Disposition",
                    f"attachment; filename*=UTF-8''{quote(filename)}",
                ),
            ],
        )

    def _belongs_to_agent(self, agent, attachment):
        """Only files that were queued for this agent are downloadable."""
        return bool(
            request.env["ai.bridge.agent.message"]
            .sudo()
            .search(
                [
                    ("agent_id", "=", agent.id),
                    ("attachment_ids", "in", attachment.id),
                ],
                limit=1,
            )
        )
