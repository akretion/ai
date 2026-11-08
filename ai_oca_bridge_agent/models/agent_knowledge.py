# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class AiBridgeAgentKnowledge(models.Model):
    """Text the agent may use as context, exported with the agent definition.

    Deliberately plain text: the module does not index or embed anything, the
    responder does that job with its own tooling. A glue module can fill
    ``body`` from ``document.page`` or the Documents app.
    """

    _name = "ai.bridge.agent.knowledge"
    _description = "AI agent knowledge source"
    _order = "sequence, name"

    sequence = fields.Integer(default=10)
    agent_id = fields.Many2one(
        "ai.bridge.agent",
        required=True,
        ondelete="cascade",
        index=True,
    )
    name = fields.Char(required=True)
    body = fields.Html(help="Text the responder injects as context.")
    source_url = fields.Char(help="Where this knowledge comes from, for humans.")
    active = fields.Boolean(default=True)

    def _as_knowledge_dict(self):
        self.ensure_one()
        return {
            "name": self.name,
            "source": self.source_url or "",
            "text": self.body or "",
        }
