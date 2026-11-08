## Agent settings

| Setting | Default | What it does |
|---|---|---|
| AI User | required | Odoo user the answers are posted as. Must be linked to an `ai.bridge` with usage *Hermes*. |
| Allowed groups | *Use Hermes AI* | Who may chat with the assistant. System administrators always keep access. |
| Access denied message | built-in text | Standard answer for users outside the allowed groups. |
| Denial cooldown (minutes) | 30 | Minimum delay between two denial answers in one channel (`0` = every message). |
| Require an Odoo API key for tools | on | Announce `odoo_rpc` only for users owning an API key. |
| Tenant key | generated | Stable database identifier shared by every agent of the database. Do not edit. |
| Environment | prod | `prod`/`staging`/`dev`, part of the Hermes session key. |
| Session scope | per user | One persistent Hermes conversation per user, or per channel. |
| Push enabled | off | Real-time push of the events to `push_url`. Empty = polling only. |
| Push secret | generated | HMAC secret used in both directions. Shown to system administrators. |
| Webhook token | generated | Token of the polling transport. Rotating it keeps the previous one valid for `token_grace_minutes`. |
| Max batch size | 50 | Messages claimed per poll or pushed per cron run. |
| Claim TTL (minutes) | 5 | Delay before an unanswered message is handed back to the queue. |
| Max attempts | 5 | Delivery attempts before the message becomes `dead`. |
| Long poll timeout | 0 | Up to 20 s of long polling on `/ai_agent/poll`. 0 disables it. |
| Allow users to connect | off | Show the **Connect Hermes** button in the users' preferences. |
| Credential validity (days) | 30 | Lifetime of the API key created by **Connect**. |
| Backend | Hermes Agent | Which runtime answers: Hermes, n8n or a custom responder. |
| Agent key | generated | Stable key the responder uses to route this agent. |
| Model override | empty | Provider and model the responder should use; empty keeps its default. The credential stays in the responder. |
| Tool access mode | Any model the user may read | Trust the user's own rights, or restrict the assistant to the Tools list. |
| Models hidden from the assistant | none | Deny list applied in both modes, for data that must not be quoted in a channel. |

## Recommended presets

- **Single customer, behind a firewall**: push disabled, long poll 20 s, one
  agent worker. Nothing has to reach Odoo from outside.
- **Akretion-style shared Hermes**: push enabled over HTTPS, HMAC secret per
  tenant, `environment` set per database, one agent worker per tenant queue.
- **Pilot with power users only**: allowed groups = a dedicated group, API key
  required for tools, session scope per user.
