/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onWillStart, useEffect, useRef, useState } from "@odoo/owl";
import { DateTimeInput } from "@web/core/datetime/datetime_input";
import { deserializeDate, serializeDate } from "@web/core/l10n/dates";

/**
 * Collections: what was invoiced against what has actually come in.
 *
 * Sales are read from POSTED INVOICES (net of credit notes), not from orders - an
 * order is a promise, an invoice is a claim, and only a claim can be collected
 * against. Both windows are editable, so the same screen answers "how did July
 * close?" a fortnight later.
 *
 * Every figure comes from one server call; a route's breakdown (ageing, worst
 * payers) is fetched only when that route is opened, so the first paint stays fast.
 */
export class CollectionDashboard extends Component {
    static template = "lab_collections.CollectionDashboard";
    static components = { DateTimeInput };
    static props = ["*"];

    // The client asked for dd/mm/yyyy: a native <input type="date"> follows the
    // BROWSER's locale and cannot be told otherwise, so the period pickers are
    // Odoo's own DateTimeInput with the format pinned. (client, 2026-08-21)
    get dateFormat() {
        return "dd/MM/yyyy";
    }

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.loadToken = 0;
        this.cancelTarget = false;
        this.targetInput = useRef("targetInput");
        // A freshly inserted <input> does not honour the autofocus attribute -
        // browsers apply that only on page load - so focus it when it appears.
        useEffect(
            (el) => {
                if (el) {
                    el.focus();
                    el.select();
                }
            },
            () => [this.targetInput.el]
        );
        this.state = useState({
            data: null,
            loading: true,
            tab: "route",
            options: {},
            expanded: null,
            details: {},
            sort: { field: "pending", asc: false },
            query: "",
            editingTarget: null,
            editingSales: null,
            boards: null,
            people: null,
            showBoards: true,
        });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        const token = ++this.loadToken;
        try {
            const data = await this.orm.call(
                "lab.collection.performance", "dashboard_data", [], {
                    options: this.state.options,
                    with_trend: false,
                    // Asked for in the SAME call: the server has already done the
                    // expensive part by the time it builds these, where a second
                    // RPC made it redo the whole countback. (2026-08-21)
                    with_extras: true,
                });
            this.state.data = data;
            this.state.boards = data.boards || null;
            this.state.people = data.people || null;
            // A reload invalidates whatever breakdowns were open: the periods moved.
            this.state.details = {};
        } finally {
            this.state.loading = false;
        }
        // Sparklines are the slowest figure on the screen and the least urgent, so
        // the table paints without them and they arrive a moment later. Not awaited:
        // `load()` must resolve as soon as the numbers are up. The token guards
        // against a stale answer landing after the user has moved the period on.
        // Only the sparklines are still deferred: they are the slowest figure and
        // the least urgent, so the table paints without them.
        this.loadTrend(token);
    }

    async loadTrend(token) {
        let trend;
        try {
            trend = await this.orm.call(
                "lab.collection.performance", "trend_data", [], {
                    options: this.state.options,
                });
        } catch {
            return; // a missing sparkline must never take the figures down with it
        }
        if (token !== this.loadToken || !this.state.data) {
            return;
        }
        for (const row of this.state.data.by_route) {
            row.trend = trend[row.key] || [];
        }
    }

    // ------------------------------------------------------------------ periods
    dateValue(key) {
        const iso = this.state.data && this.state.data.dates[key];
        return iso ? deserializeDate(iso) : false;
    }

    onDateChange(key, value) {
        if (!value || !value.isValid) {
            return;
        }
        this.state.options = { ...this.state.options, [key]: serializeDate(value) };
        this.load();
    }

    /** Jump the whole screen a month back or forward, both windows together. */
    shiftPeriod(months) {
        const dates = this.state.data && this.state.data.dates;
        if (!dates) {
            return;
        }
        const options = { ...this.state.options };
        for (const key of ["sales_from", "sales_to", "pay_from", "pay_to"]) {
            const shifted = deserializeDate(dates[key]).plus({ months });
            // Keep month ends on month ends: 31 Jul + 1 month must be 31 Aug, and
            // luxon would otherwise land on the 30th and stay there for good.
            const isMonthEnd = deserializeDate(dates[key]).endOf("month").hasSame(
                deserializeDate(dates[key]), "day");
            options[key] = serializeDate(isMonthEnd ? shifted.endOf("month") : shifted);
        }
        this.state.options = options;
        this.load();
    }

    resetPeriod() {
        this.state.options = {};
        this.load();
    }

    // ------------------------------------------------------------------ table
    setTab(tab) {
        this.state.tab = tab;
        this.state.expanded = null;
    }

    get allRows() {
        if (!this.state.data) {
            return [];
        }
        return this.state.tab === "route" ? this.state.data.by_route : this.state.data.by_user;
    }

    get rows() {
        const query = this.state.query.trim().toLowerCase();
        const rows = query
            ? this.allRows.filter((r) => r.label.toLowerCase().includes(query))
            : this.allRows.slice();
        const { field, asc } = this.state.sort;
        rows.sort((a, b) => {
            const x = a[field];
            const y = b[field];
            const cmp = typeof x === "string" ? x.localeCompare(y) : (x || 0) - (y || 0);
            return asc ? cmp : -cmp;
        });
        return rows;
    }

    sortBy(field) {
        const sort = this.state.sort;
        // A second click on the same column flips it; a new column starts with the
        // reading that is useful first - biggest number, or A-Z for a name.
        this.state.sort = field === sort.field
            ? { field, asc: !sort.asc }
            : { field, asc: field === "label" };
    }

    sortIcon(field) {
        if (this.state.sort.field !== field) {
            return "fa fa-sort o_coll_sort_idle";
        }
        return this.state.sort.asc ? "fa fa-sort-asc" : "fa fa-sort-desc";
    }

    // ------------------------------------------------------------------ breakdown
    async toggleRow(row) {
        if (this.state.expanded === row.key) {
            this.state.expanded = null;
            return;
        }
        this.state.expanded = row.key;
        if (!(row.key in this.state.details)) {
            this.state.details[row.key] = null; // spinner while it loads
            try {
                this.state.details[row.key] = await this.orm.call(
                    "lab.collection.performance", "row_detail", [], {
                        group: this.groupField,
                        key: row.key,
                        options: this.state.options,
                    });
            } catch (error) {
                // Leaving the key at null would spin for ever; forget it so the
                // next click tries again, and let the error surface as usual.
                delete this.state.details[row.key];
                this.state.expanded = null;
                throw error;
            }
        }
    }

    get groupField() {
        return this.state.tab === "route" ? "team_id" : "user_id";
    }

    detailFor(key) {
        return this.state.details[key] || undefined;
    }

    // ------------------------------------------------------------------ drill-through
    /**
     * Open the documents behind a figure: the invoices that make up Sales, the
     * receipts that make up Collected, or what is left open on the clinic's account.
     */
    async drill(kind, row, ev, group) {
        if (ev) {
            ev.stopPropagation(); // never also toggle the row open
        }
        const action = await this.orm.call(
            "lab.collection.performance", "action_drill", [], {
                // The leaderboards sit ABOVE the tab switcher and show on both tabs,
                // so they must name their own grouping: following the active tab
                // asked for a salesperson id under `team_id` and opened an empty
                // list on the Route tab. (2026-08-21)
                kind,
                group: group || this.groupField,
                key: row ? row.key : null,
                options: this.state.options,
            });
        return this.action.doAction(action);
    }

    openPartner(partner, ev) {
        if (ev) {
            ev.stopPropagation();
        }
        return this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "res.partner",
            res_id: partner.id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    // ------------------------------------------------------------------ display
    money(value) {
        const symbol = (this.state.data && this.state.data.currency) || "";
        return `${symbol} ${(value || 0).toLocaleString("en-IN", {
            minimumFractionDigits: 2, maximumFractionDigits: 2,
        })}`;
    }

    /** Short money for tight spots: 12.4L, 3.2Cr - the way the office says it. */
    shortMoney(value) {
        const symbol = (this.state.data && this.state.data.currency) || "";
        const abs = Math.abs(value || 0);
        if (abs >= 10000000) { return `${symbol} ${(value / 10000000).toFixed(2)}Cr`; }
        if (abs >= 100000) { return `${symbol} ${(value / 100000).toFixed(2)}L`; }
        return `${symbol} ${Math.round(value || 0).toLocaleString("en-IN")}`;
    }

    /** Collection bar: full at 100 %, and it does not run past the track above it. */
    barWidth(percent) {
        return Math.max(Math.min(percent || 0, 100), 0);
    }

    /**
     * Colour is read against the TARGET, not against a fixed 90/60: a route asked
     * for 60% and delivering 65% is doing well, and used to be painted amber.
     * Within a quarter of the way short is amber, further than that is red.
     */
    barClass(percent, target) {
        const goal = target || 0;
        if (!goal) {
            if (percent >= 90) { return "o_coll_bar_good"; }
            return percent >= 60 ? "o_coll_bar_mid" : "o_coll_bar_low";
        }
        if (percent >= goal) { return "o_coll_bar_good"; }
        return percent >= goal * 0.75 ? "o_coll_bar_mid" : "o_coll_bar_low";
    }

    /**
     * Pill styling for the "on target / short by" flag.
     *
     * NOT barClass(): those classes paint a bar's FILL, so reusing them rendered the
     * flag as a solid block of colour behind the text. This is a tinted pill.
     */
    flagClass(percent, target) {
        const bar = this.barClass(percent, target);
        return "o_coll_flag " + bar.replace("o_coll_bar_", "o_coll_flag_");
    }

    /** Where the target sits on the bar's track, as a % of its width. */
    targetMark(target) {
        return Math.max(Math.min(target || 0, 100), 0);
    }

    /** "4.2 points short" / "on target" — the sentence under the headline. */
    targetGap(percent, target) {
        if (!target) { return ""; }
        const gap = Math.round((percent - target) * 10) / 10;
        if (gap >= 0) { return `on target (+${gap})`; }
        return `${Math.abs(gap)} short of ${target}%`;
    }

    // ------------------------------------------------------------------ leaderboards
    async loadExtras(token) {
        let extras;
        try {
            // One call: the boards and the per-person analysis share a pass over the
            // same rows, so asking for them separately did the work twice.
            extras = await this.orm.call("lab.collection.performance", "extras", [], {
                options: this.state.options,
            });
        } catch {
            return;   // the leaderboards are a garnish; never take the figures down
        }
        if (token !== this.loadToken) {
            return;
        }
        this.state.boards = extras.boards;
        this.state.people = extras.people;
    }

    /** The per-person analysis row for a salesperson, once `extras` has landed. */
    personFor(key) {
        return (this.state.people || []).find((p) => p.key === key);
    }

    // Invoices and receipts are accounting documents; a field executive sees the
    // figures on this screen but cannot open the paperwork behind them, so those
    // two cards must not look clickable to them. (client, 2026-08-25)
    get canDrillDocuments() {
        return !!(this.state.data && this.state.data.can_drill_documents);
    }

    get canRank() {
        return !!(this.state.data && this.state.data.can_rank);
    }

    get canEditTargets() {
        return !!(this.state.data && this.state.data.can_edit_targets);
    }

    /** Take a login off the boards, or put it back. No figure moves. */
    async toggleRanking(row, ev) {
        if (ev) {
            ev.stopPropagation();   // never also expand the row
        }
        const value = !row.excluded;
        await this.orm.call("lab.collection.performance", "set_user_setting", [], {
            user_id: row.key,
            values: { lab_exclude_from_ranking: value },
        });
        row.excluded = value;
        // The boards and the ranks are computed on the server; ask again rather than
        // re-sorting three lists here and risking a different answer.
        this.loadExtras(this.loadToken);
    }

    medal(index) {
        return ["o_coll_medal_1", "o_coll_medal_2", "o_coll_medal_3"][index] || "o_coll_medal_n";
    }

    get boardRows() {
        const boards = this.state.boards;
        if (!boards) {
            return [];
        }
        return [
            { key: "top_sales", title: "Top salespeople", sub: "by what they invoiced",
              icon: "fa-trophy", rows: boards.top_sales, money: true },
            { key: "top_collectors", title: "Top collectors", sub: "by money brought in",
              icon: "fa-money", rows: boards.top_collectors, money: true },
            { key: "top_rate", title: "Best collection rate", sub: "among real books",
              icon: "fa-percent", rows: boards.top_rate, money: false },
        ];
    }

    /** Achievement against a money target: null target reads "not set", not 0 %. */
    salesPct(row) {
        return row.sales_pct === null || row.sales_pct === undefined ? null : row.sales_pct;
    }

    // ------------------------------------------------------------------ targets
    startEditTarget(row, ev) {
        if (ev) { ev.stopPropagation(); }
        if (!this.canEditTargets) { return; }  // not this viewer's number to move
        if (!row.own_target) { return; }   // salespeople follow the company figure
        this.state.editingTarget = row.key;
    }

    /**
     * Write a route's target straight from the table.
     *
     * The row is patched in place rather than reloading: a reload would collapse
     * whatever breakdown the user has open and lose their scroll position, for a
     * number we already know. The blended total is recomputed from the same rule
     * the server uses, so the footer keeps agreeing with the rows.
     */
    onTargetKey(ev) {
        if (ev.key === "Enter") {
            ev.target.blur();          // blur commits through saveTarget
        } else if (ev.key === "Escape") {
            // Closing the box fires blur, which would otherwise save the very edit
            // the user just abandoned.
            this.cancelTarget = true;
            this.state.editingTarget = null;
        }
    }

    async saveTarget(row, ev) {
        this.state.editingTarget = null;
        if (this.cancelTarget) {
            this.cancelTarget = false;
            return;
        }
        if (!this.canEditTargets) { return; }  // crm.team denies the write anyway
        const value = Math.max(0, Math.min(parseFloat(ev.target.value), 100));
        if (isNaN(value) || value === row.target) { return; }
        // Not a plain orm.write: crm.team's own ACL denies it even to an Accounts
        // Manager, who is not a Sales Administrator - the same reason the
        // person-level target goes through set_user_setting rather than a
        // direct write on res.users. (client, 2026-08-29)
        await this.orm.call("lab.collection.performance", "set_team_setting", [], {
            team_id: row.key,
            values: { lab_collection_target: value },
        });
        row.target = value;
        this.reblendTarget();
    }

    startEditSales(row, ev) {
        if (ev) { ev.stopPropagation(); }
        if (!this.canEditTargets) { return; }  // not this viewer's number to move
        if (!row.key) { return; }            // "Unassigned" is nobody's target
        this.state.editingSales = row.key;
    }

    async saveSalesTarget(row, ev) {
        this.state.editingSales = null;
        if (this.cancelTarget) {
            this.cancelTarget = false;
            return;
        }
        if (!this.canEditTargets) { return; }  // crm.team / set_user_setting deny it anyway
        const value = Math.max(0, parseFloat(ev.target.value) || 0);
        if (value === row.sales_target) { return; }
        if (this.state.tab === "route") {
            // Same gated setter the Collect % cell uses, for the same reason:
            // crm.team's own ACL denies a plain write here too. (client, 2026-08-29)
            await this.orm.call("lab.collection.performance", "set_team_setting", [], {
                team_id: row.key,
                values: { lab_sales_target: value },
            });
        } else {
            // res.users is writable only by ERP managers, which the accounts desk is
            // not - this raised AccessError for its whole audience. Goes through the
            // module's own gated setter instead. (2026-08-21)
            await this.orm.call("lab.collection.performance", "set_user_setting", [], {
                user_id: row.key,
                values: { lab_sales_target: value },
            });
        }
        row.sales_target = value;
        row.sales_pct = value ? Math.round(row.sales / value * 1000) / 10 : null;
    }

    get totalSalesTarget() {
        return this.rows.reduce((sum, r) => sum + (r.sales_target || 0), 0);
    }

    reblendTarget() {
        const rows = (this.state.data && this.state.data.by_route) || [];
        const billed = rows.filter((r) => r.sales > 0);
        const weight = billed.reduce((sum, r) => sum + r.sales, 0);
        if (weight) {
            this.state.data.totals.target = Math.round(
                billed.reduce((sum, r) => sum + r.sales * r.target, 0) / weight * 10) / 10;
        }
    }

    /** Height of one sparkline column, as a % of the tallest month in that row. */
    sparkHeight(point, trend) {
        const top = Math.max(...trend.map((p) => Math.min(p.percent, 150)), 1);
        return Math.max((Math.min(point.percent, 150) / top) * 100, 3);
    }

    ageClass(bucket) {
        return {
            current: "o_coll_age_current",
            d30: "o_coll_age_30",
            d60: "o_coll_age_60",
            d90: "o_coll_age_90",
        }[bucket] || "";
    }

    printPdf() {
        return this.action.doAction({
            type: "ir.actions.report",
            report_type: "qweb-pdf",
            report_name: "lab_collections.report_collection",
            report_file: "lab_collections.report_collection",
            data: { options: this.state.options, tab: this.state.tab },
        });
    }
}

registry.category("actions").add("lab_collection_dashboard", CollectionDashboard);
