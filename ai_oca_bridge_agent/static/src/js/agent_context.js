/** @odoo-module **/

import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";

/**
 * Report the page the user is browsing, so the assistant can answer "this
 * record", "this list", "this form" without being told.
 *
 * The value is a hint for the responder, never an access control: every read
 * still goes through the user's own Odoo API key. Sending the URL only (plus
 * the page title) keeps it cheap and impossible to forge into a permission.
 */
const REPORT_INTERVAL_MS = 3000;
const ENDPOINT = "/ai_agent/web_context";

let lastReportedHref = null;

async function reportCurrentPage() {
    const href = window.location.href;
    if (href === lastReportedHref) {
        return;
    }
    lastReportedHref = href;
    try {
        await rpc(ENDPOINT, { url: href, action_name: document.title });
    } catch {
        // Never disturb the user: retry on the next tick.
        lastReportedHref = null;
    }
}

export const agentContextService = {
    start() {
        reportCurrentPage();
        window.setInterval(reportCurrentPage, REPORT_INTERVAL_MS);
    },
};

registry.category("services").add("ai_agent_context", agentContextService);
