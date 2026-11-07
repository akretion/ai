- Tool exposure: let the model call whitelisted Odoo methods (`search_read`,
  `read`, `fields_get`) instead of the gateway pre-fetching everything; the
  whitelist should live in Odoo and be enumerable by the agent
- Multi-tenant routing: a shared Hermes instance serving several Odoo
  databases, one profile per tenant
- Per-tenant quotas and usage metering (in a child module or the gateway, never
  in the transport)
- Support for file attachments and images
- Voice message integration
- Interactive approval buttons in Discuss messages (e.g. "Approve/Reject"
  actions for sensitive operations) — nice to have for escalation workflows
- Native Hermes platform adapter (`platform="odoo"`) consuming the push
  envelope directly, without a separate gateway script
- WebSocket transport for browsers that cannot reach the gateway
