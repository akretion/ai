# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestAttachmentDownload(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ai_user = cls.env["res.users"].create(
            {
                "name": "Download AI",
                "login": "download_ai",
                "email": "download_ai@example.com",
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            }
        )
        cls.user = cls.env["res.users"].create(
            {
                "name": "Download User",
                "login": "download_user",
                "email": "download_user@example.com",
                "groups_id": [
                    (
                        6,
                        0,
                        [
                            cls.env.ref("base.group_user").id,
                            cls.env.ref("ai_oca_bridge_agent.group_agent_user").id,
                        ],
                    )
                ],
            }
        )
        cls.agent = cls.env["ai.bridge.agent"].create(
            {"name": "Download agent", "ai_user_id": cls.ai_user.id}
        )
        cls.other_agent = cls.env["ai.bridge.agent"].create(
            {"name": "Other agent", "ai_user_id": cls.ai_user.id}
        )
        cls.channel = (
            cls.env["discuss.channel"]
            .with_user(cls.user)
            .create(
                {
                    "name": "Download channel",
                    "channel_type": "channel",
                    "agent_id": cls.agent.id,
                    "agent_auto_respond": True,
                }
            )
        )
        cls.attachment = (
            cls.env["ir.attachment"]
            .with_user(cls.user)
            .create(
                {
                    "name": "note.txt",
                    "datas": base64.b64encode(b"hello attachment").decode(),
                    "mimetype": "text/plain",
                }
            )
        )
        cls.message = cls.channel.with_user(cls.user).message_post(
            body="see the file",
            attachment_ids=[cls.attachment.id],
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
            author_id=cls.user.partner_id.id,
        )
        cls.queued = cls.env["ai.bridge.agent.message"].search(
            [("mail_message_id", "=", cls.message.id)], limit=1
        )

    def test_download_serves_the_file(self):
        response = self.url_open(
            f"/ai_agent/attachment/{self.agent.webhook_token}/{self.attachment.id}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"hello attachment")
        self.assertIn("text/plain", response.headers.get("Content-Type", ""))

    def test_unknown_token_is_refused(self):
        response = self.url_open(f"/ai_agent/attachment/deadbeef/{self.attachment.id}")
        self.assertEqual(response.status_code, 404)

    def test_attachment_of_another_agent_is_refused(self):
        response = self.url_open(
            f"/ai_agent/attachment/{self.other_agent.webhook_token}"
            f"/{self.attachment.id}"
        )
        self.assertEqual(response.status_code, 404)
