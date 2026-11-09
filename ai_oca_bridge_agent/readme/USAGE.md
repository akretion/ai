## Configuration

1. Create the AI user: a regular Odoo user, member of no group of its own,
   linked to an `ai.bridge` with usage *Agent* (this is what makes Discuss
   treat it as an assistant).
2. Go to *AI agent > Configuration > Agents* and create a agent: name, AI user,
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
X-AI-Agent-Tenant:    <tenant_key>
X-AI-Agent-Timestamp: <unix seconds>
X-AI-Agent-Delivery:  <uuid>
X-AI-Agent-Signature: sha256=<hmac_sha256(push_secret, "<timestamp>.<raw body>")>
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
| agent -> Odoo | `POST /ai_agent/poll` | `Authorization: Bearer <webhook_token>` |
| agent -> Odoo (legacy) | `POST /ai_agent/webhook/<token>`, `/ai_agent/push/<token>` | token in path |
| agent -> Odoo | `POST /ai_agent/notify/<token>` | token in path |
| agent -> Odoo | `POST /ai_agent/credentials` | bearer token |
| agent -> Odoo | `POST /ai_agent/tools/declared`, `/ai_agent/tools/call` | bearer token |
| agent -> Odoo | `POST /ai_agent/tools/available`, `/tools/models`, `/tools/resolve` | bearer token |
| agent -> Odoo | `POST /ai_agent/skills/get` | bearer token |
| agent -> Odoo | `POST /ai_agent/config/get` | bearer token |
| agent -> Odoo | `POST /ai_agent/progress` | bearer token |
| agent -> Odoo | `POST /ai_agent/attachment/<token>/<id>` | token in path |
| browser -> Odoo | `POST /ai_agent/web_context` | session |

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
2. The user opens **Preferences → AI assistant** and clicks **Connect**.
   Odoo creates an API key in that user's own name (scope `rpc`) and leaves it in
   a one-shot hand-off for the agent.
3. The agent collects it on `/ai_agent/credentials`, stores it encrypted on its
   side, checks it with a real RPC call, and wipes the plaintext in Odoo.
4. The key is renewable without asking again: a daily cron renews it a week
   before the date, for users who consented and are still active. After
   *Credential idle days* without a message the key is revoked and the consent is
   forgotten, so renewal follows use rather than habit.
5. **Disconnect** revokes the key immediately and stops any renewal; the user can
   reconnect from the same screen whenever they want.

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

A **grant** is the unit of data access (menu **AI assistant > Data access**): one
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

## Declared tools: what the assistant may *do*

Reading is one question, doing is another. `ai.bridge.agent.grant` rows bound
what data may be read; a **tool** declares an operation, with its schema, and is
only reachable when it is assigned:

- install `ai_oca_bridge_agent_ai_tool` (it depends on OCA `ai_tool`); the agent
  form then has a **Tools** tab, and skills can carry their own tools as a
  bundle;
- the responder lists them with `POST /ai_agent/tools/declared` and runs one
  with `POST /ai_agent/tools/call`;
- a tool runs with the rights of the user chatting, never as the agent's AI
  user; a record tool additionally checks that the user may read the record;
- `readonly` marks a read-only tool, and **Only read-only tools** on the agent
  refuses every other one;
- nothing is assignable by name alone: a tool the agent does not carry answers
  `tool_not_assigned`, even if the model learned its name elsewhere.

Server actions become tools with `ai_tool_server_action` (merged, or the review
branch): the same tab lists them, and their code runs in the user's
environment, so Odoo's ACLs remain the boundary for what the action does.

## Reading instance settings

`ir.config_parameter` is not readable as a model. When the assistant needs an
instance setting (typically `web.base.url`), it asks for that key:

    POST /ai_agent/config/get   {"user_id": 7, "key": "web.base.url"}

Only keys listed in *Config keys readable* are answered (`*` allows a prefix);
anything else returns `key_not_allowed` with the list of allowed keys. If the
key is stored through OCA **data_encryption**, the answer is `{"present": true}`
and the value never leaves the server.

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
  (AI assistant > Skills). Several agents can share one skill.
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

