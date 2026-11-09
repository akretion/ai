# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class AiBridgeAgentSkill(models.Model):
    """A reusable block of instructions an agent may load.

    A skill is content, not code: a prompt fragment plus the tools it is
    expected to use. The responder decides how to load it (Hermes maps it to a
    skill, n8n can inject it as a prompt section).
    """

    _name = "ai.bridge.agent.skill"
    _description = "AI agent skill"
    _order = "sequence, name"

    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    summary = fields.Char(help="One line shown in the agent form.")
    instructions = fields.Html(
        help="What the agent must do when this skill is loaded, and how.",
    )
    tool_names = fields.Char(
        help="Comma separated tool names this skill expects, for the "
        "responder's own tool policy.",
    )
    agent_ids = fields.Many2many(
        "ai.bridge.agent",
        "ai_bridge_agent_skill_rel",
        "skill_id",
        "agent_id",
        string="Agents",
    )
    active = fields.Boolean(default=True)

    def _as_skill_dict(self, lazy=False):
        """Describe the skill to the responder.

        In lazy mode the text is withheld: the model gets the summary and loads
        the body through ``/ai_agent/skills/get`` only when it needs it, the
        same progressive disclosure OCA ``ai_tool_skill`` gets by making a
        skill a callable ``ai.tool``.
        """
        self.ensure_one()
        return {
            "name": self.name,
            "summary": self.summary or "",
            "instructions": "" if lazy else (self.instructions or ""),
            "lazy": bool(lazy and self.instructions),
            "tools": [
                name.strip()
                for name in (self.tool_names or "").split(",")
                if name.strip()
            ],
        }
