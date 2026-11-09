# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class AiBridgeAgent(models.Model):
    _inherit = "ai.bridge.agent"

    tool_ids = fields.Many2many(
        "ai.tool",
        "ai_bridge_agent_tool_rel",
        "agent_id",
        "tool_id",
        string="Tools",
        help="Declared operations this agent may perform. Empty means the "
        "agent performs none: tools are assigned explicitly, because a tool "
        "that mutates records must not reach an agent by accident. Skills "
        "bring their own tools on top of these. Data read is a separate "
        "question, answered by the grants under Hermes > Data access.",
    )
    readonly_tools = fields.Boolean(
        string="Only read-only tools",
        help="Refuse every tool not flagged as read-only. Use it for an agent "
        "whose answer must never change a record.",
    )

    def _get_declared_tools(self, user):
        """Tools of this agent: its own, plus the ones its skills carry.

        Same idea as the Enterprise skills bundles: a skill is a prompt *and*
        the tools it expects, so reusing the skill does not mean re-picking
        tools on every agent.
        """
        self.ensure_one()
        tools = self.tool_ids | self.skill_ids.tool_ids
        tools = tools.filtered(lambda tool: tool._is_usable_by(user))
        if self.readonly_tools:
            tools = tools.filtered("readonly")
        return tools

    def _find_declared_tool(self, user, name):
        """The tool this agent may run under that name, if usable."""
        self.ensure_one()
        tools = (self.tool_ids | self.skill_ids.tool_ids).filtered(
            lambda tool: tool.name == name
        )
        if len(tools) > 1:
            # Two tools sharing a name would make the call ambiguous, which is
            # the kind of accident a silent pick would hide.
            return None
        tools = tools.filtered(lambda tool: tool._is_usable_by(user))
        if self.readonly_tools:
            tools = tools.filtered("readonly")
        return tools[:1]
