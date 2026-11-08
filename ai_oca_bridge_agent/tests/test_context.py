# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestBrowsingContext(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = cls.env["res.users"].create(
            {
                "name": "Context User",
                "login": "context_user",
                "email": "context_user@example.com",
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            }
        )
        cls.Context = cls.env["ai.bridge.user.context"]

    def test_parse_classic_web_hash(self):
        parsed = self.Context._parse_url(
            "https://odoo.example.com/web#action=123&model=sale.order&id=42&view_type=form"
        )
        self.assertEqual(parsed["action"], "123")
        self.assertEqual(parsed["model"], "sale.order")
        self.assertEqual(parsed["id"], "42")
        self.assertEqual(parsed["view_type"], "form")

    def test_parse_new_style_path(self):
        parsed = self.Context._parse_url(
            "https://odoo.example.com/odoo/action-55/7?view_type=kanban"
        )
        self.assertEqual(parsed["action"], "55")
        self.assertEqual(parsed["id"], "7")
        self.assertEqual(parsed["view_type"], "kanban")

    def test_parse_garbage_is_empty(self):
        self.assertEqual(self.Context._parse_url(""), {})
        self.assertEqual(self.Context._parse_url("not a url"), {})

    def test_record_upserts_one_row_per_user(self):
        self.Context._record(
            self.user, {"url": "/web#model=res.partner", "action_name": "Contacts"}
        )
        self.Context._record(
            self.user, {"url": "/web#model=sale.order&id=3", "action_name": "Orders"}
        )
        rows = self.Context.search([("user_id", "=", self.user.id)])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.model_name, "sale.order")
        self.assertEqual(rows.res_id, 3)
        self.assertEqual(rows.action_name, "Orders")

    def test_empty_url_is_ignored(self):
        self.Context._record(self.user, {"url": "   "})
        self.assertFalse(self.Context.search([("user_id", "=", self.user.id)]))

    def test_context_dict_marks_staleness(self):
        context = self.Context._record(self.user, {"url": "/web#model=res.partner"})
        payload = context._as_context_dict()
        self.assertFalse(payload["stale"])
        self.assertEqual(payload["model"], "res.partner")
        self.assertIn("hint", payload["source"])
        context.updated_at = "2000-01-01 00:00:00"
        self.assertTrue(context._as_context_dict()["stale"])

    def test_cleanup_removes_old_contexts(self):
        context = self.Context._record(self.user, {"url": "/web#model=res.partner"})
        context.updated_at = "2000-01-01 00:00:00"
        self.Context._cron_cleanup()
        self.assertFalse(context.exists())
