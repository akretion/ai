# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from odoo.tests import TransactionCase, tagged

from ..controllers.agent_webhook import ATTACHMENT_MAX_COUNT, AgentWebhook

try:
    import markdown  # noqa: F401
except ImportError:  # pragma: no cover - mirrors the controller fallback
    markdown = None


@tagged("post_install", "-at_install")
class TestReplyRendering(TransactionCase):
    def setUp(self):
        super().setUp()
        self.controller = AgentWebhook()
        self.user = self.env["res.users"].create(
            {
                "name": "Rendering User",
                "login": "rendering_user",
                "email": "rendering@example.com",
                "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
            }
        )
        self.channel = (
            self.env["discuss.channel"]
            .with_user(self.user)
            .create({"name": "Rendering", "channel_type": "channel"})
        )

    def test_plain_keeps_the_line_breaks(self):
        rendered = str(self.controller._format_body("first line\nsecond line"))
        self.assertIn("first line", rendered)
        self.assertIn("second line", rendered)
        self.assertIn("<br", rendered)

    def test_plain_escapes_html_and_links_urls(self):
        rendered = str(
            self.controller._format_body("<script>x</script> see https://example.com/a")
        )
        self.assertNotIn("<script>", rendered)
        self.assertIn('href="https://example.com/a"', rendered)

    def test_html_is_sanitized_not_stripped(self):
        rendered = str(
            self.controller._format_body(
                "<p>Hi <b>world</b></p><script>alert(1)</script>", "html"
            )
        )
        self.assertIn("<b>world</b>", rendered)
        self.assertNotIn("<script>", rendered)

    def test_markdown_table_becomes_a_real_table(self):
        if markdown is None:
            self.skipTest("python-markdown is not installed")
        rendered = str(
            self.controller._format_body(
                "| Partner | Total |\n| --- | --- |\n| A | 10 |", "markdown"
            )
        )
        self.assertIn("<table", rendered)
        self.assertIn("<td>A</td>", rendered)

    def test_unknown_format_falls_back_to_plain(self):
        rendered = str(self.controller._format_body("**not bold**", "wat"))
        self.assertIn("**not bold**", rendered)

    def test_attachments_are_stored_and_decoded(self):
        attachments = self.controller._create_attachments(
            self.env,
            self.channel,
            {
                "attachments": [
                    {
                        "name": "report.csv",
                        "mimetype": "text/csv",
                        "b64": base64.b64encode(b"a,b\n1,2\n").decode(),
                    }
                ]
            },
        )
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments.name, "report.csv")
        self.assertEqual(base64.b64decode(attachments.datas), b"a,b\n1,2\n")
        self.assertEqual(attachments.res_model, "discuss.channel")
        self.assertEqual(attachments.res_id, self.channel.id)

    def test_attachment_count_is_capped(self):
        items = [
            {
                "name": f"f{index}.txt",
                "b64": base64.b64encode(b"x").decode(),
            }
            for index in range(ATTACHMENT_MAX_COUNT + 3)
        ]
        attachments = self.controller._create_attachments(
            self.env, self.channel, {"attachments": items}
        )
        self.assertEqual(len(attachments), ATTACHMENT_MAX_COUNT)

    def test_invalid_base64_is_ignored(self):
        attachments = self.controller._create_attachments(
            self.env,
            self.channel,
            {"attachments": [{"name": "broken.bin", "b64": "not-base64!!"}]},
        )
        self.assertFalse(attachments)

    def test_path_separators_are_neutralised_in_names(self):
        attachments = self.controller._create_attachments(
            self.env,
            self.channel,
            {
                "attachments": [
                    {
                        "name": "../../etc/passwd",
                        "b64": base64.b64encode(b"x").decode(),
                    }
                ]
            },
        )
        self.assertNotIn("/", attachments.name)
