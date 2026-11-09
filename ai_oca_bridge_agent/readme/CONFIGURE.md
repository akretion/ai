## Agent settings

| Setting | Default | What it does |
|---|---|---|
| AI User | required | Odoo user the answers are posted as. Must be linked to an `ai.bridge` with usage *Agent*. |
| Allowed groups | *Use the AI assistant* | Who may chat with the assistant. System administrators always keep access. |
| Access denied message | built-in text | Standard answer for users outside the allowed groups. |
| Denial cooldown (minutes) | 30 | Minimum delay between two denial answers in one channel (`0` = every message). |
| Require an Odoo API key for tools | on | Announce `odoo_rpc` only for users owning an API key. |
| Tenant key | generated | Stable database identifier shared by every agent of the database. Do not edit. |
| Environment | prod | `prod`/`staging`/`dev`, part of the responder session key. |
| Session scope | per conversation | One memory per Discuss thread, or `Per user` for one memory per person. |
| Backend | Hermes Agent | Which runtime answers: Hermes, n8n or a custom responder. |
| Agent key | generated | Stable key the responder uses to route this agent. |
| Model override | empty | Provider and model the responder should use; empty keeps its default. The credential stays in the responder. |
| Instructions / operator instructions | empty | What the agent is, and the rules that outrank anything a customer writes. |
| Skills | none | Prompt packs the agent loads; each skill may carry its own tools. |
| Skill loading | in the prompt | `On demand` sends summaries and loads a skill body through `/ai_agent/skills/get`. |
| Knowledge | none | Text blocks quoted to the responder, capped by *Knowledge max chars*. |
| Push enabled | off | Real-time push of the events to `push_url`. Empty = polling only. |
| Push secret | generated | HMAC secret used in both directions. Shown to system administrators. |
| Webhook token | generated | Token of the polling transport. Rotating it keeps the previous one valid for `token_grace_minutes`. |
| Max batch size | 50 | Messages claimed per poll or pushed per cron run. |
| Claim TTL (minutes) | 5 | Delay before an unanswered message is handed back to the queue. |
| Max attempts | 5 | Delivery attempts before the message becomes `dead`. |
| Long poll timeout | 0 | Up to 20 s of long polling on `/ai_agent/poll`. 0 disables it. |
| Allow users to connect | off | Show the **Connect** button in the users' preferences. |
| Credential validity (days) | 30 | Lifetime of the API key created by **Connect**. |
| Credential auto renew | on | Renew a consenting user's key before it expires (daily cron). |
| Renew before (days) | 7 | How early the renewal happens. |
| Credential idle days | 60 | After this many days without a message, revoke the key and forget the consent (`0` disables). |
| Tool access mode | Any model the user may read | Trust the user's own rights, or restrict the assistant to the grants. |
| Models hidden from the assistant | none | Deny list applied in both modes, for data that must not be quoted in a channel. |
| Extra allowed models | empty | Opens a model the default policy closes (prefixes allowed). Secrets stay closed. |
| Denied models | empty | Closes models for this agent; a deny always wins over an allow. |
| Config keys readable | `web.base.url` | Instance parameters the assistant may read, one key at a time. Prefixes with `*`. |

The model policy, the credential lifecycle and the trust boundaries are
described in the *Security model* section of `USAGE.md`.

## Instance-level overrides

An operator can widen or narrow the model policy without touching the agents:

```ini
; odoo.conf, or the environment of the service
AI_AGENT_MODEL_ALLOW = ir.attachment,crm.lead
AI_AGENT_MODEL_DENY = hr.employee
```

or, from Odoo, the `ai_bridge_agent.model_allow` and
`ai_bridge_agent.model_deny` system parameters (comma separated). Deny wins over
allow, and `ir.config_parameter`, `ir.mail_server`, `res.users.apikeys`,
`res.users.identitycheck`, `auth.totp` and `auth.passkey` can never be opened.

## Recommended presets

- **Single customer, behind a firewall**: push disabled, long poll 20 s, one
  agent worker. Nothing has to reach Odoo from outside.
- **Shared responder across tenants**: push enabled over HTTPS, HMAC secret per
  tenant, `environment` set per database, one agent worker per tenant queue.
- **Pilot with power users only**: allowed groups = a dedicated group, API key
  required for tools, tool access mode `Only the grants`.
