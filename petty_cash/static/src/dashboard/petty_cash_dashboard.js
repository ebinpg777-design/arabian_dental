/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onWillStart, useState } from "@odoo/owl";

export class PettyCashDashboard extends Component {
    static template = "petty_cash.PettyCashDashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ data: null, period: "all", loading: true });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        this.state.data = await this.orm.call(
            "petty.cash.allocation",
            "get_dashboard_data",
            [],
            { period: this.state.period }
        );
        this.state.loading = false;
    }

    async setPeriod(period) {
        if (this.state.period === period) {
            return;
        }
        this.state.period = period;
        await this.load();
    }

    async openAllocations(domain) {
        const action = await this.orm.call(
            "petty.cash.allocation",
            "action_open_dashboard_allocations",
            [domain || []]
        );
        this.action.doAction(action);
    }

    openHolder(partnerId) {
        this.openAllocations([
            ["partner_id", "=", partnerId],
            ["state", "=", "allocated"],
        ]);
    }

    openApprovals(model, domain) {
        this.openList(model, domain, "Pending Approvals");
    }

    openTransactions(domain) {
        this.openList("petty.cash.transaction", domain, "Transactions");
    }

    openList(model, domain, name) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: name,
            res_model: model,
            view_mode: "list,form",
            views: [[false, "list"], [false, "form"]],
            domain: domain,
        });
    }

    healthColor(health) {
        return health === "critical" ? "danger" : health === "warning" ? "warning" : "success";
    }

    barWidth(pct) {
        return Math.max(0, Math.min(100, pct || 0));
    }

    pct(part, total) {
        return total ? Math.round((part / total) * 1000) / 10 : 0;
    }
}

registry.category("actions").add("petty_cash_dashboard", PettyCashDashboard);
