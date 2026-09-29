/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { money, compact, today } from "../common/utils";

/** Thirteen weeks of cash, with sliders for how customers will really behave. */
export class CashForecast extends Component {
    static template = "ebshel_account_advanced.CashForecast";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({ loading: true, data: null, busy: false, params: { weeks: null, collection_rate: null, delay_days: null, per_partner: true, include_draft: true },
                                week: null, adding: false, item: {} });
        this.money = (v) => money(v, this.state.data && this.state.data.currency);
        this.compact = (v) => compact(v, this.state.data && this.state.data.currency);
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        try {
            const p = {};
            for (const [k, v] of Object.entries(this.state.params)) {
                if (v !== null && v !== "") {
                    p[k] = v;
                }
            }
            this.state.data = await this.orm.call("ebshel.cash.forecast", "get_data", [p]);
            this.state.params.weeks = this.state.data.params.weeks;
            this.state.params.collection_rate = this.state.data.params.collection_rate;
        } finally {
            this.state.loading = false;
        }
    }
    set(key, value) {
        this.state.params[key] = value;
        this.load();
    }
    get weekDetail() {
        if (this.state.week === null || !this.state.data) {
            return [];
        }
        return this.state.data.weeks[this.state.week].detail;
    }
    keyLabel(key) {
        return { in_ar: "customer invoice", in_items: "planned in", in_draft: "draft invoice", out_ap: "vendor bill", out_items: "planned out", out_draft: "draft bill" }[key] || key;
    }
    openDetail(d) {
        if (d.model === "account.move.line") {
            this.action.doAction({ type: "ir.actions.act_window", res_model: "account.move.line", views: [[false, "list"], [false, "form"]], domain: [["id", "=", d.id]], name: d.name });
        } else if (d.model === "account.move") {
            this.action.doAction({ type: "ir.actions.act_window", res_model: "account.move", res_id: d.id, views: [[false, "form"]] });
        }
    }
    get chart() {
        const weeks = (this.state.data && this.state.data.weeks) || [];
        const hist = (this.state.data && this.state.data.history) || [];
        if (!weeks.length) {
            return null;
        }
        const W = 760, H = 200, PL = 10, PR = 10, PT = 14, PB = 24;
        const all = [...hist.map((h) => h.closing), ...weeks.map((w) => w.closing), this.state.data.kpis.opening, 0];
        const top = Math.max(...all), bottom = Math.min(...all);
        const span = Math.max(top - bottom, 1);
        const n = hist.length + weeks.length;
        const step = (W - PL - PR) / Math.max(n - 1, 1);
        const y = (v) => PT + (H - PT - PB) * (1 - (v - bottom) / span);
        const hp = hist.map((h, i) => `${PL + i * step},${y(h.closing)}`).join(" ");
        const fp = weeks.map((w, i) => `${PL + (hist.length + i) * step},${y(w.closing)}`).join(" ");
        const maxFlow = Math.max(...weeks.map((w) => Math.max(w.in, w.out)), 1);
        const bars = weeks.map((w, i) => {
            const x = PL + (hist.length + i) * step;
            const hIn = ((H - PT - PB) / 3) * (w.in / maxFlow), hOut = ((H - PT - PB) / 3) * (w.out / maxFlow);
            return { x, hIn, hOut, yIn: H - PB - hIn, yOut: H - PB - hOut, label: w.label, low: w.closing < 0, index: w.index };
        });
        return { W, H, hp, fp, bars, zero: y(0), step, topLabel: this.compact(top), bottomLabel: this.compact(bottom), split: PL + Math.max(hist.length - 1, 0) * step };
    }
    startAdd() {
        this.state.item = { name: "", kind: "out", amount: 0, date: today(), frequency: "monthly" };
        this.state.adding = true;
    }
    async saveItem() {
        const it = this.state.item;
        if (!it.name || !it.amount) {
            this.notification.add("A name and an amount, please.", { type: "warning" });
            return;
        }
        await this.orm.call("ebshel.cash.forecast", "save_item", [{ name: it.name, kind: it.kind, amount: Number(it.amount), date: it.date, frequency: it.frequency }]);
        this.state.adding = false;
        await this.load();
    }
    async deleteItem(item) {
        await this.orm.call("ebshel.cash.forecast", "delete_item", [item.id]);
        await this.load();
    }
}

registry.category("actions").add("ebshel_cash_forecast", CashForecast);
