/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { money, compact, pct } from "../common/utils";

/** One budget at a glance: bars per line, the months, the run-rate projection. */
export class BudgetBoard extends Component {
    static template = "ebshel_account_advanced.BudgetBoard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ loading: true, data: null, budgetId: (this.props.action && this.props.action.params && this.props.action.params.budget_id) || null });
        this.money = (v) => money(v, this.state.data && this.state.data.currency);
        this.compact = (v) => compact(v, this.state.data && this.state.data.currency);
        this.pct = pct;
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call("ebshel.budget.board", "get_data", [this.state.budgetId]);
            if (this.state.data.budget) {
                this.state.budgetId = this.state.data.budget.id;
            }
        } finally {
            this.state.loading = false;
        }
    }
    pick(ev) {
        this.state.budgetId = Number(ev.target.value) || null;
        this.load();
    }
    openBudget() {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "ebshel.budget", res_id: this.state.budgetId, views: [[false, "form"]] });
    }
    newBudget() {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "ebshel.budget", views: [[false, "form"]], target: "current" });
    }
    openReport() {
        const b = this.state.data.budget;
        this.action.doAction({ type: "ir.actions.client", tag: "ebshel_fin_report", name: "Budget vs Actual",
                               params: { report_key: "budget_vs_actual", options: { date: { preset: "custom", from: b.from, to: b.to }, unfold_all: true } } });
    }
    async openLine(line) {
        const act = await this.orm.call("ebshel.budget.line", "action_view_actuals", [[line.id]]);
        this.action.doAction(act);
    }
    width(value, top) {
        return Math.max(0, Math.min(100, (value / Math.max(top, 1)) * 100));
    }
    get chart() {
        const months = (this.state.data && this.state.data.months) || [];
        if (!months.length) {
            return null;
        }
        const W = 720, H = 190, PL = 10, PR = 10, PT = 14, PB = 24;
        const top = Math.max(...months.map((m) => Math.max(m.planned, m.actual, m.cum_planned, m.cum_actual || 0)), 1);
        const bw = (W - PL - PR) / months.length;
        const y = (v) => PT + (H - PT - PB) * (1 - v / top);
        const bars = months.map((m, i) => ({
            x: PL + i * bw + bw * 0.12, w: bw * 0.33, yp: y(m.planned), hp: H - PB - y(m.planned),
            ya: y(m.actual), ha: H - PB - y(m.actual), label: m.label, past: m.past,
        }));
        const cp = months.map((m, i) => `${PL + i * bw + bw / 2},${y(m.cum_planned)}`).join(" ");
        const ca = months.filter((m) => m.cum_actual !== null).map((m, i) => `${PL + i * bw + bw / 2},${y(m.cum_actual)}`).join(" ");
        return { W, H, bars, cp, ca, topLabel: this.compact(top), bw };
    }
}

registry.category("actions").add("ebshel_budget_board", BudgetBoard);
