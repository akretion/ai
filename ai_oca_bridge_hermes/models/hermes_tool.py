# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models

METHOD_SELECTION = [
    ("search_read", "Search and read"),
    ("read", "Read by ids"),
    ("fields_get", "Describe fields"),
    ("search_count", "Count records"),
    ("read_group", "Group and aggregate"),
]


class HermesTool(models.Model):
    """A whitelisted read access the assistant may use for a set of users.

    The whitelist is data, not code: an administrator can open a model to the
    power users of one customer without touching the transport, and a user only
    ever sees the tools bound to the groups they already belong to.
    """

    _name = "hermes.tool"
    _description = "Hermes tool allowed for the assistant"
    _order = "sequence, name"

    sequence = fields.Integer(default=10)
    name = fields.Char(
        required=True,
        help="Technical name the assistant calls, e.g. 'partners'.",
    )
    description = fields.Char(
        required=True,
        help="One sentence the assistant reads to decide when to use the tool.",
    )
    model_name = fields.Char(
        required=True,
        help="Odoo model the tool queries, e.g. res.partner.",
    )
    method = fields.Selection(
        METHOD_SELECTION,
        required=True,
        default="search_read",
    )
    allowed_group_ids = fields.Many2many(
        "res.groups",
        "hermes_tool_group_rel",
        "tool_id",
        "group_id",
        string="Allowed groups",
        help="Users in these groups may use the tool. Admins always may.",
    )
    max_limit = fields.Integer(
        default=40,
        help="Maximum number of records returned by one call.",
    )
    field_names = fields.Char(
        help="Comma separated fields to return. Empty: every field the user "
        "may read on that model.",
    )
    active = fields.Boolean(default=True)

    @api.model
    def _for_user(self, user):
        if not user:
            return self.browse()
        if user._is_admin() or user.has_group("base.group_system"):
            return self.search([("active", "=", True)])
        return self.search(
            [
                ("active", "=", True),
                ("allowed_group_ids", "in", user.groups_id.ids),
            ]
        )

    @api.model
    def _resolve_for_user(self, user, name):
        """Return the tool definition if this user may use it, else None."""
        tools = self._for_user(user).filtered(lambda tool: tool.name == name)
        return tools[:1]._as_tool_dict() if tools else None

    @api.model
    def _resolve_for_user_model(self, user, model_name, method):
        """Grant lookup by (model, method): how the gateway validates a call.

        ``fields_get`` only exposes the schema Odoo already filters by access
        rights, and ``search_count`` only counts the rows the same grant already
        lets the user read. Both are implied by a read grant on the same model,
        so an administrator does not duplicate a record per model, and the
        assistant does not have to page through thousands of rows to answer
        "how many".
        """
        grants = self._for_user(user).filtered(
            lambda tool: tool.model_name == model_name and tool.method == method
        )
        if not grants and method in ("fields_get", "search_count"):
            grants = self._for_user(user).filtered(
                lambda tool: tool.model_name == model_name
            )
        if not grants:
            return None
        tool = grants[:1]._as_tool_dict()
        tool["method"] = method
        return tool

    def _as_tool_dict(self):
        self.ensure_one()
        return {
            "name": self.name,
            "description": self.description,
            "model": self.model_name,
            "method": self.method,
            "max_limit": self.max_limit,
            "fields": [
                field.strip()
                for field in (self.field_names or "").split(",")
                if field.strip()
            ],
        }
