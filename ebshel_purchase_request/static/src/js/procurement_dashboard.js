/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, useState, onMounted, onWillUnmount } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { user } from "@web/core/user";

// ─────────────────────────────────────────────────────────────────────────────
//  Procurement Intelligence Dashboard
//  Live KPIs · SVG donut · bar charts · vendor leaderboard · activity feed
// ─────────────────────────────────────────────────────────────────────────────

export class ProcurementDashboard extends Component {
    static template = "ebshel_purchase_request.ProcurementDashboard";
    static props = ["*"];

    setup() {
        this.orm           = useService("orm");
        this.actionService = useService("action");
        this.notification  = useService("notification");

        this.state = useState({
            loading:      true,
            error:        null,
            data:         null,
            lastRefresh:  null,
            animateIn:    false,
            hoveredMonth: null,
            hoveredVendor: null,
            period:       "month",   // "month" | "quarter" | "year"
        });

        this._timer = null;
        this._animTimer = null;
        this._refreshing = false;   // in-flight guard for the silent refresh

        onMounted(async () => {
            await this._loadData();
            // Silent background refresh every 60 s
            this._timer = setInterval(() => this._silentRefresh(), 60_000);
        });

        onWillUnmount(() => {
            if (this._timer) clearInterval(this._timer);
            if (this._animTimer) clearTimeout(this._animTimer);
        });
    }

    // ─── Data ─────────────────────────────────────────────────────────────────

    async _loadData() {
        this.state.loading   = true;
        this.state.error     = null;
        this.state.animateIn = false;
        try {
            const data = await this.orm.call(
                "ebshel.purchase.request",
                "get_dashboard_data",
                [],
                { period: this.state.period }
            );
            this.state.data        = data;
            this.state.lastRefresh = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            if (this._animTimer) clearTimeout(this._animTimer);
            this._animTimer = setTimeout(() => { this.state.animateIn = true; }, 60);
        } catch (e) {
            this.state.error = e.message || "Failed to load dashboard.";
        } finally {
            this.state.loading = false;
        }
    }

    async _silentRefresh() {
        // Skip when a refresh is already running or the tab is hidden, to avoid
        // overlapping ORM calls piling up.
        if (this._refreshing || (typeof document !== "undefined" && document.hidden)) return;
        this._refreshing = true;
        try {
            const data = await this.orm.call(
                "ebshel.purchase.request", "get_dashboard_data", [],
                { period: this.state.period }
            );
            this.state.data        = data;
            this.state.lastRefresh = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        } catch (_) { /* silent */ } finally {
            this._refreshing = false;
        }
    }

    // ─── SVG Donut ────────────────────────────────────────────────────────────

    get donutSegments() {
        const dist  = this.state.data?.status_distribution || [];
        const total = dist.reduce((s, d) => s + d.count, 0);
        if (!total) return [];

        const r = 52;
        const C = 2 * Math.PI * r;   // ≈ 326.73
        let accumulated = 0;

        return dist.map(seg => {
            const frac     = seg.count / total;
            const rotation = accumulated * 360 - 90;
            accumulated   += frac;
            return {
                ...seg,
                dasharray: `${(frac * C).toFixed(2)} ${C.toFixed(2)}`,
                transform: `rotate(${rotation.toFixed(2)}, 70, 70)`,
                pct: Math.round(frac * 100),
            };
        });
    }

    get donutTotal() {
        return (this.state.data?.status_distribution || [])
            .reduce((s, d) => s + d.count, 0);
    }

    // ─── Monthly Bar Chart ────────────────────────────────────────────────────

    get maxMonthSpend() {
        return Math.max(
            ...(this.state.data?.monthly_spend || []).map(m => m.amount),
            1
        );
    }

    getBarPct(amount) {
        const pct = (amount / this.maxMonthSpend) * 100;
        return `${Math.max(3, pct).toFixed(1)}%`;
    }

    isCurrentMonth(monthLabel) {
        const now = new Date();
        const cur = now.toLocaleString('en', { month: 'short' });
        return monthLabel.startsWith(cur);
    }

    // ─── Formatting ───────────────────────────────────────────────────────────

    _fmt(amount, compact = false) {
        if (!this.state.data || amount === undefined) return "—";
        const { currency_symbol: sym, currency_position: pos } = this.state.data;
        let val;
        if (compact && amount >= 1_000_000)     val = (amount / 1_000_000).toFixed(1) + "M";
        else if (compact && amount >= 1_000)    val = (amount / 1_000).toFixed(1) + "K";
        else val = new Intl.NumberFormat(undefined, {
            minimumFractionDigits: 0, maximumFractionDigits: 0,
        }).format(amount || 0);
        return pos === "before" ? `${sym}${val}` : `${val} ${sym}`;
    }

    // ─── Style helpers ────────────────────────────────────────────────────────