## Security model

The assistant is a language model with a live connection to a company database,
so the module treats it as an untrusted process that happens to be helpful. The
rules below are enforced in Odoo, on the server, before any data leaves: a
prompt cannot widen them, and neither can a compromised responder.

This file describes the model, what each layer protects against, and what it
does not protect against.

**Threat model**

| Adversary | What they can try | What stops them |
| --- | --- | --- |
| A user chatting with the assistant | Ask for records they may not read, for other people's data, for secrets | The reply runs under the user's own API key, so Odoo's ACLs and record rules apply; technical models are denied by policy |
| A prompt injected in the conversation (a document, an email, a web page the assistant reads) | Trick the model into reading more, or into running a tool | Every call is re-resolved server-side against the user's rights, the agent's grants and the model policy; tool assignment is explicit and checked per call |
| A user who is not allowed to use the assistant | Address the assistant in a channel | Group check before queueing; a configurable refusal, posted at most once per cooldown |
| The responder process, if compromised | Read every database, keep every credential | It holds only the keys users consented to hand over; each key is `rpc`-scoped, expiring, revocable, and usable only as that user |
| A leaked webhook token | Post fake answers, replay old ones | Token rotation with a grace window, HMAC signature, timestamp skew, idempotency on `delivery_id` |
| Another tenant on the same responder | Read this tenant's conversations | Tenant key + environment in the session key; every endpoint authenticates the agent and checks the user belongs to it |

**Who may talk to the assistant**

Access is a group (`Use the AI assistant`), per agent. System administrators
always keep access so a fresh install stays configurable; remove them from the
group to test the refusal. A user outside the group gets a configurable standard
answer, posted at most once per `denial_cooldown_minutes` so a channel is not
flooded.

**Authentication of the responder channel**

Every call from the responder carries the agent token; every call *to* the
responder is signed:

- push events carry `X-AI-Agent-Tenant`, `X-AI-Agent-Timestamp`,
  `X-AI-Agent-Delivery` and `X-AI-Agent-Signature` (HMAC-SHA256 of the body);
- the signature is compared in constant time, and a timestamp older than
  300 seconds is refused, which bounds replay;
- the reply is idempotent on `delivery_id`: a retried delivery never posts
  twice, and the second attempt returns the original message id.

Rotating a token keeps the previous one valid for a grace window so a running
responder is not cut off mid-conversation.

**What the assistant may read**

Two independent layers, both server-side:

1. **The user's own API key.** Every query runs with the key of the person who
   is chatting, so ACLs and record rules apply exactly as in the interface. The
   assistant cannot read a record its user cannot open.
2. **A policy above that.** A user's rights are not enough for technical
   models, which everybody can read and which hold what nobody sees on screen.
   So:

   - `ir.*` models are **denied by default**, with an allowlist for what an
     assistant legitimately needs: `ir.model`, `ir.model.fields`, `ir.ui.menu`,
     `ir.ui.view`, window/url/client/report actions, installed modules and
     scheduled actions (read-only);
   - never readable, even if configured: `ir.config_parameter`,
     `ir.mail_server`, `res.users.apikeys`, `res.users.identitycheck`,
     `auth.totp`, `auth.passkey`;
   - everything else follows the agent `tool_access_mode`: **open** (the user's
     key is the boundary) or **grants only** (`ai.bridge.agent.grant` rows name
     the model, method, groups, agents, row cap and fields);
   - a per-agent deny list (`tool_excluded_model_ids`) closes models in both
     modes, and it is the right answer to the one leak a key cannot prevent:
     the answer is posted in a Discuss channel that other people read, so
     payroll or contracts must not be quoted there even when the asker may open
     them.

   Overrides exist for operators (`model_allowlist` / `model_denylist` on the
   agent, the `ai_bridge_agent.model_allow` / `model_deny` instance parameters,
   and the `AI_AGENT_MODEL_ALLOW` / `AI_AGENT_MODEL_DENY` environment
   variables); deny always wins over allow, and the hard list above wins over
   both.

