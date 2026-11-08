# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "AI OCA Bridge Hermes",
    "summary": """
        Integrate Hermes AI Agent as a native chatbot inside Odoo Discuss.
        Requires a running Hermes gateway with the Odoo platform adapter.
    """,
    "version": "18.0.1.5.0",
    "license": "AGPL-3",
    "author": "Akretion,Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/ai",
    "category": "AI",
    "development_status": "Beta",
    "depends": [
        "ai_oca_bridge_chatter",
    ],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/hermes_gateway_views.xml",
        "views/discuss_channel_views.xml",
        "views/menu.xml",
        "views/hermes_tool_views.xml",
        "views/res_users.xml",
        "data/ir_cron.xml",
        "data/hermes_tool_data.xml",
    ],
    "demo": [],
    "installable": True,
    "application": False,
}
