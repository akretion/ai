# Hermes Demo Scenarios for ai_oca_bridge_agent

**Module**: `ai_oca_bridge_agent` **Version**: Odoo 18.0 / ai
18.0-add-ai_oca_bridge_hermes **Branch**: `18.0-add-ai_oca_bridge_hermes` **Last
commit**: `130235c` ([IMP] add /hermes/notify endpoint) + `da4f03c` ([IMP] smart
mention-based routing)

---

## Resurrection Command

To resume this conversation with full context in another session:

```bash
# From the project directory
cd ~/DEV/odoo18/odoo/external-src/ai

# Search past sessions for this topic
hermes search "ai_oca_bridge_agent demo scenario multi-tenant push notify"

# Or start a fresh session and load the skill
hermes --new-session
# Then: "Can you read ai_oca_bridge_agent/HERMES_DEMO.md and help me prepare the demo?"
```

**Key context**:

- Module: `~/DEV/odoo18/odoo/external-src/ai/ai_oca_bridge_agent/`
- Database: `odoo18-hermes` (port 8070)
- Gateway token: see setup notes
- Related PR: OCA/ai #69 (ai_tool by dixmit), PR #68 (ai_oca_mcp)
- Prototype code: `prototype_modules/hermes_tools_prototype.py`
- Soft dependency: `web_notify` (OCA/web) via `/hermes/notify`
- OCA-pre-commit wrapper fixed: `~/.local/bin/oca-pre-commit`

---

## Quick Start for the Demo

1. Install module on `odoo18-hermes` DB
2. Create gateway "Main Hermes" + AI user "Hermes"
3. Launch gateway:
   ```bash
   source ~/.hermes/venv/bin/activate
   python ~/DEV/odoo18/odoo/external-src/ai/ai_oca_bridge_agent/scripts/hermes_odoo_gateway.py \
       --odoo-url http://localhost:8069 \
       --webhook-token YOUR_TOKEN \
       --use-agent --model k2p6 --provider kimi-coding
   ```
4. Open Odoo Discuss, DM "Hermes" or use `@Hermès` in a channel

---

## Use Case 1: "Le Contrôleur de Gestion" 💰

**Theme**: Finance / invoicing **Wow factor**: Natural language to complex accounting
queries + proactive notification

### Flow (4 min)

1. **User** (in `#finance` channel): `@Hermès résume moi les factures impayées ce mois`
2. **Hermès** executes Odoo shell:
   ```python
   moves = env['account.move'].search([
       ('state', '=', 'posted'),
       ('payment_state', '!=', 'paid'),
       ('invoice_date', '>=', date.replace(day=1))
   ])
   total = sum(m.amount_total for m in moves)
   ```
3. **Hermès**: "42 factures impayées ce mois, total **127,450€**. Top 3 clients :\n- ABC
   Corp: 15,200€\n- DEF Industries: 12,800€\n- GHI Services: 8,900€"
4. **User**: `@Hermès envoie une relance aux 2 premiers`
5. **Hermès** uses `/hermes/notify` to send 2 notifications:
   ```bash
   curl -X POST http://localhost:8069/hermes/notify/TON_TOKEN \
     -d '{
       "user_id": 3,
       "message": "3 factures impayées totalisant 15,200€. Client en retard de 45 jours.",
       "title": "Relance requise - ABC Corp",
       "type": "warning",
       "sticky": true,
       "action": {"type": "ir.actions.act_window", "res_model": "res.partner", "res_id": 1, "views": [[false, "form"]]}
     }'
   ```
6. **Wow**: The responsible salesperson receives a **sticky browser toast** with a
   button that opens the customer form directly.

### Key features shown

- Odoo shell queries from Hermes (via tools in real future, terminal in demo)
- `/hermes/notify` with `web_notify` soft dependency
- Sticky notifications with action buttons
- Multi-channel awareness (Hermes knows Discuss + users)

---

## Use Case 2: "L'Urgence Production" 🏭

**Theme**: Manufacturing / stock **Wow factor**: Real-time diagnosis + human escalation
workflow

### Flow (4 min)

1. **Operator** (in `#production` channel):
   `@Hermès l'ordre de fabrication #1234 est bloqué, pourquoi ?`
2. **Hermès** queries stock moves:
   ```python
   mo = env['mrp.production'].browse(1234)
   missing = mo.move_raw_ids.filtered(lambda m: m.state == 'waiting')
   ```
3. **Hermès**: "Ordre #1234 bloqué — composant manquant : **Vis M8** (réf:
   VIS001)\nStock disponible : 0\nRéservation en attente : 150 unités\nFournisseur :
   Fastenal (délai 2 jours)"