    stateBadgeClass(state) {
        return `dsh-badge dsh-badge--${state || 'draft'}`;
    }

    priorityIcon(p) {
        return { '0': '', '1': '⚡', '2': '🔥' }[p] || '';
    }

    kpiCardClass(key) {
        const base = 'dsh-kpi-card';
        if (key === 'pending' && (this.state.data?.my_pending || 0) > 0)
            return `${base} dsh-kpi-card--alert`;
        if (key === 'savings' && (this.state.data?.savings_month || 0) > 0)
            return `${base} dsh-kpi-card--green`;
        return `${base} dsh-kpi-card--${key}`;
    }

    get containerClass() {
        return `ebshel-procurement-dashboard${this.state.animateIn ? ' dsh-animate-in' : ''}`;
    }

    // ─── Navigation ──────────────────────────────────────────────────────────

    async onRefresh() {
        this.state.animateIn = false;
        await this._loadData();
    }

    onNewPR() {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "ebshel.purchase.request",
            view_mode: "form",
            views: [[false, "form"]],
        });
    }

    onPendingApprovals() {
        this.actionService.doAction({
            name: "Pending My Approval",
            type: "ir.actions.act_window",
            res_model: "ebshel.purchase.request",
            view_mode: "list,form",
            views: [[false, "list"], [false, "form"]],
            domain: [["state", "=", "submitted"],
                     ["pending_approver_ids", "in", [user.userId]]],
        });
    }

    onAllRFQs() {
        this.actionService.doAction({
            name: "Active RFQs",
            type: "ir.actions.act_window",
            res_model: "ebshel.purchase.rfq",
            view_mode: "list,form",
            views: [[false, "list"], [false, "form"]],
            domain: [["state", "in", ["draft", "sent", "comparing"]]],
        });
    }
        
    onPosThisPeriod() {
        const today = new Date();
        let periodStart;
        if (this.state.period === "year") {
            periodStart = new Date(today.getFullYear(), 0, 1);
        } else if (this.state.period === "quarter") {
            const qMonth = Math.floor(today.getMonth() / 3) * 3;
            periodStart = new Date(today.getFullYear(), qMonth, 1);
        } else {
            periodStart = new Date(today.getFullYear(), today.getMonth(), 1);
        }
        const dateStr = periodStart.toISOString().slice(0, 10); // YYYY-MM-DD

        this.actionService.doAction({
            name: `POs · ${this.state.data?.period_label || "This Month"}`,
            type: "ir.actions.act_window",
            res_model: "ebshel.purchase.rfq",
            view_mode: "list,form",
            views: [[false, "list"], [false, "form"]],
            domain: [
                ["state", "=", "po_created"],
                ["date_rfq", ">=", dateStr],
            ],
        });
    }

    onAllRequests() {
        this.actionService.doAction({
            name: "All Purchase Requests",
            type: "ir.actions.act_window",
            res_model: "ebshel.purchase.request",
            view_mode: "list,kanban,form",
            views: [[false, "list"], [false, "kanban"], [false, "form"]],
        });
    }

    onOpenPR(id) {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "ebshel.purchase.request",
            view_mode: "form",
            res_id: id,
            views: [[false, "form"]],
        });
    }

    onAllVendors() {
        this.actionService.doAction("ebshel_purchase_request.action_ebshel_vendors");
    }

    onOpenVendor(id) {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "res.partner",
            view_mode: "form",
            res_id: id,
            views: [[false, "form"]],
        });
    }

    onHoverMonth(short) { this.state.hoveredMonth  = short; }
    onLeaveMonth()      { this.state.hoveredMonth  = null;  }
    onHoverVendor(name) { this.state.hoveredVendor = name;  }
    onLeaveVendor()     { this.state.hoveredVendor = null;  }

    async setPeriod(period) {
        if (this.state.period === period) return;
        this.state.period = period;
        this.state.animateIn = false;
        await this._loadData();
    }

    /** Drill-down: open the PR list filtered to one status. */
    onOpenStatus(stateKey, label) {
        this.actionService.doAction({
            name: label ? `Requests · ${label}` : "Requests",
            type: "ir.actions.act_window",
            res_model: "ebshel.purchase.request",
            view_mode: "list,kanban,form",
            views: [[false, "list"], [false, "kanban"], [false, "form"]],
            domain: [["state", "=", stateKey]],
        });
    }

    /** Drill-down: open the PR list filtered to one department. */
    onOpenDept(deptName) {
        this.actionService.doAction({
            name: `Requests · ${deptName}`,
            type: "ir.actions.act_window",
            res_model: "ebshel.purchase.request",
            view_mode: "list,kanban,form",
            views: [[false, "list"], [false, "kanban"], [false, "form"]],
            domain: [["analytic_account_id.display_name", "=", deptName]],
        });
    }
}

registry.category("actions").add("ebshel_procurement_dashboard", ProcurementDashboard);
