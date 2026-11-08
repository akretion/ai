# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "AI OCA Bridge Agent",
    "summary": """
        Realtime bridge between Odoo Discuss and an external AI agent runtime,
        with per-user identity, an Odoo access policy and a tool whitelist.
        Backend agnostic: Hermes Agent is the reference implementation.
    """,
    "version": "18.0.2.1.1",
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
