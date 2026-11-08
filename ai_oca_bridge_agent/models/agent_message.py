# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import timedelta
from functools import partial

from odoo import api, fields, models
from odoo.tools.mail import html2plaintext


class AiBridgeAgentMessage(models.Model):
    _name = "ai.bridge.agent.message"
    _description = "Messages queued for AI agent"
    _order = "create_date asc, id asc"

    channel_id = fields.Many2one("discuss.channel", required=True, index=True)
    body = fields.Text(required=True)
    body_text = fields.Text(compute="_compute_body_text")
    author_id = fields.Many2one("res.partner", string="Author")
    author_name = fields.Char(related="author_id.name", store=True)
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("processing", "Processing"),
            ("pushed", "Pushed to agent"),
            ("done", "Done"),
            ("error", "Error"),
            ("dead", "Dead"),
        ],
        default="pending",
        index=True,
    )
    company_id = fields.Many2one(
        "res.company",
        default=lambda self: self.env.company,
        index=True,
    )
    user_id = fields.Many2one(
        "res.users",
        string="Requesting User",
        index=True,
        help="Odoo user the message belongs to. The agent uses it to call "
        "Odoo with that user's own access rights.",
    )
    user_has_api_key = fields.Boolean(
        readonly=True,
        help="Tell the agent whether the requesting user gave it an Odoo API "
        "key, i.e. whether it may call Odoo on their behalf.",
    )
    session_key = fields.Char(
        index=True,
        help="AI agent session this message belongs to.",
    )
    delivery_id = fields.Char(
        index=True,
        help="Idempotency key of the event pushed to the agent.",
    )
    attempts = fields.Integer(default=0, readonly=True)
    next_attempt_at = fields.Datetime(readonly=True)
    claimed_at = fields.Datetime(readonly=True)
    claimed_by = fields.Char(readonly=True)
    last_http_status = fields.Integer(readonly=True)
    response_message_id = fields.Many2one(
        "mail.message",
        string="Response Message",
        readonly=True,
    )
    tokens_in = fields.Integer(default=0, readonly=True)
    tokens_out = fields.Integer(default=0, readonly=True)
    agent_id = fields.Many2one("ai.bridge.agent", required=True, index=True)
    db_hash = fields.Char(help="Database hash for AI agent session key.")
    error_message = fields.Text()
    message_type = fields.Selection(
        [
            ("user", "User Message"),
            ("system", "System Event"),
            ("command", "Command"),
        ],
        default="user",
    )
    escalation_id = fields.Many2one(
        "mail.message",
        string="Escalation Message",
        help="Reference to the escalation message or ticket. Used by child modules.",
    )
    is_escalated = fields.Boolean(
        default=False,
        help="Checked when a human has taken over the conversation.",
    )

    def _compute_body_text(self):
        for message in self:
            message.body_text = html2plaintext(message.body or "")

    def _get_history(self, limit=20):
        """Return the recent conversation of the channel, oldest first."""
        self.ensure_one()
        messages = (
            self.env["mail.message"]
            .sudo()
            .search(
                [
                    ("res_id", "=", self.channel_id.id),
                    ("model", "=", "discuss.channel"),
                    ("message_type", "=", "comment"),
                ],
                order="date desc",
                limit=limit,
            )
        )
        return [
            {
                "role": "assistant"
                if message.author_id.user_ids.ai_bridge_id
                else "user",
                "body": message.body,
                "author_name": message.author_id.name,
                "date": fields.Datetime.to_string(message.date)
                if message.date
                else None,
            }
            for message in reversed(messages)
        ]

    def _claim_batch(self, agent, limit=None, worker="odoo"):
        """Claim pending messages for one agent, atomically.

        Several agent workers may poll concurrently: the ``FOR UPDATE SKIP
        LOCKED`` clause guarantees that two workers never receive the same
        row, whatever the number of Odoo workers behind the HTTP endpoint.
        """
        limit = limit or agent.max_batch_size or 50
        now = fields.Datetime.now()
        self.flush_model(
            ["state", "next_attempt_at", "claimed_at", "claimed_by", "attempts"]
        )
        # The table name follows the model, never hardcoded in the SQL.
        table = self._table
        self.env.cr.execute(
            f"""
            WITH claimed AS (
                SELECT id
                  FROM {table}
                 WHERE agent_id = %s
                   AND state = 'pending'
                   AND (next_attempt_at IS NULL OR next_attempt_at <= %s)
                 ORDER BY id
                 LIMIT %s
                   FOR UPDATE SKIP LOCKED
            )
            UPDATE {table} q
               SET state = 'processing',
                   claimed_at = %s,
                   claimed_by = %s,
                   attempts = q.attempts + 1
              FROM claimed
             WHERE q.id = claimed.id
            RETURNING q.id
            """,
            (agent.id, now, limit, now, worker),
        )
        ids = sorted(row[0] for row in self.env.cr.fetchall())
        records = self.browse(ids)
        records.invalidate_recordset()
        return records

    def _requeue_stale(self, agent):
        """Hand back messages claimed but never answered, or mark them dead."""
        deadline = fields.Datetime.now() - timedelta(
            minutes=agent.claim_ttl_minutes or 5
        )
        stale = self.search(
            [
                ("agent_id", "=", agent.id),
                ("state", "in", ("processing", "pushed")),
                ("claimed_at", "<", deadline),
            ]
        )
        max_attempts = agent.max_attempts or 5
        for message in stale:
            if message.attempts >= max_attempts:
                message.write(
                    {"state": "dead", "claimed_at": False, "claimed_by": False}
                )
            else:
                message.write(
                    {
                        "state": "pending",
                        "claimed_at": False,
                        "claimed_by": False,
                        "next_attempt_at": False,
                    }
                )
        return stale

    def _mark_error(self, reason):
        """Record a delivery failure: retry with a backoff, or give up."""
        self.ensure_one()
        values = {"error_message": reason, "claimed_at": False, "claimed_by": False}
        if self.attempts >= (self.agent_id.max_attempts or 5):
            values["state"] = "dead"
        else:
            delay = min(2 ** max(self.attempts, 1), 60)
            values["state"] = "pending"
            values["next_attempt_at"] = fields.Datetime.now() + timedelta(minutes=delay)
        self.write(values)

    def _mark_done(self, response_message_id=False):
        """Close the message after its answer has been posted."""
        self.write(
            {
                "state": "done",
                "claimed_at": False,
                "claimed_by": False,
                "error_message": False,
                "response_message_id": response_message_id,
            }
        )

    def _mark_pushed(self, http_status):
        """The agent accepted the event; its answer is pending."""
        self.write(
            {"state": "pushed", "last_http_status": http_status, "error_message": False}
        )

    def _schedule_push(self):
        """Push the messages to the agent once the transaction is committed.

        The callback runs after the commit, so the agent never receives a
        message the database could still roll back.
        """
        for message in self:
            agent = message.agent_id
            if agent.push_enabled and agent.push_url:
                self.env.cr.postcommit.add(partial(agent.sudo()._push_message, message))

    @api.model
    def _cron_push_queue(self):
        """Safety net: push the messages the post-commit hook could not send."""
        now = fields.Datetime.now()
        agents = (
            self.env["ai.bridge.agent"]
            .sudo()
            .search([("push_enabled", "=", True), ("push_url", "!=", False)])
        )
        for agent in agents:
            self.sudo()._requeue_stale(agent)
            due = self.sudo().search(
                [
                    ("agent_id", "=", agent.id),
                    ("state", "=", "pending"),
                    "|",
                    ("next_attempt_at", "=", False),
                    ("next_attempt_at", "<=", now),
                ],
                order="id asc",
                limit=agent.max_batch_size or 50,
            )
            for message in due:
                agent._push_message(message)

    @api.model
    def _find_by_delivery(self, agent, delivery_id):
        """Return the message already answered under ``delivery_id``, if any."""
        if not delivery_id:
            return self.browse()
        return self.search(
            [("agent_id", "=", agent.id), ("delivery_id", "=", delivery_id)],
            limit=1,
        )

    def action_retry(self):
        """Reset failed messages to pending for reprocessing."""
        self.filtered(lambda m: m.state == "error").write(
            {"state": "pending", "error_message": False}
        )

    def _cleanup_old_messages(self):
        """Cron job: delete messages older than 7 days in done/error state."""
        cutoff = fields.Datetime.now() - timedelta(days=7)
        old_messages = self.search(
            [
                ("create_date", "<", cutoff),
                ("state", "in", ["done", "error", "pushed"]),
            ]
        )
        if old_messages:
            old_messages.unlink()
