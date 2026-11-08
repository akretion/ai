# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestInboundAttachments(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ai_user = cls.env["res.users"].create(
            {
                "name": "Attachment AI",
                "login": "attachment_ai",
                "email": "attachment_ai@example.com",
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            }
        )
        cls.user = cls.env["res.users"].create(
            {
                "name": "Attachment User",
                "login": "attachment_user",
                "email": "attachment_user@example.com",
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
            {"name": "Attachment agent", "ai_user_id": cls.ai_user.id}
        )
        cls.channel = (
            cls.env["discuss.channel"]
            .with_user(cls.user)
            .create(
                {
                    "name": "Attachments",
                    "channel_type": "channel",
                    "agent_id": cls.agent.id,
                    "agent_auto_respond": True,
                }
            )
        )

    def _post_with_attachment(self, name="screenshot.png", payload=b"\x89PNG"):
        attachment = (
            self.env["ir.attachment"]
            .with_user(self.user)
            .create(
                {
                    "name": name,
                    "datas": base64.b64encode(payload).decode(),
                    "mimetype": "image/png",
                }
            )
        )
        message = self.channel.with_user(self.user).message_post(
            body="Can you look at this screenshot?",
            attachment_ids=[attachment.id],
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
            author_id=self.user.partner_id.id,
        )
        return message, attachment

    def test_message_attachment_reaches_the_queue(self):
        message, attachment = self._post_with_attachment()
        queued = self.env["ai.bridge.agent.message"].search(
            [("mail_message_id", "=", message.id)], limit=1
        )
        self.assertTrue(queued, "the message should be queued for the agent")
        self.assertEqual(queued.attachment_ids, attachment)
        payload = queued._get_attachments_payload()
        self.assertEqual(payload[0]["name"], "screenshot.png")
        self.assertEqual(payload[0]["mimetype"], "image/png")
        self.assertIn(self.agent.webhook_token, payload[0]["url"])
        self.assertIn(str(attachment.id), payload[0]["url"])

    def test_message_without_attachment_has_empty_payload(self):
        message = self.channel.with_user(self.user).message_post(
            body="No file here",
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
            author_id=self.user.partner_id.id,
        )
        queued = self.env["ai.bridge.agent.message"].search(
            [("mail_message_id", "=", message.id)], limit=1
        )
        self.assertEqual(queued._get_attachments_payload(), [])
