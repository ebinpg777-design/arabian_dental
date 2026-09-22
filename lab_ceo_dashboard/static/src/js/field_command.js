/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";
import { ErrorHandler } from "@web/core/utils/components";
import { useService } from "@web/core/utils/hooks";

const REMEMBER = "lab_field_command.screen";

/**
 * Field Command — everything behind the Management hub's Field Force tile.
 *
 * One user's "field force" is a queue to sign, another's is a chart to read.
 * The server says which screens this user's roles earn (the role desks, then
 * the analysis pulse for everyone) and this component is only the switcher over
 * them: each tab carries a live badge of what is waiting there, the default is
 * the most specific role's own desk, and the last choice is remembered — a
 * manager who lives in Analysis is not dragged back to their queue every
 * morning. The screens themselves stay whole: the same components, mounted the
 * same way the hub mounts this one. (client, 2026-08-31)
 */
export class LabFieldCommand extends Component {
    static template = "lab_ceo_dashboard.FieldCommand";
    static components = { ErrorHandler };
    static props = ["*"];

    setup() {
        this.actionService = useService("action");
        this.orm = useService("orm");
        this.state = useState({ screens: [], active: null, loading: true, failed: {} });
        onWillStart(() => this.load());
    }

    async load() {
        const data = await this.orm.call("lab.ceo.dashboard", "get_field_command", []);
        // A tag not in the registry (module removed, asset failure) is a tab
        // that cannot open; better absent than a white screen behind a click.
        this.state.screens = data.screens.filter(
            (s) => registry.category("actions").get(s.tag, null));
        let remembered = null;
        try {
            remembered = browser.localStorage.getItem(REMEMBER);
        } catch {
            remembered = null;
        }
        const first = this.state.screens.find((s) => s.key === remembered)
            || this.state.screens[0];
        this.state.active = first ? first.key : null;
        this.state.loading = false;
    }

    get active() {
        return this.state.screens.find((s) => s.key === this.state.active) || null;
    }

    select(screen) {
        this.state.active = screen.key;
        try {
            browser.localStorage.setItem(REMEMBER, screen.key);
        } catch {
            // private mode: nothing to remember it in
        }
    }

    component(screen) {
        const entry = registry.category("actions").get(screen.tag, null);
        return entry && entry.prototype instanceof Component ? entry : null;
    }

    /** The same prop shape the web client's action service hands a client
     *  action, so the desks and the pulse cannot tell they are embedded. */
    screenProps(screen) {
        return {
            action: {
                id: false,
                type: "ir.actions.client",
                tag: screen.tag,
                params: screen.params || {},
                context: {},
                display_name: screen.label,
                name: screen.label,
            },
            actionId: false,
            className: "o_fcmd_embedded",
            updateActionState: () => {},
        };
    }

    onScreenError(screen, error) {
        // Once broken, stay out of the way: drop the tab and move on rather
        // than re-mounting the same crash on every render.
        this.state.failed[screen.key] = true;
        this.state.screens = this.state.screens.filter((s) => s.key !== screen.key);
        if (this.state.active === screen.key) {
            this.state.active = this.state.screens.length
                ? this.state.screens[0].key : null;
        }
        console.error("field command: screen failed", screen.key, error);
    }
}

registry.category("actions").add("lab_field_command", LabFieldCommand);
