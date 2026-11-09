# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


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
        "that mutates records must not reach an agent by accident. Data read "
        "is a separate question, answered by the grants under Hermes > Data "
        "access.",
    )

    @api.model
    def _find_declared_tool(self, user, name):
        """The tool assigned to this agent under that name, if usable."""
        self.ensure_one()
        tools = self.tool_ids.filtered(lambda tool: tool.name == name)
        if len(tools) > 1:
            # Two tools sharing a name would make the call ambiguous, which is
            # the kind of accident a silent pick would hide.
            return None
        return tools.filtered(lambda tool: tool._is_usable_by(user))[:1]

    def _get_declared_tools(self, user):
        """Tools this agent may offer to that user, with their schemas."""
        self.ensure_one()
        return self.tool_ids.filtered(lambda tool: tool._is_usable_by(user))
