# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import AccessError


class AiTool(models.Model):
    _inherit = "ai.tool"

    agent_ids = fields.Many2many(
        "ai.bridge.agent",
        "ai_bridge_agent_tool_rel",
        "tool_id",
        "agent_id",
        string="Agents",
        help="Bridge agents allowed to run this tool.",
    )
    readonly = fields.Boolean(
        help="The tool only reads. An agent in 'Only read-only tools' mode "
        "refuses every other tool, which is how a customer keeps an assistant "
        "helpful without letting it change anything.",
    )

    @api.model
    def _bridge_tool_definition(self, tool):
        """Definition handed to the responder, with the flags it needs."""
        definition = tool._get_tool_definition()
        definition["readonly"] = bool(tool.readonly)
        definition["kind"] = tool.kind
        return definition

    def _tool_model_name(self):
        """Model the tool's *record* belongs to, when the tool needs one.

        Only a ``record`` tool names its model: for ``generic_model`` the
        function lives on ``model_id`` but takes any record, and for a
        ``server_action`` the model comes from the action. Confusing the
        function's model with the record's model would browse the wrong table.
        """
        self.ensure_one()
        if self.kind == "record":
            return self.model_id.model
        if "server_action_id" in self._fields and self.kind == "server_action":
            return self.server_action_id.model_id.model or None
        return None

    def _function_model_name(self):
        """Model holding the Python function, for the generic kinds."""
        self.ensure_one()
        return self.model_id.model if self.model_id else None

    def _is_usable_by(self, user):
        """A record tool is usable when the user may read its model.

        The tool itself then runs with the user's own env, so this check is a
        courtesy that produces a clean refusal, not the boundary: ACLs and
        record rules apply again at execution time.
        """
        self.ensure_one()
        model_name = self._tool_model_name()
        if not model_name:
            return True
        return self.env["ai.bridge.agent.grant"]._user_can_read(user, model_name)

    def _dispatch_for_user(self, user, arguments):
        """Run the tool in an env bound to that user, never as sudo.

        ``ai.tool._execute_tool`` cannot be reused as is: it reads
        ``self.model_id`` inside the record's own env, and a normal user has no
        access to ``ir.model``, so the call dies before reaching the tool. We
        read the definition as the administrator (configuration, not business
        data) and execute every operation in the user's env.
        """
        tool = self.sudo()
        user_env = self.env(user=user, su=False)
        kind = tool.kind
        arguments = dict(arguments or {})
        function_model = tool._function_model_name()
        if kind == "record":
            record = arguments.pop("record", None)
            if not record:
                raise AccessError(
                    _("The tool %(name)s needs a record.", name=tool.name)
                )
            if record._name != function_model:
                raise AccessError(
                    _(
                        "The tool %(name)s works on %(model)s, not on %(record)s.",
                        name=tool.name,
                        model=function_model,
                        record=record._name,
                    )
                )
            return getattr(record, tool.function_name)(**arguments) or {}
        if kind in ("generic", "generic_model"):
            if not function_model:
                raise AccessError(_("The tool %(name)s has no model.", name=tool.name))
            return (
                getattr(user_env[function_model], tool.function_name)(**arguments) or {}
            )
        if kind == "server_action":
            action = tool.server_action_id.with_user(user)
            record = arguments.pop("record", None)
            active_id = record.id if record else False
            active_model = (
                record._name
                if record
                else (tool.server_action_id.model_id.model or False)
            )
            result = action.with_context(
                tool_args=arguments,
                active_id=active_id,
                active_ids=[active_id] if active_id else [],
                active_model=active_model,
            ).run()
            if isinstance(result, dict) and str(result.get("type", "")).startswith(
                "ir.actions."
            ):
                return {"ok": True}
            return result if isinstance(result, dict) else {"result": result}
        raise AccessError(
            _("The tool %(name)s has an unsupported kind.", name=tool.name)
        )

    def _run_for_user(self, user, arguments, record_model=None, record_id=None):
        """Run the tool as that user, never as the agent's AI user."""
        self.ensure_one()
        tool = self.sudo()
        needs_record = tool.kind in ("record", "generic_model")
        if needs_record and not record_id:
            raise AccessError(
                _("The tool %(name)s needs a record to work on.", name=tool.name)
            )
        record = None
        if record_id:
            model_name = tool._tool_model_name() or record_model
            if not model_name:
                raise AccessError(
                    _("The tool %(name)s does not work on a record.", name=tool.name)
                )
            if model_name not in self.env:
                raise AccessError(_("Unknown model %(model)s.", model=model_name))
            if not self.env["ai.bridge.agent.grant"]._user_can_read(user, model_name):
                raise AccessError(
                    _("You are not allowed to read %(model)s.", model=model_name)
                )
            record = self.env[model_name].with_user(user).browse(int(record_id))
            if not record.exists():
                raise AccessError(_("That record does not exist or is not accessible."))
            # Raises when a record rule keeps it out of reach for that user.
            record.check_access("read")
        arguments = dict(arguments or {})
        if tool.kind in ("record", "generic_model"):
            arguments["record"] = record
        return tool._dispatch_for_user(user, arguments)
