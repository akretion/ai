# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class AiBridgeAgentSkill(models.Model):
    _inherit = "ai.bridge.agent.skill"

    tool_ids = fields.Many2many(
        "ai.tool",
        "ai_bridge_agent_skill_tool_rel",
        "skill_id",
        "tool_id",
        string="Tools",
        help="Tools this skill expects, exactly like a bundle: loading the "
        "skill on an agent brings its tools with it. The free-text field "
        "above stays for tools the responder owns (browser, terminal).",
    )

    def _as_skill_dict(self, lazy=False):
        values = super()._as_skill_dict(lazy=lazy)
        declared = self.tool_ids.mapped("name")
        values["tools"] = list(dict.fromkeys(values["tools"] + declared))
        values["declared_tools"] = [
            self.env["ai.tool"]._bridge_tool_definition(tool) for tool in self.tool_ids
        ]
        return values
