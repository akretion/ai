/** @odoo-module **/

import {registry} from "@web/core/registry";
import {rpc} from "@web/core/network/rpc";
import {user} from "@web/core/user";

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

/** Channel id of a Discuss URL, so the context belongs to that conversation. */
function currentChannelId(href) {
    const discuss = href.match(/\/discuss\/(\d+)/);
    if (discuss) {
        return parseInt(discuss[1], 10);
    }
    const param = href.match(/[?&#]channel(?:_id)?=(\d+)/);
    return param ? parseInt(param[1], 10) : 0;
}

async function reportCurrentPage() {
    const href = window.location.href;
    if (href === lastReportedHref) {
        return;
    }
    lastReportedHref = href;
    try {
        await rpc(ENDPOINT, {
            url: href,
            action_name: document.title,
            channel_id: currentChannelId(href),
        });
    } catch {
        // Never disturb the user: retry on the next tick.
        lastReportedHref = null;
    }
}

export const agentContextService = {
    async start() {
        // Nothing leaves the browser for users who cannot use the assistant.
        let allowed = false;
        try {
            allowed =
                (await user.hasGroup("ai_oca_bridge_agent.group_agent_user")) ||
                (await user.hasGroup("base.group_system"));
        } catch {
            allowed = false;
        }
        if (!allowed) {
            return;
        }
        reportCurrentPage();
        window.setInterval(reportCurrentPage, REPORT_INTERVAL_MS);
    },
};

registry.category("services").add("ai_agent_context", agentContextService);
