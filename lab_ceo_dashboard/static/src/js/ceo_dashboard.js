/** @odoo-module **/

import { Component, onMounted, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { browser } from "@web/core/browser/browser";
import { evaluateExpr } from "@web/core/py_js/py";
import { ErrorHandler } from "@web/core/utils/components";
import { View } from "@web/views/view";
import { getCurrency } from "@web/core/currency";
import { formatMonetary, formatFloat } from "@web/views/fields/formatters";

import { shortINR } from "@lab_ceo_dashboard/js/inr";

const REMEMBER = "lab_ceo_hub.active";

/**
 * The Management workspace.
 *
 * A strip of launchers that never leaves the screen, and the chosen screen
 * mounted right under it: switching is one click or one number key, and there
 * is nothing to "go back" from. Client screens are mounted as the components
 * they already are; list screens as embedded views. Anything that will not
 * mount falls back to opening full screen. (client, 2026-08-28)
 */
export class LabCeoDashboard extends Component {
    static template = "lab_ceo_dashboard.CeoDashboard";
    static components = { View, ErrorHandler };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ launchers: [], loading: true, active: null, failed: {} });
        onWillStart(() => this.loadLaunchers());
        this.onKey = (ev) => {
            if (ev.target && /INPUT|TEXTAREA|SELECT/.test(ev.target.tagName)) {
                return;                   // never steal a keystroke meant for a field
            }
            if (ev.altKey || ev.ctrlKey || ev.metaKey) {
                return;
            }
            const launcher = this.state.launchers.find((l) => l.shortcut === ev.key);
            if (launcher) {
                ev.preventDefault();
                this.select(launcher);
            }
        };
        onMounted(() => document.addEventListener("keydown", this.onKey));
        onWillUnmount(() => document.removeEventListener("keydown", this.onKey));
    }

    async loadLaunchers() {
        this.state.loading = true;
        this.state.launchers = await this.orm.call("lab.ceo.dashboard", "get_launchers", []);
        this.state.loading = false;
        // Open where they left off; the first screen otherwise.
        let remembered = null;
        try {
            remembered = browser.localStorage.getItem(REMEMBER);
        } catch {
            remembered = null;
        }
        const first = this.state.launchers.find((l) => l.key === remembered) || this.state.launchers[0];
        if (first) {
            this.select(first);
        }
    }

    get active() {
        return this.state.launchers.find((l) => l.key === this.state.active) || null;
    }

    select(launcher) {
        if (this.state.failed[launcher.key]) {
            return this.openFull(launcher);
        }
        this.state.active = launcher.key;
        try {
            browser.localStorage.setItem(REMEMBER, launcher.key);
        } catch {
            // private mode: nothing to remember it in
        }
    }

    /** The same screen as a full page, with the breadcrumb back to here. */
    openFull(launcher) {
        this.action.doAction(launcher.action_id);
    }

    // ------------------------------------------------------------ embedding
    /** The registered component behind a client action, if there is one. */
    component(launcher) {
        if (launcher.kind !== "client") {
            return null;
        }
        const entry = registry.category("actions").get(launcher.tag, null);
        return entry && entry.prototype instanceof Component ? entry : null;
    }

    clientProps(launcher) {
        return {
            action: {
                id: launcher.action_id,
                type: "ir.actions.client",
                tag: launcher.tag,
                params: launcher.params || {},
                context: {},
                display_name: launcher.title,
                name: launcher.title,
            },
            actionId: launcher.action_id,
            className: "o_ceo_embedded",
            updateActionState: () => {},
        };
    }

    viewProps(launcher) {
        const evalCtx = { uid: this.env.services.user?.userId, context_today: () => luxon.DateTime.local() };
        let domain = [];
        let context = {};
        try {
            domain = evaluateExpr(launcher.domain || "[]", evalCtx);
        } catch {
            domain = [];
        }
        try {
            context = evaluateExpr(launcher.context || "{}", evalCtx);
        } catch {
            context = {};
        }
        const type = (launcher.views && launcher.views[0] && launcher.views[0][1]) || "list";
        return {
            type,
            resModel: launcher.res_model,
            domain,
            context,
            views: launcher.views,
            searchViewId: launcher.search_view_id || false,
            display: { controlPanel: { "top-right": true, "bottom-right": true } },
        };
    }

    /** A screen that would not mount is opened full screen instead, once. */
    onEmbedError(launcher, error) {
        console.warn("Management hub: could not embed", launcher.key, error);
        this.state.failed[launcher.key] = true;
        this.state.active = null;
        this.openFull(launcher);
    }

    // ------------------------------------------------------------ figures
    /**
     * The number on the tile. Money is said the way this lab says money —
     * 25.9 Cr, not 259,043,615.31, which reads as a phone number at a glance.
     * The exact amount survives in the tooltip (see exact()).
     */
    figure(launcher) {
        if (launcher.value === null || launcher.value === undefined) {
            return "";
        }
        if (!launcher.money) {
            return formatFloat(launcher.value || 0, { digits: [16, 0] });
        }
        const cur = getCurrency(launcher.currency_id);
        const short = shortINR(launcher.value || 0);
        if (!cur) {
            return short;
        }
        return cur.position === "before"
            ? `${cur.symbol} ${short}` : `${short} ${cur.symbol}`;
    }

    /** The full amount, for the tooltip — shortening must never hide paise
     *  from whoever hovers to check. */
    exact(launcher) {
        if (launcher.value === null || launcher.value === undefined) {
            return "";
        }
        return launcher.money
            ? formatMonetary(launcher.value || 0, { currencyId: launcher.currency_id })
            : formatFloat(launcher.value || 0, { digits: [16, 0] });
    }
}

registry.category("actions").add("lab_ceo_dashboard", LabCeoDashboard);
