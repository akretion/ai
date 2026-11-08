#!/usr/bin/env python3
"""Reference AI agent - push receiver.

Implements protocol v1 of ``ai_oca_bridge_agent``: Odoo pushes an event,
this process answers by calling ``/ai_agent/reply`` with an HMAC signature.

This is the transport skeleton of a AI agent: replace ``build_answer``
with a call to your agent (Hermes AIAgent, an MCP client, a queue, ...).
Everything that is actual product policy (prompts, skills, toolsets, model
choice, tenant routing) stays in your private agent, never in the OCA
module.

Stdlib only, so it runs anywhere Odoo runs.

Usage:
    python push_server_example.py \
        --port 8770 \
        --odoo-url http://127.0.0.1:8069 \
        --tenant 8dc3bd93b34c7c1c \
        --secret 58c59ef7... \
        --mode echo

Protocol (verified here and in the module tests):
    POST /odoo/events
      X-AI-Agent-Tenant, X-AI-Agent-Timestamp, X-AI-Agent-Delivery,
      X-AI-Agent-Signature: sha256=hex(hmac_sha256(secret, f"{ts}.{raw_body}"))
    -> 202 {"status": "accepted"}

    POST {odoo_url}/ai_agent/reply  (same HMAC headers)
      {"delivery_id": ..., "queue_id": ..., "channel_id": ...,
       "action": "post_message", "body": "..."}
"""

import argparse
import hashlib
import hmac
import json
import logging
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger("ai_agent_push")

CONFIG = {}


def sign(secret, timestamp, body):
    payload = f"{timestamp}.".encode() + body.encode()
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def build_answer(event):
    """Return the text to post back in Odoo. Replace with your agent call."""
    message = event.get("message") or {}
    text = message.get("body_text") or ""
    author = message.get("author_name") or "user"
    capabilities = event.get("capabilities") or {}
    if CONFIG["mode"] == "echo":
        tier = capabilities.get("tier", "basic")
        return f"Echo [{tier}] from AI agent: {text}"
    return f"Received from {author}: {text}"


def post_reply(event, answer):
    message = event.get("message") or {}
    payload = {
        "tenant": CONFIG["tenant"],
        "delivery_id": event.get("delivery_id"),
        "queue_id": message.get("queue_id"),
        "channel_id": message.get("channel_id"),
        "action": "post_message",
        "body": answer,
        "usage": {"tokens_in": 0, "tokens_out": 0, "ms": 0},
    }
    body = json.dumps(payload)
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-AI-Agent-Tenant": CONFIG["tenant"],
        "X-AI-Agent-Timestamp": timestamp,
        "X-AI-Agent-Signature": "sha256=" + sign(CONFIG["secret"], timestamp, body),
    }
    request = urllib.request.Request(
        f"{CONFIG['odoo_url']}/ai_agent/reply",
        data=body.encode(),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=CONFIG["timeout"]) as response:
        return json.loads(response.read().decode())


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length).decode() if length else ""

    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - http.server API
        if self.path == "/health":
            self._send(200, {"status": "ok", "tenant": CONFIG["tenant"]})
            return
        self._send(404, {"status": "error"})

    def do_POST(self):  # noqa: N802 - http.server API
        if self.path != "/odoo/events":
            self._send(404, {"status": "error", "message": "unknown path"})
            return
        raw = self._read_body()
        timestamp = self.headers.get("X-AI-Agent-Timestamp", "")
        signature = self.headers.get("X-AI-Agent-Signature", "")
        tenant = self.headers.get("X-AI-Agent-Tenant", "")

        if tenant != CONFIG["tenant"]:
            self._send(403, {"status": "error", "message": "unknown tenant"})
            return
        try:
            skew = abs(time.time() - int(timestamp))
        except (TypeError, ValueError):
            skew = None
        if skew is None or skew > CONFIG["max_skew"]:
            self._send(403, {"status": "error", "message": "stale timestamp"})
            return
        expected = "sha256=" + sign(CONFIG["secret"], timestamp, raw)
        if not hmac.compare_digest(expected, signature):
            self._send(403, {"status": "error", "message": "bad signature"})
            return

        try:
            event = json.loads(raw)
        except ValueError:
            self._send(400, {"status": "error", "message": "invalid json"})
            return

        message = event.get("message") or {}
        logger.info(
            "event=%s delivery=%s queue=%s channel=%s tenant=%s user=%s",
            event.get("event"),
            event.get("delivery_id"),
            message.get("queue_id"),
            message.get("channel_id"),
            event.get("tenant", {}).get("key"),
            message.get("user_id"),
        )
        # Acknowledge first: Odoo marks the message as pushed, the answer
        # follows on its own request.
        self._send(202, {"status": "accepted"})
        try:
            answer = build_answer(event)
            reply = post_reply(event, answer)
            logger.info("reply sent: %s", reply)
        except (urllib.error.URLError, OSError) as exc:
            logger.error("reply failed: %s", exc)

    def log_message(self, format, *args):  # pylint: disable=redefined-builtin
        logger.info("%s - %s", self.address_string(), format % args)


def main():
    parser = argparse.ArgumentParser(description="AI agent push receiver")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--odoo-url", default="http://127.0.0.1:8069")
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--secret", required=True)
    parser.add_argument("--mode", default="echo", choices=["echo", "plain"])
    parser.add_argument("--timeout", type=int, default=10)
    parser.add_argument("--max-skew", type=int, default=300)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    CONFIG.update(
        {
            "odoo_url": args.odoo_url.rstrip("/"),
            "tenant": args.tenant,
            "secret": args.secret,
            "mode": args.mode,
            "timeout": args.timeout,
            "max_skew": args.max_skew,
        }
    )
    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    logger.info("listening on :%s for tenant %s", args.port, args.tenant)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("stopped")


if __name__ == "__main__":
    main()
