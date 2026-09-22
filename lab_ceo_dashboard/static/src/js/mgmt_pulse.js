/** @odoo-module **/

import { Component, onWillStart, useEffect, useRef, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { formatMonetary } from "@web/views/fields/formatters";

import { shortINR } from "@lab_ceo_dashboard/js/inr";

/**
 * The Management menu's Sales & Cases / Field Force / Money screens.
 *
 * One component, three tabs, one RPC: the three menu entries land on their own
 * tab of the same page, so the reader who came for sales glances at money on
 * the way out — which is the point of a management menu. The pivots these
 * replaced are one click away behind "Full analysis".
 */
export class LabMgmtPulse extends Component {
    static template = "lab_ceo_dashboard.MgmtPulse";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        const params = (this.props.action && this.props.action.params) || {};
        this.state = useState({
            lens: "orders",
            userId: false,
            period: "month",
            data: null,
            loading: true,
            tab: params.section || "sales",
            // Sales & Cases opens on the day: today as it happens, tomorrow as an
            // outlook. The month of bars is one tap away. (client, 2026-09-17)
            salesView: "day",
            day: false,
            dayData: null,
            dayLoading: false,
        });
        onWillStart(() => this.load());
        // The rail runs oldest to newest, so on a phone today and tomorrow start
        // off-screen: bring the chosen day into view whenever the day changes.
        this.rail = useRef("rail");
        useEffect(
            (day) => {
                const el = this.rail.el;
                const on = el && el.querySelector(".o_sd_day_on");
                if (day && on) {
                    el.scrollLeft = on.offsetLeft - el.clientWidth / 2 + on.offsetWidth / 2;
                }
            },
            () => [this.state.dayData && this.state.dayData.day, this.state.salesView, this.state.tab]
        );
    }

    /** The whole page for the current filter - startup and every refilter. */
    async load() {
        this.state.loading = true;
        try {
            const [data] = await Promise.all([
                this.orm.call("lab.mgmt.pulse", "get_pulse", [
                    12,
                    this.state.userId || false,
                    this.state.period || "month",
                ]),
                this.loadDay(),
            ]);
            this.state.data = data;
        } finally {
            this.state.loading = false;
        }
    }

    // ---------------------------------------------------------------- day view
    async loadDay() {
        this.state.dayLoading = true;
        try {
            this.state.dayData = await this.orm.call(
                "lab.mgmt.pulse", "get_sales_day",
                [this.state.day || false, this.state.userId || false]);
        } finally {
            this.state.dayLoading = false;
        }
    }

    setSalesView(view) {
        this.state.salesView = view;
    }

    async selectDay(day) {
        if (!day || (this.state.dayData && this.state.dayData.day === day)) {
            return;
        }
        this.state.day = day;
        await this.loadDay();
    }

    /** Today and tomorrow are the two days asked about most: one tap each. */
    async jumpDay(offset) {
        const today = this.state.dayData && this.state.dayData.today;
        if (!today) {
            return;
        }
        const [y, m, d] = today.split("-").map(Number);
        const when = new Date(Date.UTC(y, m - 1, d + offset));
        await this.selectDay(when.toISOString().slice(0, 10));
    }

    onPickDay(ev) {
        this.selectDay(ev.target.value);
    }

    get tomorrow() {
        const rail = (this.state.dayData && this.state.dayData.rail) || [];
        const last = rail[rail.length - 1];
        return last && last.future ? last.day : null;
    }

    round1(v) {
        return Math.round((v || 0) * 10) / 10;
    }

    railPct(row) {
        const rail = (this.state.dayData && this.state.dayData.rail) || [];
        const peak = Math.max(...rail.map((r) => r.orders || 0), 0);
        return peak ? Math.max(4, Math.round(((row.orders || 0) / peak) * 100)) : 4;
    }

    hourPct(rows, v) {
        const peak = Math.max(...rows.map((r) => Math.max(r.orders || 0, r.ghost || 0)), 0);
        return peak && v ? Math.max(3, Math.round((v / peak) * 100)) : 0;
    }

    weekPct(weeks, v) {
        const peak = Math.max(...weeks.map((w) => w.orders || 0), 0);
        return peak && v ? Math.max(4, Math.round((v / peak) * 100)) : 0;
    }

    /** Where today sits against the outlook range, 0..100, for the gauge. */
    get expectedMarker() {
        const o = this.state.dayData && this.state.dayData.outlook;
        if (!o || o.high === o.low) {
            return 50;
        }
        return Math.round(((o.expected - o.low) / (o.high - o.low)) * 100);
    }

    openDayOrders(name, extra) {
        const d = this.state.dayData;
        this.action.doAction({
            type: "ir.actions.act_window",
            name,
            res_model: "sale.order",
            views: [[false, "list"], [false, "form"]],
            domain: [["state", "=", "sale"]]
                .concat(this.userLeaf)
                .concat(d.bounds || [])
                .concat(extra || []),
        });
    }

    openOrder(id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "sale.order",
            res_id: id,
            views: [[false, "form"]],
        });
    }

    openPartnerOrders(row) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: row.name,
            res_model: "sale.order",
            views: [[false, "list"], [false, "form"]],
            domain: [["state", "=", "sale"], ["partner_id", "=", row.id]],
        });
    }

    openFollowups() {
        const d = this.state.dayData;
        this.action.doAction({
            type: "ir.actions.act_window",
            name: _t("Follow-ups due %s", d.label.toLowerCase()),
            res_model: "sale.order",
            views: [[false, "list"], [false, "form"]],
            domain: [["hold_reason", "!=", false], ["next_followup_date", "<=", d.day]]
                .concat(this.userLeaf),
        });
    }

    // -------------------------------------------------------------- formatting
    money(v) {
        return formatMonetary(v || 0, {
            currencyId: this.state.data && this.state.data.currency_id,
        });
    }

    /** Rupee figures on an axis crowd; lakh-style shortening keeps them legible. */
    short(v) {
        return shortINR(v);
    }

    pctDelta(now, before) {
        if (!before) {
            return null;
        }
        return Math.round(((now - before) / Math.abs(before)) * 100);
    }

    barPct(rows, key, v) {
        const peak = Math.max(...rows.map((r) => r[key] || 0), 0);
        return peak ? Math.max(2, Math.round((v / peak) * 100)) : 0;
    }

    // ------------------------------------------------------------------- tabs
    setTab(tab) {
        this.state.tab = tab;
    }

    /**
     * Cases or money on the same twelve bars.
     *
     * Both numbers were already in the payload; only the count was drawn, and the
     * value hid in a tooltip. A month of many cheap retainers and a month of few
     * aligners are the same height in one lens and opposite in the other, which is
     * the comparison a lab owner is actually making. (client, 2026-08-28)
     */
    setLens(lens) {
        this.state.lens = lens;
    }

    get lens() {
        return this.state.lens || "orders";
    }

    lensValue(row) {
        return this.lens === "value" ? row.value || 0 : row.orders || 0;
    }

    lensLabel(row) {
        return this.lens === "value" ? this.money(row.value) : String(row.orders || 0);
    }

    /**
     * Read the same page for one salesperson.
     *
     * Every figure narrows together - cases, value, billing, the debt and its age -
     * so a manager comparing somebody against their own last month is reading it
     * rather than working it out from a pivot. (client, 2026-08-28)
     */
    async setUser(ev) {
        const value = ev.target.value;
        this.state.userId = value ? Number(value) : false;
        await this.load();
    }

    get users() {
        return (this.state.data && this.state.data.users) || [];
    }

    get userName() {
        const id = this.state.userId;
        const row = this.users.find((u) => u.id === id);
        return row ? row.name : "";
    }

    /** Every drill carries the filter the page is showing. */
    get userLeaf() {
        return this.state.userId ? [["user_id", "=", this.state.userId]] : [];
    }

    /** The current month against the same elapsed days of the last one. */
    get sameDaysGhost() {
        const s = this.state.data && this.state.data.sales;
        if (!s) {
            return 0;
        }
        return this.lens === "value" ? s.prev_month.value : s.prev_month.orders;
    }

    /** A ghost bar's height, on the same scale as the real ones. */
    ghostPct(rows) {
        const peak = Math.max(...rows.map((r) => this.lensValue(r)), 0);
        const ghost = this.sameDaysGhost;
        return peak && ghost ? Math.max(2, Math.round((ghost / peak) * 100)) : 0;
    }

    /** Orders confirmed in one month - the slice a bar actually counts. */
    monthDomain(month) {
        const start = month;
        const [y, m] = month.split("-").map(Number);
        const next = m === 12 ? `${y + 1}-01-01` : `${y}-${String(m + 1).padStart(2, "0")}-01`;
        return [
            ["date_order", ">=", `${start} 00:00:00`],
            ["date_order", "<", `${next} 00:00:00`],
        ];
    }

    /** The month the ranked panels are about. */
    get monthFrom() {
        const s = this.state.data && this.state.data.sales;
        return s ? s.month_from : null;
    }

    /** Move the Money tab's flow window; the open receivable stays as-of-today. */
    async setPeriod(period) {
        if (this.state.period === period) {
            return;
        }
        this.state.period = period;
        await this.load();
    }

    /**
     * The billed card's footnote. Two days into a month "-100%" reads as a
     * collapse when the truth is "nobody has billed yet"; say the truth.
     */
    get billedDelta() {
        const mo = this.state.data && this.state.data.money;
        if (!mo || mo.prev.billed === null) {
            return null;
        }
        if (!mo.billed.amount && mo.period.key === "month") {
            return { nothingYet: true };
        }
        const pct = this.pctDelta(mo.billed.amount, mo.prev.billed);
        return pct === null ? null : { pct, up: mo.billed.amount >= mo.prev.billed };
    }

    get receivedDelta() {
        const mo = this.state.data && this.state.data.money;
        if (!mo || mo.prev.received === null) {
            return null;
        }
        if (!mo.received.amount && mo.period.key === "month") {
            return { nothingYet: true };
        }
        const pct = this.pctDelta(mo.received.amount, mo.prev.received);
        return pct === null ? null : { pct, up: mo.received.amount >= mo.prev.received };
    }

    /** Last 8 weeks of cash-in, as sparkline points on an 80x22 box. */
    get cashSpark() {
        const mo = this.state.data && this.state.data.money;
        const rows = (mo && mo.weekly_cash) || [];
        if (!rows.length) {
            return "";
        }
        const peak = Math.max(...rows.map((r) => Math.max(r.amount, 0)), 1);
        return rows
            .map((r, i) => {
                const x = rows.length > 1 ? (i / (rows.length - 1)) * 78 + 1 : 40;
                const y = 20 - (Math.max(r.amount, 0) / peak) * 18;
                return `${x.toFixed(1)},${y.toFixed(1)}`;
            })
            .join(" ");
    }

    /** A received bar in the paired chart; the opening month is a footnote. */
    pairPct(rows, key, v) {
        const peak = Math.max(
            ...rows.map((r) => Math.max(r.billed || 0, r.received || 0)),
            0
        );
        return peak ? Math.max(2, Math.round((Math.max(v, 0) / peak) * 100)) : 0;
    }

    // ----------------------------------------------------------------- drills
    /** The pivot the tab replaced — still there, one click deep. */
    openFull() {
        const xml = {
            sales: "lab_ceo_dashboard.action_mgmt_sales",
            field: "lab_ceo_dashboard.action_mgmt_field_force",
            money: "lab_ceo_dashboard.action_mgmt_money",
        }[this.state.tab];
        this.action.doAction(xml);
    }

    /**
     * Orders, bounded to the slice the panel claims.
     *
     * Every one of these used to open ALL time - and the month card asked for a
     * search filter named `filter_order_date`, which does not exist in core (it is
     * `order_date`), so it opened unfiltered too. A figure whose drill disagrees
     * with it is worse than no drill. (client, 2026-08-28)
     */
    openOrders(name, extra, month) {
        const domain = [["state", "=", "sale"]]
            .concat(this.userLeaf)
            .concat(extra || []);
        this.action.doAction({
            type: "ir.actions.act_window",
            name,
            res_model: "sale.order",
            views: [[false, "list"], [false, "form"]],
            domain: domain.concat(this.monthDomain(month || this.monthFrom)),
        });
    }

    openMonthOrders() {
        this.openOrders(_t("Cases this month"), []);
    }

    openMonth(row) {
        this.openOrders(_t("Cases — %s", row.label), [], row.month);
    }

    openRoute(r) {
        this.openOrders(r.name, [["team_id", "=", r.id]]);
    }

    openClinic(c) {
        this.openOrders(c.name, [["partner_id", "=", c.id]]);
    }

    /** Appliances rank on the line, so the drill opens lines, not orders. */
    openAppliance(a) {
        const month = this.monthFrom;
        const [y, m] = month.split("-").map(Number);
        const next = m === 12 ? `${y + 1}-01-01` : `${y}-${String(m + 1).padStart(2, "0")}-01`;
        this.action.doAction({
            type: "ir.actions.act_window",
            name: a.name,
            res_model: "sale.order.line",
            views: [[false, "list"], [false, "form"]],
            domain: (this.state.userId
                ? [["order_id.user_id", "=", this.state.userId]] : []).concat([
                ["product_id", "=", a.id],
                ["order_id.state", "=", "sale"],
                ["order_id.date_order", ">=", `${month} 00:00:00`],
                ["order_id.date_order", "<", `${next} 00:00:00`],
            ]),
        });
    }

    openNewClinics() {
        this.action.doAction({
            type: "ir.actions.client",
            tag: "lab_new_clinics",
            name: "New Clinics",
        });
    }

    openPerson(p) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: p.name,
            res_model: "lab.visit",
            views: [[false, "list"], [false, "form"]],
            domain: [["user_id", "=", p.id], ["state", "=", "done"]],
        });
    }

    async openDebtor(d) {
        const action = await this.orm.call(
            "lab.collection.performance",
            "action_open_items_for_partner", [d.id]);
        this.action.doAction(action);
    }

    /** One ageing band of the open receivable. */
    // Both drills go through the countback, so the list always foots to the
    // figure that was clicked. Opening lab.outstanding.report here showed
    // different money than the band itself, because that view reads
    // amount_residual on a ledger that never reconciles. (2026-08-31)
    async openAgeBand(band) {
        const action = await this.orm.call(
            "lab.collection.performance", "action_drill",
            [band.key],
            this.state.userId
                ? { group: "user_id", key: this.state.userId } : {});
        this.action.doAction(action);
    }

    /**
     * The open/overdue cards open the countback's own items. They used to open
     * lab.outstanding.report — the residual view — so the list under the
     * click held different money than the figure clicked. (2026-09-02)
     */
    async openAR(kind) {
        const action = await this.orm.call(
            "lab.collection.performance",
            "action_drill",
            [kind],
            this.state.userId ? { group: "user_id", key: this.state.userId } : {}
        );
        this.action.doAction(action);
    }

    /** One route's open items — the row's Open figure, itemized. */
    async openRouteMoney(r) {
        const action = await this.orm.call(
            "lab.collection.performance",
            "action_drill",
            ["open"],
            { group: "team_id", key: r.id }
        );
        this.action.doAction(action);
    }

    async openQuiet(d) {
        const action = await this.orm.call(
            "lab.collection.performance",
            "action_open_items_for_partner",
            [d.id]
        );
        this.action.doAction(action);
    }
}

registry.category("actions").add("lab_mgmt_pulse", LabMgmtPulse);
