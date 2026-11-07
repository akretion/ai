## Real-time or polling, pick one

The module exposes two transports for the same conversation:

- **Push (recommended)**: Odoo signs an event with an HMAC secret and posts it
  to the gateway the moment a message is committed. Latency is one HTTP call,
  and no Odoo worker is ever held waiting.
- **Polling (fallback)**: the gateway calls `/hermes/poll` and *claims* the
  pending messages atomically. Works through any firewall, no inbound route
  needed on the Odoo side, but costs one request per interval per tenant.

Both transports share one envelope, so a gateway can switch from one to the
other without touching the Odoo configuration beyond a checkbox.

## Multi-tenant by construction

One Hermes installation can serve many Odoo databases:

- every database owns a stable `tenant_key` (an `ir.config_parameter`,
  independent from `database.uuid`) and an `environment`
  (`prod`/`staging`/`dev`);
- the Hermes session key is
  `odoo:<tenant_key>:<environment>:user_<id>`, so a staging database restored
  from production never continues the production conversation;
- messages are claimed with `FOR UPDATE SKIP LOCKED`: any number of gateway
  workers and Odoo HTTP workers can run concurrently without a duplicate
  delivery;
- replies are idempotent (`delivery_id`), and a reply closes exactly the
  message it answers, not every message of the channel.

## Who may talk to the assistant

Access is a group (`Use Hermes AI`), configurable per gateway. System
administrators always keep access so a fresh install stays configurable.
Someone outside the allowed groups who addresses the assistant receives a
configurable standard answer, posted at most once per cooldown to avoid
flooding the channel.

## Two capability tiers per user

Not every Odoo user wants to hand an API key to the assistant:

- a user **with** an Odoo API key is announced to the gateway as `full` tier
  and the gateway may call Odoo on their behalf, with their own access rights;
- a user **without** a key is announced as `basic` tier: generic assistant
  capabilities only, no Odoo data access.

The module only publishes the fact (`capabilities` in the envelope) and the
policy stays on the gateway side. `require_api_key_for_tools` decides whether a
key is mandatory before announcing `odoo_rpc`.

## What this module deliberately does not do

The module is a transport, published under AGPL-3 in the OCA `ai` repository.
It contains no prompt, no skill, no tool whitelist, no model choice, no
tenant-to-customer mapping, no quota and no escalation backend: all of that is
product logic and lives in the gateway process, which talks to Odoo only
through the documented HTTP envelope. See `CONTEXT.md` for the boundary and
the extension points to use from your own (AGPL) child modules.