4. **Operator**: `@Hermès demande au responsable stock de débloquer`
5. **Hermès** uses `/hermes/notify` to the stock manager:
   ```bash
   curl -X POST http://localhost:8069/hermes/notify/TON_TOKEN \
     -d '{
       "user_id": 4,
       "message": "Déblocage requis pour 150x Vis M8 (OF #1234). Fournisseur Fastenal, délai 2j.",
       "title": "🚨 Demande de déblocage stock",
       "type": "danger",
       "sticky": true,
       "action": {"type": "ir.actions.act_window", "res_model": "mrp.production", "res_id": 1234, "views": [[false, "form"]]}
     }'
   ```
6. **Manager** clicks the toast → opens the MO form → validates the reservation
7. **Wow**: The operator sees Hermes post "✅ Réservation débloquée par [Manager]!" in
   the channel

### Key features shown

- Multi-model analysis (mrp, stock, purchase)
- Proactive notification to another user
- Action button opening the exact form
- Human-in-the-loop approval pattern

---

## Use Case 3: "Le Support Client Proactif" 🎧

**Theme**: Sales / customer support **Wow factor**: Predictive alert + cross-department
coordination

### Flow (4 min)

1. **Hermès** (proactive via `/hermes/push`): detects SO-2026-0456 is 3 days late
   ```bash
   curl -X POST http://localhost:8069/hermes/push/TON_TOKEN \
     -d '{"channel_id": 5, "body": "⚠️ Alerte: Commande SO-2026-0456 pour Client XYZ en retard de 3 jours. Cause probable: transporteur non assigné."}'
   ```
2. **Support lead** (in `#support`): `@Hermès donne moi le statut de livraison`
3. **Hermès**: "Commande validée, picking terminé. Transporteur NON assigné. Délai
   estimé +2 jours si expédié aujourd'hui."
4. **Support lead**: `@Hermès propose 10% de remise au client`
5. **Hermès** notifies the sales manager:
   ```bash
   curl -X POST http://localhost:8069/hermes/notify/TON_TOKEN \
     -d '{
       "user_id": 5,
       "message": "SO-2026-0456 en retard de 3j. Support demande remise 10% pour maintenir la relation. Montant estimé: 450€.",
       "title": "Approbation remise requise",
       "type": "warning",
       "sticky": true,
       "action": {"type": "ir.actions.act_window", "res_model": "sale.order", "res_id": 456, "views": [[false, "form"]]}
     }'
   ```
6. **Manager** approves → Hermès confirms in the channel
7. **Wow**: Predictive alert + automated analysis + cross-team coordination without
   leaving Discuss

### Key features shown

- `/hermes/push` (proactive message from Hermes)
- Predictive detection (Hermès initiates before user asks)
- `/hermes/notify` for cross-department escalation
- Full conversation loop in Discuss

---

## Use Case 4: "L'Achat Intelligent" 🛒

**Theme**: Purchasing / inventory management **Wow factor**: From scarcity detection to
PO creation in seconds

### Flow (4 min)

1. **Buyer** (in `#achats`): `@Hermès quels produits sont en rupture critique ?`
2. **Hermès** complex query:
   ```python
   # Products below minimum stock AND with pending sale orders
   critical = env['product.product'].search([
       ('orderpoint_ids.product_min_qty', '>', 0),
       ('orderpoint_ids.qty_on_hand', '<', 0)
   ])
   ```
3. **Hermès**: "**5 produits en rupture critique** :\n- Carte mère X200: 5 jours de
   rupture, 3 commandes client en attente\n- Capteur Temp A1: 3 jours, 1 commande\n-
   Boulon M10: 12 jours (stock de sécurité dépassé)\n- ..."
4. **Buyer**: `@Hermès génère un RFQ pour 200 unités de Carte mère X200`
5. **Hermès** creates draft PO:
   ```python
   po = env['purchase.order'].create({
       'partner_id': supplier_id,
       'order_line': [(0, 0, {
           'product_id': product_x200.id,
           'product_qty': 200,
       })],
   })
   ```
6. **Hermès** notifies buyer:
   ```bash
   curl -X POST http://localhost:8069/hermes/notify/TON_TOKEN \
     -d '{"user_id": 6, "message": "RFQ PO-2026-0892 créé pour 200x Carte mère X200", "title": "RFQ généré", "type": "success", "action": {"type": "ir.actions.act_window", "res_model": "purchase.order", "res_id": po.id}}'
   ```
7. **Wow**: From question to PO draft in 30 seconds, entirely through chat

### Key features shown

- Complex analytical queries
- Write operations from Hermes (create PO)
- Immediate post-action confirmation with action button

---

## Use Case 5: "L'Escalade Multi-Hermès" 🌐

