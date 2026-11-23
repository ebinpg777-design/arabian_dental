/** @odoo-module **/

import { Component, onWillStart, proxy, useProps } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const STORE = "lab_technician_work.board";
const FILTERS = [
    { key: "technician_ids", label: _t("Technicians"), icon: "fa-user" },
    { key: "department_ids", label: _t("Departments"), icon: "fa-sitemap" },
    { key: "categ_ids", label: _t("Categories"), icon: "fa-tags" },
    { key: "team_ids", label: _t("Routes"), icon: "fa-road" },
    { key: "partner_ids", label: _t("Clinics"), icon: "fa-hospital-o" },
];
const PRESETS = [
    { key: "today", label: _t("Today") },
    { key: "yesterday", label: _t("Yesterday") },
    { key: "this_week", label: _t("This week") },
    { key: "last_week", label: _t("Last week") },
    { key: "this_month", label: _t("This month") },
    { key: "last_month", label: _t("Last month") },
    { key: "last_30", label: _t("Last 30 days") },
    { key: "this_quarter", label: _t("This quarter") },
    { key: "this_year", label: _t("This year") },
];
const MONEY = ["value", "share"];
const PERCENT = ["rework_rate", "works_pct"];

function store() {
    try {
        return JSON.parse(window.localStorage.getItem(STORE) || "{}");
    } catch {
        return {};
    }
}

function save(values) {
    try {
        window.localStorage.setItem(STORE, JSON.stringify(values));
    } catch {
        // private window or storage blocked: the screen works without it
    }
}

/** [from, to] (ISO dates) of a period preset, in the browser's own days. */
function presetDates(key) {
    const { DateTime } = luxon;
    const today = DateTime.local().startOf("day");
    const iso = (d) => d.toISODate();
    switch (key) {
        case "today":
            return [iso(today), iso(today)];
        case "yesterday": {
            const day = today.minus({ days: 1 });
            return [iso(day), iso(day)];
        }
        case "this_week":
            return [iso(today.startOf("week")), iso(today)];
        case "last_week": {
            const start = today.startOf("week").minus({ weeks: 1 });
            return [iso(start), iso(start.endOf("week"))];
        }
        case "last_month": {
            const start = today.startOf("month").minus({ months: 1 });
            return [iso(start), iso(start.endOf("month"))];
        }
        case "last_30":
            return [iso(today.minus({ days: 29 })), iso(today)];
        case "this_quarter":
            return [iso(today.startOf("quarter")), iso(today)];
        case "this_year":
            return [iso(today.startOf("year")), iso(today)];
        default:
            return [iso(today.startOf("month")), iso(today)];
    }
}

/**
 * Technician Work, on one screen (client, 2026-11-23: "instead of a pivot, an
 * attractive widget; one menu; filters in the widget; more fields can be added; print
 * or export it; more innovative features").
 *
 * The month's figures with the change on the period before, a few sentences worth
 * reading, a leaderboard whose rows open into their products (or departments,
 * clinics...), a heatmap of who worked when, columns to add or take away, rows by
 * technician or by any other dimension - and the same board printed or exported.
 * Every figure comes from ``sale.technician.work.get_board``.
 */
