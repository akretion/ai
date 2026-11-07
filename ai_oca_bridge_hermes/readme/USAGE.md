## Configuration

1. Create the AI user: a regular Odoo user, member of no group of its own,
   linked to an `ai.bridge` with usage *Hermes* (this is what makes Discuss
   treat it as an assistant).
2. Go to *AI Bridge > Hermes > Gateways* and create a gateway: name, AI user,
   environment (`prod`/`staging`/`dev`) and the groups allowed to chat with it.
   System administrators always keep access.
3. Copy the **webhook token** for the polling transport, and the **HMAC secret**
   (`push_secret`) for the push transport. Both are shown to system
   administrators only.
4. Optional: restrict the gateway to specific channels, otherwise every channel
   the AI user belongs to is monitored, and group channels answer only when the
   assistant is mentioned.

**Real-time push (recommended)**

In the *Realtime push* section, check **Push enabled** and set the gateway URL,
e.g. `https://hermes.example.com/odoo/events`. Odoo then posts every new message
after the transaction commits, signed with:

```
X-Hermes-Tenant:    <tenant_key>
X-Hermes-Timestamp: <unix seconds>
X-Hermes-Delivery:  <uuid>
X-Hermes-Signature: sha256=<hmac_sha256(push_secret, "<timestamp>.<raw body>")>
```

A one-minute cron retries whatever the post-commit hook could not deliver, so a
gateway restart does not lose a message. Leaving push disabled keeps the
polling transport in charge.

**Polling (fallback)**

```bash
python scripts/hermes_odoo_gateway.py \
    --odoo-url https://odoo.example.com \
    --webhook-token YOUR_TOKEN \
    --poll-interval 5
```

Add `--use-agent --model ... --provider ...` to answer with a Hermes AIAgent
instead of echo mode. Set **Long poll timeout** on the gateway (0-20 s) to hold
the request until a message arrives instead of paying a fixed interval.

**Reference push receiver**

```bash
python scripts/hermes_push_server.py \
    --port 8770 \
    --odoo-url https://odoo.example.com \
    --tenant <tenant_key> \
    --secret <push_secret> \
    --mode echo
```

It verifies the signature, answers `/hermes/reply` with the same HMAC scheme and
is the place to plug the agent call (`build_answer`).

## Access control

Users outside the allowed groups are answered with a standard message
(configurable per gateway, *Access denied message*), posted at most once per
`denial_cooldown_minutes` per channel. Administrators are always allowed.

## Two capability tiers

The envelope announces what the gateway may do for the user:

- `tier: "full"`, `odoo_rpc: true` — the user owns an Odoo API key, so the
  gateway can call Odoo with that user's rights (ACL and record rules included);
- `tier: "basic"`, `odoo_rpc: false` — no API key: the assistant answers
  generically and does not read business data.

Uncheck *Require an Odoo API key for tools* to announce `odoo_rpc: true` for
everyone (the gateway then uses its own service account, which is a deployment
decision, not an Odoo one).

In both cases the gateway must never receive more rights than the user: the
API key is the user's own, and the module only reports whether it exists.

## Protocol summary

| Direction | Endpoint | Auth |
|---|---|---|
| Odoo -> gateway | `POST {push_url}` (`/odoo/events` by convention) | HMAC headers |
| gateway -> Odoo | `POST /hermes/reply` | HMAC headers |
| gateway -> Odoo | `POST /hermes/poll` | `Authorization: Bearer <token>` |
| gateway -> Odoo (legacy) | `POST /hermes/webhook/<token>`, `/hermes/push/<token>` | token in path |
| gateway -> Odoo | `POST /hermes/notify/<token>` | token in path |

Every JSON answer carries `status` in the body (`ok`/`error` plus an `error`
code): the JSON-RPC transport always returns HTTP 200, so the status must be
read from the body.

Answer payload:

```json
{"tenant": "...", "delivery_id": "<uuid>", "queue_id": 42, "channel_id": 11,
 "action": "post_message", "body": "the answer", "format": "markdown|html",
 "usage": {"tokens_in": 0, "tokens_out": 0, "ms": 0}}
```

A repeated `delivery_id` is answered with `{"status": "ok", "duplicate": true}`
and posts nothing: retries are safe. A reply closes exactly its `queue_id`, so
two conversations in flight in one channel cannot swallow each other.

## Troubleshooting

- *Queue rows stay pending*: the gateway is not polling and push is disabled or
  failing — check `last_error`/`last_error_date` on the gateway, and
  `pending_count` for the queue depth.
- *`dead` state*: `max_attempts` reached. Fix the gateway, then use
  **Retry** on the queue rows.
- *Signature rejected*: the gateway clock is more than 5 minutes off, or the
  secret does not match the one in Odoo.
