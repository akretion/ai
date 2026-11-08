- Writes: `create`/`write` behind an explicit grant and a human confirmation
  in Discuss, on top of the read-only tools already shipped
- Multi-tenant routing: a shared Hermes instance serving several Odoo
  databases, one profile per tenant
- Per-tenant quotas and usage metering (in a child module or the agent, never
  in the transport)
- Support for file attachments and images
- Voice message integration
- Interactive approval buttons in Discuss messages (e.g. "Approve/Reject"
  actions for sensitive operations) — nice to have for escalation workflows
- Native Hermes platform adapter (`platform="odoo"`) consuming the push
  envelope directly, without a separate agent script
- WebSocket transport for browsers that cannot reach the agent