export class TechnicianBoard extends Component {
    static template = "lab_technician_work.TechnicianBoard";
    props = useProps();

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.filters = FILTERS;
        this.presets = PRESETS;
        const kept = store();
        const preset = kept.preset && kept.preset !== "custom" ? kept.preset : "this_month";
        const [dateFrom, dateTo] = presetDates(preset);
        const context = (this.props.action && this.props.action.context) || {};
        this.state = proxy({
            loading: true,
            busy: false,
            data: null,
            error: null,
            preset,
            options: {
                date_from: dateFrom,
                date_to: dateTo,
                date_field: kept.date_field || "date",
                group_by: kept.group_by || "technician_id",
                breakdown: kept.breakdown || "product_id",
                rework: "all",
                cases: "all",
                technician_ids: context.technician_ids || [],
                department_ids: [],
                categ_ids: [],
                team_ids: [],
                partner_ids: [],
            },
            columns: kept.columns || null,
            sort: { key: "works", desc: true },
            view: kept.view || "board",
            panel: null,
            panelQuery: "",
            query: "",
            open: {},
            printBreakdown: false,
        });
        onWillStart(() => this.load());
    }

    // ------------------------------------------------------------------ loading
    async load() {
        this.state.busy = true;
        this.state.error = null;
        try {
            const data = await this.orm.call("sale.technician.work", "get_board", [this.state.options]);
            this.state.data = data;
            if (!this.state.columns) {
                this.state.columns = data.default_columns;
            }
            this.state.open = {};
        } catch (error) {
            this.state.error = (error && error.data && error.data.message) || String(error);
        } finally {
            this.state.loading = false;
            this.state.busy = false;
        }
        this.remember();
    }

    remember() {
        save({
            preset: this.state.preset,
            date_field: this.state.options.date_field,
            group_by: this.state.options.group_by,
            breakdown: this.state.options.breakdown,
            columns: this.state.columns,
            view: this.state.view,
        });
    }

    // ------------------------------------------------------------------ the period
    pickPreset(key) {
        const [dateFrom, dateTo] = presetDates(key);
        this.state.preset = key;
        this.state.options.date_from = dateFrom;
        this.state.options.date_to = dateTo;
        this.state.panel = null;
        this.load();
    }

    pickDate(which, value) {
        if (!value) {
            return;
        }
        this.state.preset = "custom";
        this.state.options[which] = value;
        if (this.state.options.date_from > this.state.options.date_to) {
            this.state.options[which === "date_from" ? "date_to" : "date_from"] = value;
        }
        this.load();
    }

    /** The same length of time, before or after. */
    shift(direction) {
        const { DateTime } = luxon;
        const from = DateTime.fromISO(this.state.options.date_from);
        const to = DateTime.fromISO(this.state.options.date_to);
        const wholeMonths = from.day === 1 && to.toISODate() === to.endOf("month").toISODate();
        let newFrom, newTo;
        if (wholeMonths) {
            const months = Math.round(to.diff(from, "months").months) || 1;
            newFrom = from.plus({ months: direction * months });
            newTo = newFrom.plus({ months }).minus({ days: 1 });
        } else {
            const days = Math.round(to.diff(from, "days").days) + 1;
            newFrom = from.plus({ days: direction * days });
            newTo = to.plus({ days: direction * days });
        }
        this.state.preset = "custom";
        this.state.options.date_from = newFrom.toISODate();
        this.state.options.date_to = newTo.toISODate();
        this.load();
    }

    get presetLabel() {
        const preset = PRESETS.find((p) => p.key === this.state.preset);
        return preset ? preset.label : _t("Custom");
    }

    // ------------------------------------------------------------------ filters
    setOption(key, value) {
        this.state.options[key] = value;
        this.load();
    }

    togglePanel(name) {
        this.state.panel = this.state.panel === name ? null : name;
        this.state.panelQuery = "";
    }

    choices(key) {
        const all = (this.state.data && this.state.data.choices[key]) || [];
        const query = this.state.panelQuery.trim().toLowerCase();
        return query ? all.filter((c) => c.name.toLowerCase().includes(query)) : all;
    }

    isChosen(key, id) {
        return this.state.options[key].includes(id);
    }

    toggleChoice(key, id) {
        const values = this.state.options[key];
        this.state.options[key] = values.includes(id) ? values.filter((v) => v !== id) : [...values, id];
        this.load();
    }

    clearFilter(key) {
        this.state.options[key] = [];
        this.load();
    }

    chosenNames(key) {
        const ids = this.state.options[key];
        const all = (this.state.data && this.state.data.choices[key]) || [];
        return all.filter((c) => ids.includes(c.id)).map((c) => c.name);
    }

    get activeFilters() {
        return FILTERS.filter((f) => this.state.options[f.key].length);
    }

    clearAll() {
        for (const f of FILTERS) {
            this.state.options[f.key] = [];
        }
        this.state.options.rework = "all";
        this.state.options.cases = "all";
        this.load();
    }

    // ------------------------------------------------------------------ columns
    toggleColumn(key) {
        const all = this.state.data.columns.map((c) => c.key);
        const chosen = this.state.columns.includes(key)
            ? this.state.columns.filter((k) => k !== key)
            : [...this.state.columns, key];
        this.state.columns = all.filter((k) => chosen.includes(k));
        this.remember();
    }

    get shownColumns() {
        const labels = Object.fromEntries(this.state.data.columns.map((c) => [c.key, c.label]));
        return (this.state.columns || []).filter((k) => labels[k]).map((k) => ({ key: k, label: labels[k] }));
    }

    sortBy(key) {
        if (this.state.sort.key === key) {
            this.state.sort.desc = !this.state.sort.desc;
        } else {
            this.state.sort.key = key;
            this.state.sort.desc = key !== "name";
        }
    }

    get rows() {
        if (!this.state.data) {
            return [];
        }
        const query = this.state.query.trim().toLowerCase();
        const rows = this.state.data.rows.filter((r) => !query || r.name.toLowerCase().includes(query));
        const { key, desc } = this.state.sort;
        return [...rows].sort((a, b) => {
            const x = a[key];
            const y = b[key];
            const order = typeof x === "string" ? x.localeCompare(y) : (x || 0) - (y || 0);
            return desc ? -order : order;
        });
    }

    get topWorks() {
        return Math.max(1, ...((this.state.data && this.state.data.rows.map((r) => r.works)) || [0]));
    }

    // ------------------------------------------------------------------ rows
    async toggleRow(row) {
        const key = String(row.key);
        if (this.state.open[key]) {
            this.state.open = Object.fromEntries(Object.entries(this.state.open).filter(([k]) => k !== key));
            return;
        }
        this.state.open[key] = { loading: true, rows: [] };
        const result = await this.orm.call("sale.technician.work", "get_breakdown", [
            this.state.options,
            row.key,
            this.state.options.breakdown,
        ]);
        this.state.open[key] = { loading: false, ...result };
    }

    setBreakdown(value) {
        this.state.options.breakdown = value;
        this.state.open = {};
        this.remember();
    }

    async openLines(key) {
        const action = await this.orm.call("sale.technician.work", "action_open_lines", [
            this.state.options,
            key === undefined ? null : key,
        ]);
        this.action.doAction(action);
    }

    openRecord(row) {
        const model = {
            technician_id: "res.partner",
            partner_id: "res.partner",
            department_id: "hr.department",
            product_id: "product.product",
            categ_id: "product.category",
            team_id: "crm.team",
        }[this.state.options.group_by];
        if (!row.key || !model) {
            return;
        }
        this.action.doAction({ type: "ir.actions.act_window", res_model: model, res_id: row.key, views: [[false, "form"]] });
    }

    // ------------------------------------------------------------------ print, export
    async print() {
        this.state.panel = null;
        const action = await this.orm.call("sale.technician.work", "action_print", [
            this.state.options,
            this.state.columns,
            this.state.printBreakdown,
        ]);
        this.action.doAction(action);
    }

    async exportExcel() {
        this.state.panel = null;
        const action = await this.orm.call("sale.technician.work", "action_export", [
            this.state.options,
            this.state.columns,
        ]);
        this.action.doAction(action);
    }

    setView(view) {
        this.state.view = view;
        this.remember();
    }

    // ------------------------------------------------------------------ small setters
    setPrintBreakdown(value) {
        this.state.printBreakdown = value;
    }

    setPanelQuery(value) {
        this.state.panelQuery = value;
    }

    setQuery(value) {
        this.state.query = value;
    }

    closePanel() {
        this.state.panel = null;
    }

    clickTile(tile) {
        if (tile.key === "works" || tile.key === "cases") {
            this.openLines();
        }
    }

    openOf(row) {
        return this.state.open[String(row.key)];
    }

    get breakdownLabel() {
        const dim = this.state.data && this.state.data.dimensions.find((d) => d.key === this.state.options.breakdown);
        return dim ? dim.label : "";
    }

    get otherDimensions() {
        return this.state.data.dimensions.filter((d) => d.key !== this.state.options.group_by);
    }

    barWidth(row) {
        return `width: ${Math.max(2, (100 * row.works) / this.topWorks).toFixed(1)}%`;
    }

    breakWidth(item) {
        return `width: ${Math.max(2, item.works_pct || 0).toFixed(1)}%`;
    }

    totalCell(key) {
        const totals = this.state.data.totals;
        return key in totals ? this.cell(key, totals[key]) : "";
    }

    shortLabel(label) {
        const parts = String(label).split(" ");
        return parts.length > 1 ? parts[parts.length - 2] : label;
    }

    heatTitle(row, index, value) {
        return `${row.name} · ${this.state.data.period.point_labels[index]}: ${value} ${_t("works")}`;
    }

    // ------------------------------------------------------------------ showing
    number(value, digits = 0) {
        return new Intl.NumberFormat(undefined, { maximumFractionDigits: digits, minimumFractionDigits: 0 }).format(
            value || 0
        );
    }

    money(value) {
        const currency = (this.state.data && this.state.data.currency) || { symbol: "", position: "before", digits: 2 };
        const amount = new Intl.NumberFormat(undefined, {
            maximumFractionDigits: 0,
            minimumFractionDigits: 0,
        }).format(value || 0);
        return currency.position === "after" ? `${amount} ${currency.symbol}` : `${currency.symbol} ${amount}`;
    }

    cell(key, value) {
        if (MONEY.includes(key)) {
            return this.money(value);
        }
        if (PERCENT.includes(key)) {
            return `${this.number(value, 1)}%`;
        }
        if (key === "trend") {
            return value > 0 ? `+${this.number(value)}` : this.number(value);
        }
        if (key === "per_day" || key.endsWith("_share")) {
            return this.number(value, 1);
        }
        return this.number(value, key === "quantity" ? 2 : 0);
    }

    trendClass(value) {
        return value > 0 ? "o_tw_up" : value < 0 ? "o_tw_down" : "o_tw_flat";
    }

    /** The change on the previous period, as a percentage word. */
    delta(key) {
        const data = this.state.data;
        const now = data.totals[key] || 0;
        const before = data.previous[key] || 0;
        if (!before) {
            return now ? { text: _t("new"), cls: "o_tw_up" } : { text: "", cls: "o_tw_flat" };
        }
        const pct = Math.round((100 * (now - before)) / before);
        return {
            text: `${pct > 0 ? "▲" : pct < 0 ? "▼" : "="} ${Math.abs(pct)}%`,
            cls: pct > 0 ? "o_tw_up" : pct < 0 ? "o_tw_down" : "o_tw_flat",
        };
    }

    get tiles() {
        const t = this.state.data.totals;
        return [
            { key: "people", label: _t("Technicians"), value: this.number(t.people), icon: "fa-users" },
            { key: "cases", label: _t("Cases"), value: this.number(t.cases), icon: "fa-folder-open-o" },
            { key: "works", label: _t("Works"), value: this.number(t.works), icon: "fa-wrench", main: true },
            { key: "quantity", label: _t("Quantity"), value: this.number(t.quantity, 2), icon: "fa-cubes" },
            { key: "teeth", label: _t("Teeth"), value: this.number(t.teeth), icon: "fa-th" },
            { key: "share", label: _t("Value Share"), value: this.money(t.share), icon: "fa-inr" },
        ];
    }

    get heatRows() {
        return this.rows.slice(0, 60);
    }

    get heatMax() {
        return Math.max(1, ...this.heatRows.flatMap((r) => r.series));
    }

    heatStyle(value) {
        if (!value) {
            return "";
        }
        const level = 0.15 + 0.85 * (value / this.heatMax);
        return `background-color: rgba(1, 126, 132, ${level.toFixed(2)})`;
    }

    medal(rank) {
        return rank <= 3 && this.state.sort.key === "works" && this.state.sort.desc && !this.state.query
            ? `o_tw_medal o_tw_medal_${rank}`
            : "o_tw_rank";
    }
}

registry.category("actions").add("lab_technician_work_board", TechnicianBoard);
