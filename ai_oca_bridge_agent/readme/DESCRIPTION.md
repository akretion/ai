## Bring your own agent

This module connects Odoo Discuss to an **external AI agent runtime** and gives
that runtime a safe way to work on Odoo data as the user who is chatting. It
does not run a model itself and does not embed a prompt: it owns the transport,
the identity and the policy, and the agent lives outside Odoo.

The contract is four plain HTTP endpoints, so the runtime can be anything that
speaks them: an agent framework, an automation platform (n8n), or a service of
your own. **Hermes Agent is the reference implementation** and the one Akretion
runs in production; a reference responder is published separately (Hermes Agent
plus a small HTTP adapter) so the contract is readable as running code rather
than as a specification only.

Because the agent runs elsewhere, it can do things an in-Odoo assistant cannot:
use its own skills and files, browse, call other systems, run on a model of your
choice, and be redeployed without an Odoo upgrade. What stays in Odoo is the
part that must not be delegated: who may talk to the assistant, what it may read
or create, and the audit trail.

## Real-time or polling, pick one

The module exposes two transports for the same conversation:

- **Push (recommended)**: Odoo signs an event with an HMAC secret and posts it
  to the responder the moment a message is committed. Latency is one HTTP call,
  and no Odoo worker is ever held waiting.
- **Polling (fallback)**: the responder calls `/ai_agent/poll` and *claims* the
  pending messages atomically. Works through any firewall, no inbound route
  needed on the Odoo side, but costs one request per interval per tenant.

Both transports share one envelope, so a responder can switch from one to the
other without touching the Odoo configuration beyond a checkbox.

## Multi-tenant by construction

One responder installation can serve many Odoo databases:

- every database owns a stable `tenant_key` (an `ir.config_parameter`,
  independent from `database.uuid`) and an `environment`
  (`prod`/`staging`/`dev`);
- the session key is `odoo:<tenant_key>:<environment>:user_<id>`, so a staging
  database restored from production never continues the production
  conversation;
- messages are claimed with `FOR UPDATE SKIP LOCKED`: any number of responder
  workers and Odoo HTTP workers can run concurrently without a duplicate
  delivery;
- replies are idempotent (`delivery_id`), and a reply closes exactly the
  message it answers, not every message of the channel.

## Who may talk to the assistant

Access is a group (`Use the AI assistant`), configurable per agent. System
administrators always keep access so a fresh install stays configurable.
Someone outside the allowed groups who addresses the assistant receives a
configurable standard answer, posted at most once per cooldown to avoid
flooding the channel.

## Two capability tiers per user

Not every Odoo user wants to hand an API key to the assistant:

- a user **with** an Odoo API key is announced to the responder as `full` tier
  and the responder may call Odoo on their behalf, with their own access rights;
- a user **without** a key is announced as `basic` tier: generic assistant
  capabilities only, no Odoo data access.

The module only publishes the fact (`capabilities` in the envelope) and the
policy stays on the responder side. `require_api_key_for_tools` decides whether
a key is mandatory before announcing `odoo_rpc`.

## Read and create under the user's own rights

A connected user can let the assistant query Odoo. The default policy follows
the API key: read methods on any model that user may read, plus `create` (see
`USAGE.md`), never `write` or `unlink` unless explicitly granted. Two optional
restrictions exist for customers who want the assistant weaker than its users:
an explicit grant list, and a deny list of models that must not be quoted into a
channel other people read.

## What this module deliberately does not do

The module is a transport, published under AGPL-3 in the OCA `ai` repository.
It contains no prompt, no skill, no model choice, no tenant-to-customer mapping,
no quota and no escalation backend: all of that is product logic and lives in
the responder process, which talks to Odoo only through the documented HTTP
envelope. See `CONTEXT.md` for the boundary and the extension points to use from
your own (AGPL) child modules.
