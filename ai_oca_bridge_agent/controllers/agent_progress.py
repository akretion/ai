# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Transient notes while the agent works ("Thinking...", one line per tool).

Borrowed from OCA ``ai_agent`` (#117), where each turn shows what the agent is
doing, live and kept out of what is sent back to the model. We post them as
notifications and delete them when the answer arrives; the ids live on the
queue row so a failed run cannot leave a thread full of ghosts.
"""

import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class AgentProgress(http.Controller):
    @http.route(
        "/ai_agent/progress",
        type="json",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def agent_progress(self, **kwargs):
        agent = self._get_agent()
        if not agent:
            return {"status": "error", "error": "invalid_token"}
        data = request.get_json_data() or {}
        text = (data.get("text") or "").strip()[:300]
        if not text:
            return {"status": "error", "error": "missing_text"}
        queued = self._get_target(agent, data)
        if not queued:
            return {"status": "error", "error": "unknown_message"}
        ai_user = agent.ai_user_id
        if not ai_user:
            return {"status": "error", "error": "no_ai_user"}
        try:
            message = (
                queued.channel_id.sudo()
                .with_user(ai_user)
                .with_context(mail_create_nosubscribe=True, mail_notrack=True)
                .message_post(
                    body=text,
                    message_type="notification",
                    subtype_xmlid="mail.mt_note",
                )
            )
        except Exception as exc:  # noqa: BLE001 - progress must never block
            _logger.warning(
                "progress note failed on channel %s: %s", queued.channel_id.id, exc
            )
            return {"status": "error", "error": "post_failed"}
        ids = list(queued.progress_message_ids or [])
        ids.append(message.id)
        queued.write({"progress_message_ids": ids})
        return {"status": "ok", "message_id": message.id}

    def _get_target(self, agent, data):
        queued = request.env["ai.bridge.agent.message"].sudo()
        if data.get("queue_id"):
            row = queued.browse(int(data["queue_id"])).exists()
            return row if row and row.agent_id == agent else None
        session_key = (data.get("session_key") or "").strip()
        if not session_key:
            return None
        return queued.search(
            [("agent_id", "=", agent.id), ("session_key", "=", session_key)],
            order="id desc",
            limit=1,
        )

    def _get_agent(self):
        auth_header = request.httprequest.headers.get("Authorization", "")
        token = auth_header.replace("Bearer ", "").strip() if auth_header else ""
        if not token:
            return None
        return request.env["ai.bridge.agent"].sudo()._find_by_token(token)