**Theme**: Multi-tenant / architecture vision **Wow factor**: Two AI agents cooperating
across databases

### Flow (4 min)

1. **Customer's Hermès** (running locally, cheap model):
   ```
   Customer: "J'ai un problème de TVA sur mes déclarations Q2"
   Customer Hermès: "Je vais chercher... Je trouve des écarts sur les pièces AJ-2026-0012 à 0018 mais je ne peux pas résoudre seul."
   ```
2. **Customer Hermès** uses `/hermes/push` on Akretion's shared channel:
   ```bash
   curl -X POST https://akretion-odoo.com/hermes/push/TOKEN \
     -d '{"channel_id": 10, "body": "🚨 ESCALADE Client XYZ (DB: client_xyz)\nErreur TVA Q2 non résolue.\nPièces concernées: AJ-2026-0012 à 0018\nMon analyse: écart de 2,340€"}'
   ```
3. **Akretion Hermès** (premium model) sees the message, reads the context
4. **Akretion Hermès**: "Analyse terminée. L'écart vient d'une erreur de codage TVA sur
   3 factures. Solution : lettrage manuel sur les pièces AJ-2026-0012 à 0018."
5. **Akretion Hermès** notifies the accounting consultant:
   ```bash
   curl -X POST https://akretion-odoo.com/hermes/notify/TOKEN \
     -d '{"user_id": 7, "message": "Pièces à lettrer: AJ-2026-0012 à 0018. Écart TVA: 2,340€", "title": "Intervention client XYZ", "type": "danger", "sticky": true}'
   ```
6. **Consultant** opens the journal entries, fixes the issue
7. **Wow**: Two Hermès instances from different companies cooperating — cheap triage +
   premium expertise

### Key features shown

- Multi-tenant architecture (different Odoo DBs)
- `/hermes/push` across Odoo instances
- Inter-Hermès communication pattern
- Tiered AI strategy (cheap local + expensive cloud)

---

## Recommendation Matrix

| Criteria            | UC1 Finance  | UC2 Production  | UC3 Support       | UC4 Purchasing  | UC5 Multi-Hermès |
| ------------------- | ------------ | --------------- | ----------------- | --------------- | ---------------- |
| **Wow effect**      | ⭐⭐⭐       | ⭐⭐⭐⭐⭐      | ⭐⭐⭐⭐          | ⭐⭐⭐          | ⭐⭐⭐⭐⭐       |
| **Realistic**       | ✅           | ✅✅✅          | ✅✅✅            | ✅✅            | ✅✅             |
| **Shows push**      | ❌           | ❌              | ✅✅✅            | ✅              | ✅✅✅           |
| **Shows notify**    | ✅✅         | ✅✅✅          | ✅✅✅            | ✅✅            | ✅✅✅           |
| **Shows @mention**  | ✅           | ✅✅            | ✅                | ✅              | ✅               |
| **Technical depth** | Medium       | High            | High              | High            | Very High        |
| **Best for**        | Finance demo | Production demo | Full product demo | Purchasing demo | Vision demo      |

### Top picks

- **For a 4-min "wow"**: Use Case 2 (Production) — it has the most emotional impact
  (urgency + resolution)
- **For a full product demo**: Use Case 3 (Support) — covers all endpoints, most
  complete
- **For strategic vision**: Use Case 5 (Multi-Hermès) — shows where you're going

---

## Notes for the Demo

- Have 2 browser windows ready: Odoo Discuss + Odoo form view
- Pre-create some demo data: invoices, production orders, customers
- Pre-configure the gateway token in the push/notify examples above
- All curl commands assume `localhost:8069`, replace with real URL for cloud demo

---

## Resurrection Command

To resume this demo preparation session with full context:

```bash
# From the project directory
cd ~/DEV/odoo18/odoo/external-src/ai

# Search past sessions for this topic
hermes search "ai_oca_bridge_agent demo scenario multi-tenant push notify"

# Or start a fresh session and load the skill
hermes --new-session
# Then say: "Can you read ai_oca_bridge_agent/HERMES_DEMO.md and help me prepare the demo?"
```

**Key context**:

- Module: `~/DEV/odoo18/odoo/external-src/ai/ai_oca_bridge_agent/`
- Database: `odoo18-hermes` (port 8070)
- Gateway token: see setup notes
- Related PR: OCA/ai #69 (ai_tool by dixmit), PR #68 (ai_oca_mcp)
- Prototype code: `prototype_modules/hermes_tools_prototype.py`
- Soft dependency: `web_notify` (OCA/web) via `/hermes/notify`
- OCA-pre-commit wrapper fixed: `~/.local/bin/oca-pre-commit`
