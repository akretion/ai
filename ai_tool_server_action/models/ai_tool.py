# Copyright 2026 SDi
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import json

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class AiTool(models.Model):
    _inherit = "ai.tool"

    kind = fields.Selection(
        selection_add=[("server_action", "Server Action")],
        ondelete={"server_action": "cascade"},
    )
    model_id = fields.Many2one(required=False)
    function_name = fields.Char(required=False)

    server_action_id = fields.Many2one(
        "ir.actions.server",
        string="Server Action",
        help="The server action to execute when this tool is called.",
    )
    input_schema_json = fields.Text(
        string="Input Schema (JSON)",
        help="Define the expected input schema for the LLM in JSON format.",
    )
    output_schema_json = fields.Text(
        string="Output Schema (JSON)",
        help="Define the expected output schema for the LLM in JSON format.",
    )

    @api.constrains("input_schema_json", "output_schema_json")
    def _check_schemas_json(self):
        for tool in self:
            for field_name in ("input_schema_json", "output_schema_json"):
                raw = tool[field_name]
                if not raw:
                    continue
                try:
                    schema = json.loads(raw)
                except ValueError as exc:
                    raise ValidationError(
                        _(
                            "The %(field_label)s of AI tool '%(tool_name)s' "
                            "is not valid JSON: %(error)s",
                            field_label=tool._fields[field_name].string,
                            tool_name=tool.name,
                            error=exc,
                        )
                    ) from exc
                if not isinstance(schema, dict):
                    raise ValidationError(
                        _(
                            "The %(field_label)s of AI tool '%(tool_name)s' "
                            "must be a JSON object.",
                            field_label=tool._fields[field_name].string,
                            tool_name=tool.name,
                        )
                    )

    def _get_tool_definition(self):
        if self.kind == "server_action":
            input_schema = {}
            if self.input_schema_json:
                try:
                    input_schema = json.loads(self.input_schema_json)
                except ValueError:
                    input_schema = {}
            output_schema = {}
            if self.output_schema_json:
                try:
                    output_schema = json.loads(self.output_schema_json)
                except ValueError:
                    output_schema = {}
            return {
                "name": self.name,
                "description": self.description,
                "inputSchema": input_schema,
                "outputSchema": output_schema,
            }
        return super()._get_tool_definition()

    def _execute_tool(self, *args, record=None, **kwargs):
        if self.kind == "server_action":
            # Extract active_id and active_model from record, or fallback
            # to kwargs (useful for MCP integration)
            active_id = record.id if record else kwargs.get("active_id", False)
            active_model = (
                record._name
                if record
                else kwargs.get("active_model", self.server_action_id.model_id.model)
            )
            active_ids = [active_id] if active_id else kwargs.get("active_ids", [])

            action = self.server_action_id.with_context(
                tool_args=kwargs,
                active_id=active_id,
                active_ids=active_ids,
                active_model=active_model,
            )
            result = action.run()
            if isinstance(result, dict):
                # If the action returns a UI action (like opening a window),
                # we ignore it and return a success message since
                # the AI cannot interact with the UI.
                if result.get("type") and str(result.get("type")).startswith(
                    "ir.actions."
                ):
                    return {
                        "status": "success",
                        "message": "Action executed successfully (UI action ignored)",
                    }
                return result

            # For actions that return False/None (like Update Record, Send Email)
            return {
                "status": "success",
                "message": "Action executed successfully",
            }
        return super()._execute_tool(*args, record=record, **kwargs)
