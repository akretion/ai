# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "AI Bridge Agent - AI Tool",
    "summary": "Let a bridge agent run OCA AI tools under the user's own rights",
    "version": "18.0.1.0.0",
    "license": "AGPL-3",
    "author": "Akretion,Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/ai",
    "category": "Productivity/AI",
    "depends": [
        "ai_oca_bridge_agent",
        "ai_tool",
    ],
    "data": [
        "views/agent_views.xml",
    ],
    "installable": True,
}
