/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { money } from "../common/utils";

/** A score for the books and the findings that cost it points. */
export class LedgerHealth extends Component {
    static template = "ebshel_account_advanced.LedgerHealth";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({ loading: true, data: null, busy: false, filter: "all" });
        this.money = (v) => money(v, this.state.data && this.state.data.currency);
        onWillStart(() => this.load());
    }
    async load() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call("ebshel.ledger.scanner", "get_data", []);
        } finally {
            this.state.loading = false;
        }
    }
    async scan() {
        this.state.busy = true;
        try {
            const res = await this.orm.call("ebshel.ledger.scanner", "scan", []);
            this.notification.add(`Scan done: ${res.created} new, ${res.updated} updated, ${res.resolved} resolved by themselves.`, { type: "success" });
            await this.load();
        } finally {
            this.state.busy = false;
        }
    }
    get findings() {
        const all = (this.state.data && this.state.data.findings) || [];
        return this.state.filter === "all" ? all : all.filter((f) => f.severity === this.state.filter);
    }
    get gauge() {
        const s = this.state.data ? this.state.data.score : 0;
        const r = 50, c = Math.PI * r;
        return { r, c, off: c * (1 - s / 100), color: s >= 85 ? "#067647" : s >= 60 ? "#b45309" : "#b42318", s };
    }
    sevClass(s) {
        return { error: "bad", warn: "warn", info: "info" }[s] || "muted";
    }
    async open(f) {
        const act = await this.orm.call("ebshel.ledger.scanner", "open_finding", [f.id]);
        if (act) {
            this.action.doAction(act);
        }
    }
    async set(f, state) {
        await this.orm.call("ebshel.ledger.scanner", "set_state", [f.id, state]);
        await this.load();
    }
}

registry.category("actions").add("ebshel_ledger_health", LedgerHealth);
