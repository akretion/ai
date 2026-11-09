## Configuration

1. Create the AI user: a regular Odoo user, member of no group of its own,
   linked to an `ai.bridge` with usage *Hermes* (this is what makes Discuss
   treat it as an assistant).
2. Go to *AI Bridge > Hermes > Agents* and create a agent: name, AI user,
   environment (`prod`/`staging`/`dev`) and the groups allowed to chat with it.
   System administrators always keep access.
3. Copy the **webhook token** for the polling transport, and the **HMAC secret**
   (`push_secret`) for the push transport. Both are shown to system
   administrators only.
4. Optional: restrict the agent to specific channels, otherwise every channel
   the AI user belongs to is monitored, and group channels answer only when the
   assistant is mentioned.

**Real-time push (recommended)**

In the *Realtime push* section, check **Push enabled** and set the agent URL,
e.g. `https://responder.example.com/ai_agent/events`. Odoo then posts every new message
after the transaction commits, signed with:

```
X-Hermes-Tenant:    <tenant_key>
X-Hermes-Timestamp: <unix seconds>
X-Hermes-Delivery:  <uuid>
X-Hermes-Signature: sha256=<hmac_sha256(push_secret, "<timestamp>.<raw body>")>
```

A one-minute cron retries whatever the post-commit hook could not deliver, so a
agent restart does not lose a message. Leaving push disabled keeps the
polling transport in charge.

**Polling (fallback)**

```bash
python scripts/agent_gateway_example.py \
    --odoo-url https://odoo.example.com \
    --webhook-token YOUR_TOKEN \
    --poll-interval 5
```

Add `--use-agent --model ... --provider ...` to answer with a Hermes AIAgent
instead of echo mode. Set **Long poll timeout** on the agent (0-20 s) to hold
the request until a message arrives instead of paying a fixed interval.

**Reference push receiver**

```bash
python scripts/ai_agent_push_server.py \
    --port 8770 \
    --odoo-url https://odoo.example.com \
    --tenant <tenant_key> \
    --secret <push_secret> \
    --mode echo
```

It verifies the signature, answers `/ai_agent/reply` with the same HMAC scheme and
is the place to plug the agent call (`build_answer`).

## Access control

Users outside the allowed groups are answered with a standard message
(configurable per agent, *Access denied message*), posted at most once per
`denial_cooldown_minutes` per channel. Administrators are always allowed.

## Two capability tiers

The envelope announces what the agent may do for the user:

- `tier: "full"`, `odoo_rpc: true` — the user owns an Odoo API key, so the
  agent can call Odoo with that user's rights (ACL and record rules included);
- `tier: "basic"`, `odoo_rpc: false` — no API key: the assistant answers
  generically and does not read business data.

Uncheck *Require an Odoo API key for tools* to announce `odoo_rpc: true` for
everyone (the agent then uses its own service account, which is a deployment
decision, not an Odoo one).

In both cases the agent must never receive more rights than the user: the
API key is the user's own, and the module only reports whether it exists.

## Protocol summary

| Direction | Endpoint | Auth |
|---|---|---|
| Odoo -> agent | `POST {push_url}` (`/odoo/events` by convention) | HMAC headers |
| agent -> Odoo | `POST /ai_agent/reply` | HMAC headers |
| agent -> Odoo | `POST /ai_agent/poll` | `Authorization: Bearer <token>` |
| agent -> Odoo (legacy) | `POST /ai_agent/webhook/<token>`, `/ai_agent/push/<token>` | token in path |
| agent -> Odoo | `POST /ai_agent/notify/<token>` | token in path |

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

## Letting the assistant read Odoo as the user

A user can hand their own API key to the assistant, so answers can be based on
records **that user** is allowed to see:

1. The administrator enables **Allow users to connect** on the agent.
2. The user opens **Preferences → Hermes AI** and clicks **Connect Hermes**.
   Odoo creates an API key in that user's own name (scope `rpc`) and leaves it in
   a one-shot hand-off for the agent.
3. The agent collects it on `/ai_agent/credentials`, stores it encrypted on its
   side, checks it with a real RPC call, and wipes the plaintext in Odoo.
4. **Disconnect** revokes the key immediately; otherwise it expires after
   *Credential validity (days)*.

Nothing is typed by hand and nothing appears in the chat. Because the key is the
user's own, the assistant inherits exactly that user's access rights and record
rules: it cannot read what the user cannot read, and the key can be revoked from
the same screen.

Note: Odoo stores API keys hashed, so a key cannot be read back later. The
hand-off is therefore one-shot: if the agent loses it, the user reconnects.

## Conversations, skills on demand, and live progress

Three behaviours follow the agent framework OCA is building in `ai_tool_skill`
and `ai_agent`; they are implemented here on our own models, behind the same
ideas, so the two can share one abstraction later:

- **One memory per conversation** (`session_scope = Per conversation`, the
  default): the session key carries the thread, so a new Discuss channel starts
  a fresh conversation and nothing has to be reset by hand. `Per user` keeps a
  single memory across all of a person's threads.
- **Skills on demand** (`skill_loading = On demand`): the agent definition
  carries the skill summaries, and the model loads a skill's text with the
  `odoo_skill` tool (`/ai_agent/skills/get`). This is the same progressive
  disclosure `ai_tool_skill` gets by making a skill a callable tool, and it
  keeps long skill texts out of every message.
- **Progress notes**: while a run works, the responder posts temporary notes
  (`/ai_agent/progress`: "Thinking...", one line per Odoo call) and they are
  deleted when the answer lands, so the thread keeps only the conversation.

