# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestHermesTools(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = cls.env["res.users"].create(
            {
                "name": "Tool User",
                "login": "tool_user",
                "email": "tool_user@example.com",
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            }
        )
        cls.outsider = cls.env["res.users"].create(
            {
                "name": "Tool Outsider",
                "login": "tool_outsider",
                "email": "tool_outsider@example.com",
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            }
        )
        cls.power_group = cls.env["res.groups"].create({"name": "Hermes Power Users"})
        cls.env.ref("ai_oca_bridge_hermes.tool_partners").allowed_group_ids = [
            (6, 0, [cls.env.ref("base.group_user").id])
        ]

    def test_default_tools_are_read_only_search_read(self):
        tool = self.env["hermes.tool"]._resolve_for_user(self.user, "partners")
        self.assertEqual(tool["model"], "res.partner")
        self.assertEqual(tool["method"], "search_read")
        self.assertEqual(tool["max_limit"], 40)
        self.assertTrue(tool["description"])

    def test_user_sees_only_their_groups_tools(self):
        self.env["hermes.tool"].create(
            {
                "name": "power_moves",
                "description": "All invoices",
                "model_name": "account.move",
                "allowed_group_ids": [(6, 0, [self.power_group.id])],
            }
        )
        user_tools = self.env["hermes.tool"]._for_user(self.user)
        self.assertNotIn("power_moves", user_tools.mapped("name"))
        self.assertIsNone(
            self.env["hermes.tool"]._resolve_for_user(self.user, "power_moves")
        )

        self.outsider.groups_id = [(4, self.power_group.id)]
        self.assertIn(
            "power_moves",
            self.env["hermes.tool"]._for_user(self.outsider).mapped("name"),
        )
        tool = self.env["hermes.tool"]._resolve_for_user(self.outsider, "power_moves")
        self.assertEqual(tool["model"], "account.move")

    def test_unknown_tool_is_refused(self):
        self.assertIsNone(
            self.env["hermes.tool"]._resolve_for_user(self.user, "does_not_exist")
        )

    def test_archived_tool_is_refused(self):
        self.env.ref("ai_oca_bridge_hermes.tool_products").active = False
        self.assertIsNone(
            self.env["hermes.tool"]._resolve_for_user(self.user, "products")
        )

    def test_admin_sees_every_tool(self):
        tool = self.env["hermes.tool"]._resolve_for_user(
            self.env.ref("base.user_root"), "partners"
        )
        self.assertIsNotNone(tool)

    def test_field_whitelist_is_parsed(self):
        tool = self.env.ref("ai_oca_bridge_hermes.tool_partners")
        tool.field_names = "name, vat ,"
        self.assertEqual(tool._as_tool_dict()["fields"], ["name", "vat"])

    def test_fields_get_is_implied_by_a_read_grant(self):
        tool = self.env["hermes.tool"]._resolve_for_user_model(
            self.user, "res.partner", "fields_get"
        )
        self.assertIsNotNone(tool)

    def test_implied_fields_get_does_not_grant_reading(self):
        tool = self.env["hermes.tool"]._resolve_for_user_model(
            self.user, "sale.order", "fields_get"
        )
        self.assertIsNone(tool)

    def test_user_without_group_has_no_tool(self):
        tool_model = self.env["hermes.tool"]
        tool_model.env.ref("ai_oca_bridge_hermes.tool_partners").allowed_group_ids = [
            (6, 0, [self.power_group.id])
        ]
        self.assertFalse(tool_model._resolve_for_user(self.user, "partners"))
