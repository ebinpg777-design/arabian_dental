/** @odoo-module **/

import { useService } from "@web/core/utils/hooks";
import { debounce } from "@web/core/utils/timing";
import { Component, onWillStart, onWillUpdateProps, proxy, t, useProps } from "@odoo/owl";

// ─────────────────────────────────────────────────────────────────────────────
//  Purchase Request list KPI dashboard — colored filter tiles above the list,
//  modelled on the standard Purchase Order dashboard.
// ─────────────────────────────────────────────────────────────────────────────

export class PRDashboard extends Component {
    static template = "ebshel_purchase_request.PRDashboard";
    props = useProps({ list: t.object().optional() });

    setup() {
        this.orm = useService("orm");
        this.state = proxy({ data: null, multiuser: false });
        // The embedded dashboard re-receives props on every list re-render
        // (search keystroke, filter toggle, pager). retrieve_dashboard is
        // filter-independent, so collapse those bursts into a single trailing
        // call instead of firing a full server-side aggregation each time.
        this._loadDebounced = debounce(() => this._load(), 400);
        onWillStart(() => this._load());
        onWillUpdateProps(() => this._loadDebounced());
    }

    async _load() {
        if (this._loading) {
            return;
        }
        this._loading = true;
        try {
            const data = await this.orm.call("ebshel.purchase.request", "retrieve_dashboard");
            this.state.data = data;
            this.state.multiuser =
                JSON.stringify(data.global) !== JSON.stringify(data.my);
        } finally {
            this._loading = false;
        }
    }

    fmtMoney(amount) {
        const d = this.state.data;
        if (!d) return "";
        const val = new Intl.NumberFormat(undefined, {
            notation: amount >= 100000 ? "compact" : "standard",
            maximumFractionDigits: amount >= 100000 ? 1 : 0,
        }).format(amount || 0);
        return d.currency_position === "before"
            ? `${d.currency_symbol} ${val}`
            : `${val} ${d.currency_symbol}`;
    }

    /** Clear the current search and activate the filter(s) named on the tile. */
    setSearchContext(ev) {
        const filters = ev.currentTarget.getAttribute("filter_name").split(",");
        const items = this.env.searchModel.getSearchItems((i) =>
            filters.includes(i.name)
        );
        this.env.searchModel.query = [];
        for (const item of items) {
            this.env.searchModel.toggleSearchItem(item.id);
        }
    }
}
