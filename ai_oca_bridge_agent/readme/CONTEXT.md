## Why This Module?

The AI revolution is changing how users interact with software. Odoo users should
be able to ask questions, get help, and automate tasks using natural language —
directly inside the interface they already use daily.

## Architecture Rationale

**Two transports, one envelope**

Polling was the original design: it works behind firewalls and needs no inbound
route on the Odoo side. It stays as the **fallback**, but it does not scale to
many tenants (one request per interval per tenant, a delayed answer, and a
agent that has to hold the conversation state between two polls).

The recommended transport is **outbound push**: Odoo signs the event and posts
it as soon as the transaction is committed (`cr.postcommit`), so the agent
never sees a message the database could still roll back. A one-minute cron
pushes whatever the post-commit hook could not send, which makes a agent
restart or a network glitch invisible to the user.

Both transports carry the same JSON envelope (see `USAGE.md`), so the agent
only has to implement one parser.

**Stateless agent, persistent session per user**

The agent keeps **no** conversation state in Odoo: it authenticates with a
tenant key, reads the session key from the envelope, and answers with a
`delivery_id`. The responder session itself is persistent
(`session_scope`: per conversation by default, per user optionally), which is
what gives the assistant a memory across messages without storing a transcript
in Odoo.

**Atomic claiming**

`/ai_agent/poll` claims messages with a single `UPDATE ... FOR UPDATE SKIP
LOCKED ... RETURNING`. This is what makes several agent workers and several
Odoo HTTP workers safe together, and it removes the previous per-agent
one-second throttle that capped every tenant of a database.

**Standalone agent**

`scripts/agent_gateway_example.py` polls in echo mode or with a Hermes AIAgent.
`scripts/ai_agent_push_server.py` is the reference push receiver: it verifies the
HMAC signature, answers, and is the place to plug the agent call. Neither needs
to run inside Odoo's Python environment.

**Extension points**

The module is intentionally minimal. Child modules can extend, without forking:

- `ai.bridge.agent._is_user_allowed(user)` — who may talk to the assistant;
- `ai.bridge.agent._get_capabilities(message)` — what the agent may do for
  that user (`full`/`basic`, `odoo_rpc`, tool list);
- `ai.bridge.agent._build_event_payload(message)` — enrich the envelope with
  business context;
- `ai.bridge.agent._get_session_key(user, channel)` — session granularity;
- `ai.bridge.agent.message._get_history(limit)` — what the assistant sees of the
  conversation;
- `discuss.channel._should_queue_for_agent(message, agent)` — routing rules;
- `ai_agent_webhook._action_escalate(agent, data)` — escalation backend.

**The AGPL boundary (read this before adding private logic)**

This module is AGPL-3 and published at OCA. A child Odoo module that inherits
it is a derivative work: it must stay AGPL, and if you serve it over the
network the AGPL asks you to offer its source to the users. So the private
value of the product must **not** live in an Odoo module:

- keep in Odoo only the transport, the traceability and the coarse access
  policy that you are happy to publish;
- keep prompts, skills, tool whitelists, model/provider choice, tenant
  registry, quotas, billing and routing intelligence in the **agent
  process**, which talks to Odoo over the documented HTTP envelope;
- if a private Odoo child module is really needed, accept that it is AGPL too
  (that is fine for internal, non-distributed deployments, but it is a
  conscious choice, not an accident).

**Escalation Concept**

The module includes a minimal escalation hook (`_action_escalate`) so the AI can
signal when human intervention is needed. The base module does not implement any
specific escalation backend — child modules can override this hook to create
tickets, notify teams, or route to another Hermes instance.

## Security Model

The full model lives in the *Security model* section of `USAGE.md`; the invariants that must survive any
refactor are:

- tokens are auto-generated, rotatable with a grace period, compared in constant
  time; state-changing endpoints authenticate with an HMAC signature over
  `<timestamp>.<body>`, and an old timestamp is refused;
- the AI user is a regular Odoo user with limited permissions; answers are
  posted as that user, and the agent never becomes superuser;
- data access is the user's own API key, bounded by the model policy: technical
  `ir.*` models are denied by default, configuration parameters are read one
  allowlisted key at a time, and a hard list (`ir.config_parameter`,
  `ir.mail_server`, `res.users.apikeys`, `auth.totp`, `auth.passkey`) can never
  be opened;
- declared tools are assigned explicitly and run in the user's environment,
  never as the AI user and never with `sudo`;
- queue rows are protected by a multi-company rule and by an own-messages-only
  rule for non-managers;
- the Odoo side never executes code sent by the agent.

## Optional companions (loose coupling, no dependency)

The module stays free of optional modules, and uses them when they are there:

- **`ai_tool` / `ai_tool_server_action`**: declared tools, through the glue
  module `ai_oca_bridge_agent_ai_tool`;
- **`data_encryption`**: an `ir.config_parameter` value stored in
  `encrypted.data` is decrypted server-side and reported as present, never
  disclosed;
- **`document_page`** (OCA knowledge) and **`automation_oca`**: knowledge sources
  and triggers belong to glue modules, in the same spirit.

The check is always a registry lookup (`"encrypted.data" in self.env`,
`"ai.tool" in self.env`), never an import of the other module: no hard
dependency, no glue module for a hundred lines, and the feature disappears
cleanly when the module is uninstalled.
