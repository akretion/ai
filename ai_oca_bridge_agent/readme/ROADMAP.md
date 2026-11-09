- **Per-tenant model credentials**: the reference responder takes its provider
  key from its own configuration, not from the request. Serving several customers
  with their own LLM keys therefore means one profile per tenant on the
  responder side; Odoo already stores such a key encrypted (through
  `data_encryption`) and could provision it.
- **Approval in Discuss**: `create`/`write` behind an explicit grant *and* a
  confirmation button in the thread, instead of the declared-tool route we ship
  today.
- **Quotas and metering**: per tenant, per user, per model, computed from the
  queue rows the module already stores.
- **Interactive buttons** in answers (approve, escalate, retry) rather than
  plain text links.
- **Odoo 16 backport**: the plan exists (`odoo16-backport.md` in the reference
  repository); native API keys are available there, only their expiry field is
  missing.
- **Native responder adapters**: a Hermes platform adapter consuming the push
  envelope directly, or a first-class n8n template.
- **MCP exposure**: serve the same declared tools over MCP, so one tool
  definition serves both the chat and an MCP client.
