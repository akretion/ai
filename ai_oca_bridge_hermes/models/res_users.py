# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    hermes_gateway_ids = fields.Many2many(
        "hermes.gateway",
        string="Hermes Gateways",
        help="Hermes gateways this user is linked to.",
    )
    hermes_has_api_key = fields.Boolean(
        compute="_compute_hermes_has_api_key",
        string="Hermes: Odoo API Key",
        help="Checked when the user owns at least one Odoo API key, i.e. when "
        "Hermes may call Odoo on their behalf with their own rights.",
    )

    def _compute_hermes_has_api_key(self):
        for user in self:
            user.hermes_has_api_key = bool(user.api_key_ids)
