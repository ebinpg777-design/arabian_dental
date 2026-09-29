/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { money, compact, today, addDays } from "../common/utils";

/** Every customer who owes money, what to do next, and the buttons that do it. */
export class FollowupDesk extends Component {
    static template = "ebshel_account_advanced.FollowupDesk";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({
            loading: true, data: null, busy: false,
            filters: { state: [], responsible_id: false, search: "", sort: "overdue" },
            selected: {}, detail: null, modal: null, form: {},
        });
        this.money = (v) => money(v, this.state.data && this.state.data.currency);
        this.compact = (v) => compact(v, this.state.data && this.state.data.currency);
        onWillStart(async () => {
            await this.load();
            const pid = this.props.action && this.props.action.params && this.props.action.params.partner_id;
            if (pid) {
                await this.openDetail(pid);
            }
        });
    }

    async load() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call("ebshel.followup.desk", "get_data", [this.state.filters]);
        } finally {
            this.state.loading = false;
        }
    }

    get rows() {
        return (this.state.data && this.state.data.rows) || [];
    }
    get maxOverdue() {
        return Math.max(...this.rows.map((r) => r.overdue), 1);
    }
    get selectedIds() {
        return Object.keys(this.state.selected).filter((k) => this.state.selected[k]).map(Number);
    }
    get bucketTotal() {
        return Math.max((this.state.data.buckets || []).reduce((a, b) => a + b.value, 0), 1);
    }
    stateClass(s) {
        return { action: "bad", promised: "info", reminded: "muted", overdue: "warn", due: "good", excluded: "muted", none: "muted" }[s] || "";
    }

    toggleState(key) {
        const list = this.state.filters.state;
        const i = list.indexOf(key);
        if (i >= 0) {
            list.splice(i, 1);
        } else {
            list.push(key);
        }
        this.load();
    }
    setFilter(key, value) {
        this.state.filters[key] = value;
        this.load();
    }
    toggleAll(ev) {
        const on = ev.target.checked;
        for (const r of this.rows) {
            this.state.selected[r.id] = on;
        }
    }

    // ----------------------------------------------------------- detail panel
    async openDetail(partnerId) {
        this.state.detail = await this.orm.call("ebshel.followup.desk", "partner_detail", [partnerId]);
    }
    closeDetail() {
        this.state.detail = null;
    }
    openPartner(id) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "res.partner", res_id: id, views: [[false, "form"]] });
    }
    openMove(id) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "account.move", res_id: id, views: [[false, "form"]] });
    }
    openStatement(partnerId) {
        this.action.doAction({ type: "ir.actions.client", tag: "ebshel_fin_report", name: "Statement of account",
                               params: { report_key: "customer_statement", options: { partners: [partnerId], unfold_all: true } } });
    }
    async saveNote() {
        await this.orm.call("ebshel.followup.desk", "save_note", [this.state.detail.id, this.state.detail.note]);
        this.notification.add("Note saved", { type: "success" });
    }

    // ----------------------------------------------------------- modals
    open(kind, row) {
        const ids = row ? [row.id] : this.selectedIds;
        if (!ids.length) {
            this.notification.add("Pick at least one customer first.", { type: "warning" });
            return;
        }
        this.state.modal = { kind, ids, row };
        this.state.form = { level_id: (row && row.suggested && row.suggested.id) || false, note: "", date: addDays(today(), 7),
                            amount: row ? row.overdue : 0, days: 7, next_date: "" };
    }
    close() {
        this.state.modal = null;
    }
    async run(fn, done) {
        this.state.busy = true;
        try {
            const res = await fn();
            if (done) {
                done(res);
            }
            this.state.modal = null;
            this.state.selected = {};
            await this.load();
            if (this.state.detail) {
                await this.openDetail(this.state.detail.id);
            }
        } finally {
            this.state.busy = false;
        }
    }
    async submit() {
        const m = this.state.modal, f = this.state.form, Desk = "ebshel.followup.desk";
        if (m.kind === "send") {
            await this.run(() => this.orm.call(Desk, "send", [m.ids, f.level_id || null, f.note || null]), (res) => {
                const skipped = res.skipped.map((s) => `${s.name} (${s.why})`).join(", ");
                this.notification.add(`${res.sent.length} reminder(s) sent` + (skipped ? `; skipped: ${skipped}` : ""),
                                      { type: res.sent.length ? "success" : "warning" });
                if (res.letters && res.letters.length) {
                    this.printLetters(res.letters);
                }
            });
        } else if (m.kind === "call") {
            await this.run(() => this.orm.call(Desk, "log_call", [m.ids[0], f.note, f.next_date || null]));
        } else if (m.kind === "promise") {
            await this.run(() => this.orm.call(Desk, "promise", [m.ids[0], f.date, Number(f.amount) || 0, f.note || null]));
        } else if (m.kind === "snooze") {
            await this.run(() => this.orm.call(Desk, "snooze", [m.ids[0], Number(f.days) || 7, f.note || null]));
        }
    }
    async flag(row, flag, value) {
        await this.run(() => this.orm.call("ebshel.followup.desk", "set_flag", [row.id, flag, value]));
    }
    async setResponsible(row, ev) {
        await this.orm.call("ebshel.followup.desk", "set_responsible", [row.id, Number(ev.target.value) || false]);
        row.responsible_id = Number(ev.target.value) || false;
    }
    async printLetters(ids) {
        const act = await this.orm.call("ebshel.followup.desk", "letter", [ids || this.selectedIds, null]);
        if (act) {
            this.action.doAction(act);
        }
        await this.load();
    }
    async sendAllDue() {
        const ids = this.rows.filter((r) => r.state === "action" && r.email).map((r) => r.id);
        if (!ids.length) {
            this.notification.add("Nobody is due a reminder by email right now.", { type: "info" });
            return;
        }
        this.state.modal = { kind: "send", ids, row: null };
        this.state.form = { level_id: false, note: "" };
    }
}

registry.category("actions").add("ebshel_followup_desk", FollowupDesk);
