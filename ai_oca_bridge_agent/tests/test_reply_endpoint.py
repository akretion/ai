# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import json
import time

from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestReplyEndpoint(HttpCase):
    """End to end: a signed reply renders markdown and carries its file."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ai_user = cls.env["res.users"].create(
            {
                "name": "Reply AI",
                "login": "reply_ai",
                "email": "reply_ai@example.com",
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            }
        )
        cls.gateway = cls.env["hermes.gateway"].create(
            {"name": "Reply Gateway", "ai_user_id": cls.ai_user.id}
        )
        cls.channel = cls.env["discuss.channel"].create(
            {"name": "Reply channel", "channel_type": "channel"}
        )

    def _post_reply(self, payload):
        body = json.dumps(payload)
        timestamp = str(int(time.time()))
        signature = self.gateway._compute_signature(timestamp, body)
        return self.url_open(
            f"/hermes/webhook/{self.gateway.webhook_token}",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Hermes-Timestamp": timestamp,
                "X-Hermes-Signature": f"sha256={signature}",
            },
        )

    def test_markdown_table_and_attachment(self):
        answer = "| Invoice | Total |\n| --- | --- |\n| INV/1 | 10 |"
        response = self._post_reply(
            {
                "channel_id": self.channel.id,
                "body": answer,
                "format": "markdown",
                "attachments": [
                    {
                        "name": "invoices.csv",
                        "mimetype": "text/csv",
                        "b64": base64.b64encode(b"name,total\nINV/1,10\n").decode(),
                    }
                ],
            }
        )
        self.assertEqual(response.status_code, 200)
        message = self.env["mail.message"].search(
            [("model", "=", "discuss.channel"), ("res_id", "=", self.channel.id)],
            order="id desc",
            limit=1,
        )
        self.assertIn("<table", message.body)
        self.assertEqual(len(message.attachment_ids), 1)
        self.assertEqual(message.attachment_ids.name, "invoices.csv")