The browsing context is captured server-side when the message is queued and
stored on the queue row, per conversation, so an answer refers to what the user
was looking at when they asked, not to whatever page they opened meanwhile.

## Letting the assistant use Odoo data

A connected user can also let the assistant *query* Odoo. The default policy is
**Any model the user may read** (`tool_access_mode` on the agent): the
assistant inherits the user's own API key, so it can read exactly what that user
can read in the interface, and nothing else. Read methods only (`search_read`,
`read`, `fields_get`, `search_count`, `read_group`); no write is exposed yet.

A **grant** is the unit of data access (menu **Hermes → Data access**): one
model, one method, a row cap, an optional field list, the groups that may use
it, and the agents it applies to (empty means every agent). Grants are rules on
data, not operations: they answer "which records may the assistant touch",
while tools answer "what operations can it perform".

Two optional restrictions, for customers who want to be stricter than Odoo's own
access rights:

- **Only the grants** (`tool_access_mode = whitelist`): the assistant may only
  touch the models named by a grant bound to its agent and the user's groups.
  `fields_get` and `search_count` are implied by a read grant on the same model.
- **Models hidden from the assistant**: a deny list that applies in both modes.
  Its real use is not secrecy but privacy in the channel — an answer is posted
  in Discuss where other members read it, so a customer may keep payroll or
  contracts out of the assistant even though the user could open them.

The agent checks every call with Odoo before running it and uses the user's
own key, so a cached grant list or a prompt injection cannot widen either mode.
`odoo_models` lets the assistant discover what it may query instead of guessing.

## What the user is looking at

When the assistant lives in Discuss, it has no idea which screen the user has
open — and a support question usually starts with "this invoice". A small web
asset reports the current URL on every navigation (throttled to one request
every few seconds, only when the page actually changed) to
`/ai_agent/web_context`, which stores one context per user: model, record id,
view type, action and URL.

The context travels with the next message (`view_context`) and the responder
treats it as a **hint**: it may say "you are looking at a sale order in form
view", but it can never widen access, because every read still runs with the
user's own API key. A context older than an hour is flagged `stale`, and a cron
forgets contexts nobody refreshed for 30 days.

The reference bridge prefixes the message with that context, so the agent can
answer about "this record" without being told which one.

## Giving the agent a role: instructions, skills, knowledge

An agent record is not only a transport endpoint, it carries the configuration
the responder applies to every conversation:

- **Instructions**: what the agent is for, written by the customer
  administrator.
- **Operator instructions**: owned by whoever runs the responder (safety rules,
  tone, escalation). The responder applies them **after** the customer
  instructions, so a customer edit can never weaken an operator rule.
- **Skills**: reusable blocks of instructions plus the tool names they expect
  (Hermes → Skills). Several agents can share one skill.
- **Knowledge**: plain text sources sent as context, capped by *Knowledge
  budget* so a conversation cannot grow unbounded. Nothing is indexed in Odoo:
  the responder does the retrieval, and a glue module can fill the body from
  `document_page`.
- **Model override**: a provider and a model for this agent
  (`deepseek` + `deepseek-v4-pro`). The responder receives both and decides how
  to apply them; the reference bridge passes the model name and lets the
  gateway resolve the provider. **The provider credential never leaves the
  responder**, so an Odoo administrator can choose the model without ever
  seeing the API key.

All of it travels in the poll response under `agent`, plus the stable
`agent_key` on every message, so one responder can serve several agents with
different roles.

## Files the user sends

A message may carry attachments. Each queued message remembers them
(`ai_bridge_agent_message_attachment_rel`) and the payload lists them with a
token-authenticated URL:

```json
"attachments": [{"id": 42, "name": "screen.png", "mimetype": "image/png",
                 "size": 81234, "url": "/ai_agent/attachment/<token>/42"}]
```

The download endpoint only serves files that were queued for **that** agent, so
a leaked URL is useless to another tenant. `POST /ai_agent/webhook/<token>`
accepts the reverse direction (`attachments` with base64), which is how the
assistant sends files back.

The reference responder writes the incoming files under its inbox, tells the
model where they are (with a text preview for text-like files), and deletes
them once the answer is posted. Reading an image also needs a vision-capable
auxiliary model configured on the responder; without one the assistant knows a
file arrived but cannot describe it.

## Rendering and attachments

The reply payload tells Odoo how to read the answer, so the assistant is not
forced into plain text:

| `format` | What Odoo does |
|---|---|
| `plain` (default) | escapes, keeps line breaks, turns bare URLs into links |
| `markdown` | converts tables, lists, fenced code to HTML, then sanitizes |
| `html` | sanitizes and keeps the markup |

`markdown` degrades to `plain` when python-markdown is not installed on the
server, so the module stays installable without it. Everything is sanitized
whatever the format: a model answer can never inject a script into Discuss.

A reply may also carry `attachments`, a list of
`{name, mimetype, b64}` (5 files, 10 MB total). Odoo stores them as regular
Discuss attachments on the message, so they respect the channel's access rights
and show the usual download link. Path separators in names are neutralized.

The reference responder only attaches files it finds in its own outbox
directory, and removes them once posted: the assistant cannot exfiltrate an
arbitrary server file by naming it.

## Troubleshooting

- *Queue rows stay pending*: the agent is not polling and push is disabled or
  failing — check `last_error`/`last_error_date` on the agent, and
  `pending_count` for the queue depth.
- *`dead` state*: `max_attempts` reached. Fix the agent, then use
  **Retry** on the queue rows.
- *Signature rejected*: the agent clock is more than 5 minutes off, or the
  secret does not match the one in Odoo.
