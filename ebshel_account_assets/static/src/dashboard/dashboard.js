/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

/** Assets at a glance: what they are worth, what they cost this year, what is
 *  due, what needs a decision. Every figure opens the list behind it. */
export class AssetDashboard extends Component {
    static template = "ebshel_account_assets.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ loading: true, data: null, busy: false });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        this.state.data = await this.orm.call("ebshel.asset.dashboard", "get_data", []);
        this.state.loading = false;
    }

    open(domain, name) {
        this.action.doAction({ type: "ir.actions.act_window", name, res_model: "ebshel.asset",
                               views: [[false, "list"], [false, "kanban"], [false, "form"]], domain, context: {} });
    }

    openOne(id) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "ebshel.asset", res_id: id, views: [[false, "form"]] });
    }

    kpiOpen(kpi) {
        const map = {
            count: [[["state", "in", ["running", "paused"]]], "Assets in use"],
            gross: [[["state", "in", ["running", "paused", "closed"]]], "Assets"],
            book: [[["state", "in", ["running", "paused", "closed"]]], "Assets"],
            additions: [[["state", "in", ["running", "paused", "closed"]]], "Assets"],
            disposals: [[["state", "=", "disposed"]], "Disposed assets"],
            due: [[["id", "in", this.state.data.overdue_ids]], "Assets with overdue depreciation"],
        };
        if (kpi.key === "charge") {
            this.action.doAction({ type: "ir.actions.client", tag: "ebshel_fin_report", name: "Depreciation Schedule",
                                   params: { report_key: "asset_schedule" } });
            return;
        }
        const [domain, name] = map[kpi.key] || [[], "Assets"];
        this.open(domain, name);
    }

    async postOverdue() {
        this.state.busy = true;
        try {
            await this.orm.call("ebshel.asset.dashboard", "post_overdue", []);
            await this.load();
        } finally {
            this.state.busy = false;
        }
    }

    barPct(value, items, key) {
        const top = Math.max(...items.map((i) => i[key]), 1);
        return Math.round((value / top) * 100);
    }
}

registry.category("actions").add("ebshel_asset_dashboard", AssetDashboard);