Only read methods are exposed for data (`search_read`, `read`, `fields_get`,
`search_count`, `read_group`). `write` and `unlink` are not reachable, and
`create` is not exposed either: a raw create skips onchange logic, defaults and
sequences. Operations that change data go through a declared tool
(`ai.tool` / server action), which carries its business logic and still runs
with the user's rights.

**Configuration parameters and secrets**

`ir.config_parameter` is never readable as a model. The module exposes
`/ai_agent/config/get`, which answers **one key** at a time and only if the
agent lists it (`config_key_allowlist`, default `web.base.url`, prefixes
allowed). A refused key returns the list of allowed ones, so the assistant can
say what it may read instead of guessing.

When OCA **`data_encryption`** is installed, a parameter stored in
`encrypted.data` is decrypted server-side and reported as `{"present": true}`
only: the model learns that a secret is configured, never its value. The
coupling is loose on purpose (the module checks for `encrypted.data` in the
registry), so there is no dependency and no glue module.

Two more secrets matter here:

- **The Odoo API key** is never stored in clear text: Odoo keeps a hash, and the
  module hands the plain value to the responder exactly once, then forgets it.
  The responder keeps it encrypted (Fernet) and bound to the session.
- **The provider key of the model** is not an Odoo concern: it lives in the
  responder's own configuration. Putting it in Odoo is possible (encrypted), but
  the runs API of the reference responder has no per-run credential, so it
  cannot be forwarded yet; a profile per tenant is the supported shape (see
  `ROADMAP.md`).

**Consent, renewal and revocation**

Handing an API key to a process is a decision the user makes once, in
Preferences, and it stays reversible:

- **Connect** creates a dedicated `rpc` key, records the consent
  (`agent_credential_consent_at`) and hands the key over; the notification says
  for how long.
- The key is **renewed automatically** before it expires (daily cron, up to
  `credential_renew_before_days` before the date), because a key that expires
  silently turns every read into a refusal while declared tools keep answering.
  Renewal only ever touches the key the module created, only for users who
  consented, still in the group, and still active.
- Renewal **follows use**: after `credential_idle_days` (60 by default) without
  a single message, the key is revoked and the consent is forgotten; the user
  reconnects when they come back.
- **Disconnect** revokes the key and clears the consent, which also stops
  renewal. Every hand-off records whether it came from a click or from the cron,
  so an administrator can see why a key changed.

A refused or expired key is reported to the assistant with a hint telling the
user where to reconnect; the assistant keeps working at the `basic` tier in the
meantime.

**Declared tools run as the user**

A declared tool (`ai.tool`, including server actions) is assigned explicitly to
an agent, and it runs in the environment of the person chatting, never as the
agent's AI user and never with `sudo`. The module checks, per call, that the
tool is assigned, that the user may read the model it acts on, and that the
record it targets is visible to them; mutating tools can be refused wholesale
with `readonly_tools`. This matters because the alternative — a tool running as
the AI user — is a privilege escalation sitting next to an otherwise per-user
design.

**What this does not protect against**

Being explicit about the limits is part of the design:

- **The user's own rights are the floor.** A user who may read a contract can
  ask the assistant about it, and the answer is posted in the channel. Use the
  deny list when the audience of the channel is wider than the asker's rights.
- **The responder sees the data it is asked about.** If it is compromised, it
  sees what the users it serves may see. The mitigation is scope (one agent per
  purpose, grants where it helps) and revocation, not encryption at rest on the
  responder's side.
- **The model provider sees the prompt.** Whatever reaches the model leaves the
  company; keep secrets out of the prompt by construction, as above.
- **The Discuss channel is a publication surface.** An answer is visible to every
  member of the channel, including guests if the channel allows them.
- **A screenshot is data.** Attachments travel to the responder like the rest,
  under the same rights, and are deleted from the responder's inbox once the
  answer is posted.

**Audit trail**

Every message that reaches the assistant is a queue row: who asked, in which
channel, which agent, which session, how many attempts, the HTTP status of the
last delivery, the answer's message id and the token usage. Credentials keep
their origin and their expiry, progress notes are transient and removed with the
answer, and a refused user leaves a single standard answer instead of a trail of
attempts.
