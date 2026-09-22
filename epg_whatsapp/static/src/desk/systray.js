/** @odoo-module **/

import { Component, onWillDestroy, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { useBus, useService } from "@web/core/utils/hooks";

/** The number of WhatsApp messages waiting for this person, one tap from the Desk. */
export class WaSystray extends Component {
    static template = "epg_whatsapp.Systray";
    static props = {};

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ allowed: false, count: 0 });
        onWillStart(async () => {
            this.state.allowed = await user.hasGroup("epg_whatsapp.group_whatsapp_user");
            if (this.state.allowed) {
                await this.refresh();
                this.timer = setInterval(() => this.refresh(), 90000);
            }
        });
        onWillDestroy(() => clearInterval(this.timer));
        useBus(this.env.bus, "EPG_WA:CHANGED", () => this.refresh());
    }

    async refresh() {
        if (!this.state.allowed) {
            return;
        }
        try {
            this.state.count = await this.orm.silent.call(
                "epg.whatsapp.desk", "pending_count", []);
        } catch {
            // A failed poll is not worth an error dialog in the top bar.
        }
    }

    open() {
        this.action.doAction("epg_whatsapp.action_desk");
    }
}

registry.category("systray").add("epg_whatsapp.desk", { Component: WaSystray }, { sequence: 40 });
