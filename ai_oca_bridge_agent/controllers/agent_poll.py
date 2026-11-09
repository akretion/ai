# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging
import time

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

LONG_POLL_MAX = 20
LONG_POLL_STEP = 0.5


class AgentPoll(http.Controller):
    """Polling endpoint, kept as the fallback of the realtime push."""

    def _get_gateway(self, token):
        return request.env["ai.bridge.agent"].sudo()._find_by_token(token)

    @http.route("/ai_agent/poll", type="json", auth="public", csrf=False)
    def ai_agent_poll(self, **kwargs):
        """Return and claim pending messages for the agent.

        Claiming is atomic (``FOR UPDATE SKIP LOCKED``), so several agent
        workers and several Odoo workers can poll concurrently without
        receiving the same message twice.
        """
        auth_header = request.httprequest.headers.get("Authorization", "")
        token = auth_header.replace("Bearer ", "").strip() if auth_header else ""
        agent = self._get_gateway(token)
        if not agent:
            return {"status": "error", "error": "invalid_token", "messages": []}

        queue = request.env["ai.bridge.agent.message"].sudo()
        queue._requeue_stale(agent)
        limit = agent.max_batch_size or 50
        messages = queue._claim_batch(agent, limit=limit, worker="poll")

        wait = 0
        if not messages and agent.long_poll_timeout:
            try:
                wait = min(
                    int(kwargs.get("wait") or 0),
                    agent.long_poll_timeout,
                    LONG_POLL_MAX,
                )
            except (TypeError, ValueError):
                wait = 0
        if wait:
            deadline = time.monotonic() + wait
            while not messages and time.monotonic() < deadline:
                time.sleep(LONG_POLL_STEP)
                messages = queue._claim_batch(agent, limit=limit, worker="poll")

        include_history = kwargs.get("include_history")
        payload = []
        for message in messages:
            payload.append(
                {
                    "id": message.id,
                    "queue_id": message.id,
                    "channel_id": message.channel_id.id,
                    "channel_name": message.channel_id.name,
                    "body": message.body,
                    "body_text": message.body_text,
                    "author_id": message.author_id.id,
                    "author_name": message.author_name,
                    "user_id": message.user_id.id,
                    "user_has_api_key": message.user_has_api_key,
                    "session_key": message.session_key,
                    "db_hash": message.db_hash,
                    "delivery_id": message.delivery_id,
                    "agent_key": agent.agent_key,
                    "attachments": message._get_attachments_payload(),
                    "view_context": message.context_json
                    or request.env["ai.bridge.user.context"]
                    .sudo()
                    ._get_for_user(message.user_id, message.channel_id),
                    "capabilities": agent._get_capabilities(message),
                    "create_date": message.create_date.isoformat()
                    if message.create_date
                    else None,
                    "history": message._get_history() if include_history else [],
                }
            )
        return {
            "status": "ok",
            "agent_id": agent.id,
            "agent_key": agent.agent_key,
            "tenant": agent._get_tenant_info(),
            "agent": agent._get_agent_definition(),
            "messages": payload,
        }
