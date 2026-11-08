# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Mock tools for testing the AI agent tools endpoint without ai_tool installed.

This module provides a minimal hello_world tool so the /ai_agent/tools/* endpoints
can be tested even when no real tools are configured. In production, real tools
are either:
- Registered as ai.tool records (when ai_tool is installed)
- Provided by a companion module (e.g., ai_tool_odoo_shell)
"""

from odoo import models


class AiBridgeAgentToolsMock(models.AbstractModel):
    _name = "ai.bridge.agent.tools.mock"
    _description = "Agent mock tools (for testing)"

    def _ai_hello_world(self, name="World", **kwargs):
        """A simple hello world tool for testing.

        Returns a greeting message. Useful for verifying that the
        /ai_agent/tools/execute endpoint is working.
        """
        return {"message": f"Hello, {name}!", "tool": "hello_world"}

    def _ai_get_odoo_version(self, **kwargs):
        """Return the current Odoo version."""
        return {"version": self.env["ir.module.module"].sudo().get_current_version()}
