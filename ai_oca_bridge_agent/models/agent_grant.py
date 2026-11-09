# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models

READ_METHODS = ("search_read", "read", "fields_get", "search_count", "read_group")

METHOD_SELECTION = [
    ("search_read", "Search and read"),
    ("read", "Read by ids"),
    ("fields_get", "Describe fields"),
    ("search_count", "Count records"),
    ("read_group", "Group and aggregate"),
]


class AiBridgeAgentGrant(models.Model):
    """Data access an agent may use on behalf of a set of users.

    A grant is *not* a tool: it does not describe an operation, it bounds what
    the agent may read or create with the user's own API key. Tools answer
    "what can this assistant do", grants answer "which data may it touch".

    The rules are data, not code: a customer can open a model to the power
    users of one agent without touching the transport, and a user only ever
    sees the grants bound to their groups and to the agent they talk to.
    """

    _name = "ai.bridge.agent.grant"
    _description = "AI agent data access grant"
    _order = "sequence, name"

    sequence = fields.Integer(default=10)
    name = fields.Char(
        required=True,
        help="Short name the administrator recognises, e.g. 'partners'.",
    )
    description = fields.Char(
        required=True,
        help="One sentence describing what this grant opens.",
    )
    model_name = fields.Char(
        required=True,
        help="Odoo model the grant opens, e.g. res.partner.",
    )
    method = fields.Selection(
        METHOD_SELECTION,
        required=True,
        default="search_read",
    )
    agent_ids = fields.Many2many(
        "ai.bridge.agent",
        "ai_bridge_agent_grant_agent_rel",
        "grant_id",
        "agent_id",
        string="Agents",
        help="Agents this grant applies to. Empty means every agent.",
    )
    allowed_group_ids = fields.Many2many(
        "res.groups",
        "ai_bridge_agent_grant_group_rel",
        "grant_id",
        "group_id",
        string="Allowed groups",
        help="Users in these groups may use the grant. Admins always may.",
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
    def _for_user(self, user, agent=None):
        """Grants that apply to this user, for this agent."""
        if not user:
            return self.browse()
        if user._is_admin() or user.has_group("base.group_system"):
            domain = [("active", "=", True)]
        else:
            domain = [
                ("active", "=", True),
                ("allowed_group_ids", "in", user.groups_id.ids),
            ]
        grants = self.search(domain)
        if agent:
            grants = grants.filtered(
                lambda grant: not grant.agent_ids or agent in grant.agent_ids
            )
        return grants

    @api.model
    def _resolve_for_user(self, user, name, agent=None):
        """Return the grant if this user may use it for this agent, else None."""
        grants = self._for_user(user, agent).filtered(lambda grant: grant.name == name)
        return grants[:1]._as_grant_dict() if grants else None

    @api.model
    def _user_can_read(self, user, model_name):
        """True when that user may read the model at all (ACL, not records)."""
        if model_name not in self.env:
            return False
        model_env = self.env[model_name].with_user(user)
        # 18.0: check_access() on an empty recordset checks the model-level ACL;
        # 16.0 has no check_access(), check_access_rights() does the same there.
        checker = getattr(model_env, "check_access", None)
        if checker is not None:
            try:
                model_env.browse().check_access("read")
                return True
            except Exception:  # noqa: BLE001 - AccessError, or no access at all
                return False
        try:
            return bool(model_env.check_access_rights("read", raise_exception=False))
        except Exception:  # noqa: BLE001 - unavailable model, treat as denied
            return False

    @api.model
    def _open_access(self, user, model_name, method, agent):
        """Trust the user's own API key: read whatever they may read.

        Not quite: technical models are filtered by the agent policy first,
        because a user who may read ``ir.config_parameter`` in the interface is
        still not supposed to hand its secrets to a language model.
        """
        if method not in READ_METHODS:
            return None
        policy = agent._model_policy() if agent else self._default_model_policy()
        if not policy.allows(model_name):
            return None
        model = (
            self.env["ir.model"].sudo().search([("model", "=", model_name)], limit=1)
        )
        if not model:
            return None
        if not self._user_can_read(user, model_name):
            return None
        return {
            "name": model.display_name,
            "description": model.display_name,
            "model": model_name,
            "method": method,
            "max_limit": 80,
            "fields": [],
            "source": "open",
        }

    @api.model
    def _default_model_policy(self):
        from .agent_model_policy import ModelPolicy

        return ModelPolicy(self.env)

    @api.model
    def _readable_models(self, user, agent=None):
        """Models the assistant may query for this user, for discovery."""
        if agent and agent.tool_access_mode == "whitelist":
            names = self._for_user(user, agent).mapped("model_name")
        else:
            excluded = agent.tool_excluded_model_ids.mapped("model") if agent else []
            policy = agent._model_policy() if agent else self._default_model_policy()
            names = []
            for model in self.env["ir.model"].sudo().search([]):
                if model.model in excluded or model.model not in self.env:
                    continue
                if not policy.allows(model.model):
                    continue
                if self._user_can_read(user, model.model):
                    names.append(model.model)
        return sorted(set(names))

    @api.model
    def _resolve_for_user_model(self, user, model_name, method, agent=None):
        """Grant lookup by (model, method): how the agent validates a call.

        ``fields_get`` only exposes the schema Odoo already filters by access
        rights, and ``search_count`` only counts the rows the same grant already
        lets the user read. Both are implied by a read grant on the same model,
        so an administrator does not duplicate a record per model, and the
        assistant does not have to page through thousands of rows to answer
        "how many".
        """
        if agent and model_name in agent.tool_excluded_model_ids.mapped("model"):
            return None
        policy = agent._model_policy() if agent else self._default_model_policy()
        if not policy.allows(model_name):
            return None
        grants = self._for_user(user, agent).filtered(
            lambda grant: grant.model_name == model_name and grant.method == method
        )
        if not grants and method in ("fields_get", "search_count"):
            grants = self._for_user(user, agent).filtered(
                lambda grant: grant.model_name == model_name
            )
        if grants:
            grant = grants[:1]._as_grant_dict()
            grant["method"] = method
            return grant
        mode = agent.tool_access_mode if agent else "open"
        if mode != "open":
            return None
        return self._open_access(user, model_name, method, agent)

    def _as_grant_dict(self):
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
            "source": "grant",
        }
