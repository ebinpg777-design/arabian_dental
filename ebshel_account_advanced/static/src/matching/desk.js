/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { money } from "../common/utils";

/** Open items paired with a confidence score; bank lines matched to what they paid. */
export class MatchDesk extends Component {
    static template = "ebshel_account_advanced.MatchDesk";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({ loading: true, data: null, busy: false, kind: "receivable", search: "", minConf: 0,
                                partnerId: null, journalId: null, selected: {}, picks: {}, open: {} });
        this.money = (v) => money(v, this.state.data && this.state.data.currency);
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call("ebshel.match.desk", "get_data", [{
                kind: this.state.kind, search: this.state.search, min_confidence: this.state.minConf,
                partner_id: this.state.partnerId, journal_id: this.state.journalId,
            }]);
            this.state.selected = {};
        } finally {
            this.state.loading = false;
        }
    }
    setKind(kind) {
        this.state.kind = kind;
        this.state.partnerId = null;
        this.load();
    }
    pickPartner(id) {
        this.state.partnerId = this.state.partnerId === id ? null : id;
        this.load();
    }
    confClass(c) {
        return c >= 90 ? "good" : c >= 75 ? "info" : c >= 60 ? "warn" : "muted";
    }
    get pairs() {
        return (this.state.data && this.state.data.pairs) || [];
    }
    get selectedPairs() {
        return this.pairs.filter((p) => this.state.selected[p.key]);
    }
    openMove(id) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "account.move", res_id: id, views: [[false, "form"]] });
    }
    async apply(pairs) {
        if (!pairs.length) {
            this.notification.add("Pick a suggestion first.", { type: "warning" });
            return;
        }
        this.state.busy = true;
        try {
            const res = await this.orm.call("ebshel.match.desk", "apply", [pairs.map((p) => ({ key: p.key, aml_ids: p.aml_ids }))]);
            this.notification.add(`${res.done} matched, ${res.full} fully cleared` + (res.skipped.length ? `, ${res.skipped.length} skipped` : ""), { type: "success" });
            await this.load();
        } finally {
            this.state.busy = false;
        }
    }
    async applySure() {
        this.state.busy = true;
        try {
            const res = await this.orm.call("ebshel.match.desk", "auto_apply", [this.state.kind, 90]);
            this.notification.add(`${res.done} matched, ${res.full} fully cleared`, { type: "success" });
            await this.load();
        } finally {
            this.state.busy = false;
        }
    }
    // bank
    togglePick(rowId, amlId, ev) {
        const set = this.state.picks[rowId] || (this.state.picks[rowId] = {});
        set[amlId] = ev.target.checked;
    }
    pickedIds(row) {
        const set = this.state.picks[row.id];
        if (set && Object.values(set).some(Boolean)) {
            return Object.keys(set).filter((k) => set[k]).map(Number);
        }
        return row.best ? [row.best.id] : [];
    }
    async matchBank(row) {
        const ids = this.pickedIds(row);
        if (!ids.length) {
            this.notification.add("Choose what this bank line paid.", { type: "warning" });
            return;
        }
        this.state.busy = true;
        try {
            const res = await this.orm.call("ebshel.match.desk", "match_bank", [row.id, ids]);
            this.notification.add(res.reconciled ? "Bank line matched." : `Matched ${res.pairs} item(s); ${this.money(res.left)} left on suspense.`, { type: "success" });
            await this.load();
        } finally {
            this.state.busy = false;
        }
    }
}

registry.category("actions").add("ebshel_match_desk", MatchDesk);
