/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { money } from "../common/utils";

/** The month-end checklist with live counts, and the button that closes the period. */
export class CloseCockpit extends Component {
    static template = "ebshel_account_advanced.CloseCockpit";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({ loading: true, data: null, busy: false, periodId: (this.props.action && this.props.action.params && this.props.action.params.period_id) || null,
                                creating: false, form: {}, noteFor: null, note: "" });
        this.money = (v) => money(v, this.state.data && this.state.data.currency);
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call("ebshel.close.cockpit", "get_data", [this.state.periodId]);
            if (this.state.data.period) {
                this.state.periodId = this.state.data.period.id;
            }
        } finally {
            this.state.loading = false;
        }
    }
    pick(ev) {
        this.state.periodId = Number(ev.target.value) || null;
        this.load();
    }
    get ring() {
        const p = this.state.data && this.state.data.period ? this.state.data.period.progress : 0;
        const r = 44, c = 2 * Math.PI * r;
        return { r, c, off: c * (1 - p / 100), p };
    }
    get checks() {
        return (this.state.data.tasks || []).filter((t) => t.kind === "check");
    }
    get manual() {
        return (this.state.data.tasks || []).filter((t) => t.kind !== "check");
    }
    startCreate() {
        const d = new Date();
        const first = new Date(d.getFullYear(), d.getMonth() - 1, 1);
        const last = new Date(d.getFullYear(), d.getMonth(), 0);
        const iso = (x) => new Date(x.getTime() - x.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
        this.state.form = { name: first.toLocaleString(undefined, { month: "long", year: "numeric" }), from: iso(first), to: iso(last) };
        this.state.creating = true;
    }
    async create() {
        const f = this.state.form;
        this.state.periodId = await this.orm.call("ebshel.close.cockpit", "create_period", [f.from, f.to, f.name]);
        this.state.creating = false;
        await this.load();
    }
    skipTask(task) {
        if (this.state.noteFor === task.id) {
            return this.setTask(task, "skipped");
        }
        this.state.noteFor = task.id;
        this.state.note = "";
    }
    async setTask(task, state) {
        await this.orm.call("ebshel.close.cockpit", "set_task", [task.id, state, this.state.noteFor === task.id ? this.state.note : null]);
        this.state.noteFor = null;
        await this.load();
    }
    async assign(task, ev) {
        await this.orm.call("ebshel.close.cockpit", "assign", [task.id, Number(ev.target.value) || false]);
        task.owner_id = Number(ev.target.value) || false;
    }
    async openTask(task) {
        const act = await this.orm.call("ebshel.close.cockpit", "open_task", [task.id]);
        if (act) {
            this.action.doAction(act);
        }
    }
    openPeriod() {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "ebshel.close.period", res_id: this.state.periodId, views: [[false, "form"]] });
    }
    async closePeriod() {
        const p = this.state.data.period;
        if (!confirm(`Close ${p.name}?` + (p.lock_books ? ` The books will be locked up to ${p.to}.` : ""))) {
            return;
        }
        this.state.busy = true;
        try {
            await this.orm.call("ebshel.close.cockpit", "close", [p.id]);
            this.notification.add(`${p.name} closed.`, { type: "success" });
            await this.load();
        } finally {
            this.state.busy = false;
        }
    }
    async reopen() {
        await this.orm.call("ebshel.close.cockpit", "reopen", [this.state.periodId]);
        await this.load();
    }
}

registry.category("actions").add("ebshel_close_cockpit", CloseCockpit);
