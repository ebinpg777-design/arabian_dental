/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, onMounted, proxy, useProps } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";

// ─────────────────────────────────────────────────────────────────────────────
//  Department / Cost-Centre Insight cockpit
//  Opened from a Purchase Request for the department selected on it. Aggregates
//  that cost centre's committed spend, status mix, trend, top products/vendors
//  and recent requests. Manager-level visibility.
// ─────────────────────────────────────────────────────────────────────────────

export class DepartmentInsight extends Component {
    static template = "ebshel_purchase_request.DepartmentInsight";
    props = useProps(standardActionServiceProps);

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = proxy({ loading: true, error: null, data: null });
        onMounted(() => this._load());
    }

    get analyticId() {
        return this.props.action.context?.analytic_account_id;
    }

    async _load() {
        if (!this.analyticId) {
            this.state.error = "No department provided.";
            this.state.loading = false;
            return;
        }
        try {
            this.state.data = await this.orm.call(
                "ebshel.purchase.request", "get_department_insight", [this.analyticId]);
        } catch (e) {
            this.state.error = e.message || "Failed to load department insight.";
        } finally {
            this.state.loading = false;
        }
    }

    // ── Formatting ────────────────────────────────────────────────────────────
    fmt(amount) {
        const d = this.state.data;
        if (!d || amount === undefined || amount === null) return "—";
        const v = new Intl.NumberFormat(undefined, {
            minimumFractionDigits: 0, maximumFractionDigits: 0,
        }).format(amount || 0);
        return d.currency_position === "before" ? `${d.currency_symbol} ${v}` : `${v} ${d.currency_symbol}`;
    }

    // ── Bar scaling helpers ───────────────────────────────────────────────────
    _max(list, key) {
        let m = 1;
        for (const x of (list || [])) if (x[key] > m) m = x[key];
        return m;
    }
    monthlyPct(a) { return `${Math.max(2, (a / this._max(this.state.data?.monthly, "amount")) * 100).toFixed(1)}%`; }
    statusPct(a)  { return `${Math.max(2, (a / this._max(this.state.data?.status_breakdown, "amount")) * 100).toFixed(1)}%`; }
    prodPct(a)    { return `${Math.max(2, (a / this._max(this.state.data?.top_products, "amount")) * 100).toFixed(1)}%`; }
    vendPct(a)    { return `${Math.max(2, (a / this._max(this.state.data?.top_vendors, "amount")) * 100).toFixed(1)}%`; }

    stateColor(st) {
        return ({
            draft: "#94a3b8", submitted: "#3b82f6", approved: "#22c55e",
            rejected: "#ef4444", cancelled: "#64748b", closed: "#8b5cf6",
        })[st] || "#94a3b8";
    }

    // ── Navigation ────────────────────────────────────────────────────────────
    get originPrId() {
        return this.props.action.context?.origin_pr_id;
    }

    onBack() {
        // Return to the originating request when we know it; otherwise fall back
        // to the browser/breadcrumb history.
        if (this.originPrId) {
            this.action.doAction({
                type: "ir.actions.act_window",
                res_model: "ebshel.purchase.request",
                res_id: this.originPrId,
                view_mode: "form",
                views: [[false, "form"]],
            });
        } else {
            this.action.doAction("ebshel_purchase_request.action_ebshel_purchase_request_all");
        }
    }

    onOpenPR(id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "ebshel.purchase.request",
            res_id: id,
            view_mode: "form",
            views: [[false, "form"]],
        });
    }

    onViewAll() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: `${this.state.data.department} — Requests`,
            res_model: "ebshel.purchase.request",
            view_mode: "list,kanban,form",
            views: [[false, "list"], [false, "kanban"], [false, "form"]],
            domain: [["analytic_account_id", "=", this.analyticId]],
        });
    }

    onRefresh() {
        this.state.loading = true;
        this._load();
    }
}

registry.category("actions").add("ebshel_department_insight", DepartmentInsight);
