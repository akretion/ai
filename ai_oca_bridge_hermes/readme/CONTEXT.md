## Why This Module?

The AI revolution is changing how users interact with software. Odoo users should
be able to ask questions, get help, and automate tasks using natural language —
directly inside the interface they already use daily.

## Architecture Rationale

**Two transports, one envelope**

Polling was the original design: it works behind firewalls and needs no inbound
route on the Odoo side. It stays as the **fallback**, but it does not scale to
many tenants (one request per interval per tenant, a delayed answer, and a
gateway that has to hold the conversation state between two polls).

The recommended transport is **outbound push**: Odoo signs the event and posts
it as soon as the transaction is committed (`cr.postcommit`), so the gateway
never sees a message the database could still roll back. A one-minute cron
pushes whatever the post-commit hook could not send, which makes a gateway
restart or a network glitch invisible to the user.

Both transports carry the same JSON envelope (see `USAGE.md`), so the gateway
only has to implement one parser.

**Stateless gateway, persistent session per user**

The gateway keeps **no** conversation state in Odoo: it authenticates with a
tenant key, reads the session key from the envelope, and answers with a
`delivery_id`. The Hermes session itself is persistent (`session_scope`: per
user by default, per channel optionally), which is what gives the assistant a
memory across messages without storing a transcript in Odoo.

**Atomic claiming**

`/hermes/poll` claims messages with a single `UPDATE ... FOR UPDATE SKIP
LOCKED ... RETURNING`. This is what makes several gateway workers and several
Odoo HTTP workers safe together, and it removes the previous per-gateway
one-second throttle that capped every tenant of a database.

**Standalone gateway**

`scripts/hermes_odoo_gateway.py` polls in echo mode or with a Hermes AIAgent.
`scripts/hermes_push_server.py` is the reference push receiver: it verifies the
HMAC signature, answers, and is the place to plug the agent call. Neither needs
to run inside Odoo's Python environment.

**Extension points**

The module is intentionally minimal. Child modules can extend, without forking:

- `hermes.gateway._is_user_allowed(user)` — who may talk to the assistant;
- `hermes.gateway._get_capabilities(message)` — what the gateway may do for
  that user (`full`/`basic`, `odoo_rpc`, tool list);
- `hermes.gateway._build_event_payload(message)` — enrich the envelope with
  business context;
- `hermes.gateway._get_session_key(user, channel)` — session granularity;
- `hermes.message.queue._get_history(limit)` — what the assistant sees of the
  conversation;
- `discuss.channel._should_queue_for_hermes(message, gateway)` — routing rules;
- `hermes_webhook._action_escalate(gateway, data)` — escalation backend.

**The AGPL boundary (read this before adding private logic)**

This module is AGPL-3 and published at OCA. A child Odoo module that inherits
it is a derivative work: it must stay AGPL, and if you serve it over the
network the AGPL asks you to offer its source to the users. So the private
value of the product must **not** live in an Odoo module:

- keep in Odoo only the transport, the traceability and the coarse access
  policy that you are happy to publish;
- keep prompts, skills, tool whitelists, model/provider choice, tenant
  registry, quotas, billing and routing intelligence in the **gateway
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

- Webhook tokens are auto-generated, rotatable with a grace period (the
  previous token stays valid for `token_grace_minutes`), compared in constant
  time and never trusted from the URL alone: the recommended endpoints
  authenticate with an HMAC signature over `<timestamp>.<body>`;
- AI users are regular Odoo users with limited permissions;
- the gateway runs with its own credentials and, per user, with that user's
  API key when there is one;
- queue rows are protected by a multi-company rule and by an
  own-messages-only rule for non-managers;
- the Odoo side never executes code sent by the gateway.
