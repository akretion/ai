# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Which models the assistant may touch at all.

The API key already limits reads to what the user may see, but technical models
are readable by nearly everyone and some of them hold secrets that no user ever
sees in the interface: ``ir.config_parameter`` carries SMTP passwords and
provider keys, ``ir.mail_server`` the SMTP password in clear, ``ir.attachment``
files posted by anyone, ``res.users.apikeys`` only hashes but is still a map of
who holds a key. A user's own rights are therefore not enough: technical
configuration stays out unless an administrator says otherwise.

The policy is deny-by-default for ``ir.*``, with an allowlist of the models an
assistant legitimately needs to describe the interface (menus, actions, views,
models and their fields), plus per-agent and instance-wide overrides.
"""

import os

#: Never readable, whatever the configuration says.
HARD_DENIED = frozenset(
    {
        "res.users.apikeys",
        "res.users.identitycheck",
        "ir.config_parameter",  # read through the config endpoint, key by key
        "ir.mail_server",
        "auth.totp",
        "auth.passkey",
    }
)

#: ``ir.*`` models the assistant may read when nothing else says otherwise.
IR_ALLOWED = frozenset(
    {
        "ir.model",
        "ir.model.fields",
        "ir.model.data",
        "ir.model.access",  # read-only: who may do what, useful to explain refusals
        "ir.ui.menu",
        "ir.ui.view",
        "ir.ui.view.custom",
        "ir.actions.act_window",
        "ir.actions.act_url",
        "ir.actions.client",
        "ir.actions.report",
        "ir.actions.act_window_close",
        "ir.module.module",
        "ir.module.category",
        "ir.module.module.dependency",
        "ir.cron",  # read-only: "what runs at night" is a support question
        "ir.filters",
        "ir.exports",
        "ir.exports.line",
    }
)

#: Prefixes denied by default, including every ``ir.*`` model not in the list.
DEFAULT_DENIED_PREFIXES = ("ir.",)

PARAM_ALLOW = "ai_bridge_agent.model_allow"
PARAM_DENY = "ai_bridge_agent.model_deny"
ENV_ALLOW = "AI_AGENT_MODEL_ALLOW"
ENV_DENY = "AI_AGENT_MODEL_DENY"


def _split(values):
    """Turn a CSV string or a list into a set of stripped names."""
    if not values:
        return set()
    if isinstance(values, str):
        values = values.replace("\n", ",").split(",")
    return {str(value).strip() for value in values if str(value).strip()}


class ModelPolicy:
    """Resolve the effective model policy for one agent.

    Precedence, strongest first: the hard deny list, an explicit deny (agent,
    environment, instance), an explicit allow, then the ``ir.*`` allowlist, then
    the open default. Explicit deny always wins over explicit allow, so an
    operator can shut a model everywhere even if an agent reopens it.
    """

    def __init__(self, env=None, agent=None):
        self.agent = agent
        self.allow = set()
        self.deny = set()
        self.deny_prefixes = set(DEFAULT_DENIED_PREFIXES)
        if agent is not None:
            self.allow |= _split(agent.model_allowlist)
            self.deny |= _split(agent.model_denylist)
        self.allow |= _split(os.environ.get(ENV_ALLOW))
        self.deny |= _split(os.environ.get(ENV_DENY))
        if env is not None:
            params = env["ir.config_parameter"].sudo()
            self.allow |= _split(params.get_param(PARAM_ALLOW))
            self.deny |= _split(params.get_param(PARAM_DENY))

    def allows(self, model_name):
        if not model_name or model_name in HARD_DENIED:
            return False
        if model_name in self.deny:
            return False
        if any(model_name.startswith(prefix) for prefix in self.deny_prefixes):
            if model_name not in IR_ALLOWED and model_name not in self.allow:
                return False
        if model_name in HARD_DENIED:
            return False
        return True
