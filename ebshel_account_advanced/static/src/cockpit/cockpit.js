/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { money, compact } from "../common/utils";

/** The accounting app's home: profit and loss as a waterfall, the balance sheet as
 *  two stacks, twelve months of trend, ratios, and the work waiting on the team. */
export class FinanceCockpit extends Component {
    static template = "ebshel_account_advanced.FinanceCockpit";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ period: "month", data: null, loading: true, hover: null });
        this.money = (v) => money(v, this.state.data && this.state.data.currency);
        this.compact = (v) => compact(v, this.state.data && this.state.data.currency);
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call("ebshel.finance.cockpit", "get_data", [this.state.period]);
        } finally {
            this.state.loading = false;
        }
    }
    setPeriod(p) {
        this.state.period = p;
        this.load();
    }
    openReport(key, extra) {
        const d = this.state.data;
        const options = { date: { preset: "custom", from: d.from, to: d.to }, ...(extra || {}) };
        if (key === "balance_sheet" || key === "aged_receivable" || key === "aged_payable") {
            options.date = { preset: "custom", from: d.to, to: d.to };
        }
        this.action.doAction({ type: "ir.actions.client", tag: "ebshel_fin_report", params: { report_key: key, options } });
    }
    openAccount(item) {
        const d = this.state.data;
        this.action.doAction({ type: "ir.actions.act_window", name: item.name, res_model: "account.move.line",
                               views: [[false, "list"], [false, "pivot"]],
                               domain: [["account_id", "=", item.id], ["parent_state", "=", "posted"], ["date", ">=", d.from], ["date", "<=", d.to]] });
    }
    openMonth(m) {
        this.action.doAction({ type: "ir.actions.client", tag: "ebshel_fin_report",
                               params: { report_key: "profit_loss", options: { date: { preset: "custom", from: m.from, to: m.to } } } });
    }
    openTodo(t) {
        const a = t.action;
        if (a.tag) {
            this.action.doAction({ type: "ir.actions.client", tag: a.tag, params: a.params || {} });
        } else {
            this.action.doAction({ type: "ir.actions.act_window", name: t.label, res_model: a.model, views: [[false, "list"], [false, "form"]], domain: a.domain });
        }
    }
    go(tag) {
        this.action.doAction({ type: "ir.actions.client", tag });
    }

    get waterfall() {
        const steps = this.state.data.waterfall.filter((s) => s.kind === "total" || Math.abs(s.value) > 0.005);
        const W = 640, H = 230, PT = 22, PB = 40;
        let running = 0;
        const bars = steps.map((s) => {
            let a, b;
            if (s.kind === "total") {
                a = 0; b = s.value; running = s.value;
            } else {
                a = running; b = running + s.value; running = b;
            }
            return { ...s, a, b };
        });
        const hi = Math.max(0, ...bars.map((x) => Math.max(x.a, x.b)));
        const lo = Math.min(0, ...bars.map((x) => Math.min(x.a, x.b)));
        const span = Math.max(hi - lo, 1);
        const y = (v) => PT + (H - PT - PB) * (1 - (v - lo) / span);
        const bw = W / bars.length;
        return {
            W, H, zero: y(0),
            bars: bars.map((x, i) => ({
                ...x, x: i * bw + bw * 0.16, w: bw * 0.68, y: y(Math.max(x.a, x.b)), h: Math.max(1.5, Math.abs(y(x.a) - y(x.b))),
                cls: x.kind === "total" ? (x.value >= 0 ? "total" : "total neg") : (x.value >= 0 ? "up" : "down"),
                lx: i * bw + bw / 2, ly: y(Math.max(x.a, x.b)) - 5,
            })),
        };
    }
    get stacks() {
        const d = this.state.data;
        const assets = d.assets.filter((a) => Math.abs(a.value) > 0.5);
        // the report already shows liabilities and equity as positive claims
        const claims = d.claims.filter((a) => Math.abs(a.value) > 0.5);
        const ta = assets.reduce((t, a) => t + Math.max(a.value, 0), 0);
        const tc = claims.reduce((t, a) => t + Math.max(a.value, 0), 0);
        const top = Math.max(ta, tc, 1);
        const colors = ["#1f3a5f", "#3a6ea5", "#6c9bd2", "#9dbde2", "#c8d9ee", "#e3ecf7"];
        const warm = ["#b45309", "#d97706", "#f2a65a", "#f8cf9e", "#067647"];
        const seg = (list, pal) => list.filter((x) => x.value > 0).map((x, i) => ({ ...x, pct: (x.value / top) * 100, color: x.key === "EQUITY" ? "#067647" : pal[i % pal.length] }));
        return { assets: seg(assets, colors), claims: seg(claims, warm), ta, tc };
    }
    get trend() {
        const m = this.state.data.months;
        const W = 760, H = 200, PT = 16, PB = 22;
        const top = Math.max(...m.map((x) => Math.max(x.revenue, x.expenses)), 1);
        const pmin = Math.min(0, ...m.map((x) => x.profit));
        const y = (v) => PT + (H - PT - PB) * (1 - (v - pmin) / (top - pmin || 1));
        const bw = W / m.length;
        const cashTop = Math.max(...m.map((x) => Math.abs(x.cash)), 1);
        const yc = (v) => PT + (H - PT - PB) * (1 - v / cashTop) * 0.98;
        return {
            W, H, zero: y(0),
            bars: m.map((x, i) => ({ ...x, x: i * bw + bw * 0.12, w: bw * 0.36, yr: y(x.revenue), hr: Math.max(0, y(0) - y(x.revenue)),
                                     ye: y(x.expenses), he: Math.max(0, y(0) - y(x.expenses)), lx: i * bw + bw / 2 })),
            profit: m.map((x, i) => `${i * bw + bw / 2},${y(x.profit)}`).join(" "),
            cash: m.map((x, i) => `${i * bw + bw / 2},${yc(x.cash)}`).join(" "),
        };
    }
    barWidth(v, list) {
        const top = Math.max(...list.map((x) => x.value), 1);
        return Math.round((v / top) * 100);
    }
    growthClass(k) {
        if (k.growth === null || k.growth === undefined) {
            return "";
        }
        return k.growth >= 0 ? "text-success" : "text-danger";
    }
}

registry.category("actions").add("ebshel_finance_cockpit", FinanceCockpit);
