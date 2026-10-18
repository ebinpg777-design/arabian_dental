/** @odoo-module **/

import { registry } from "@web/core/registry";
import { listView } from "@web/views/list/list_view";
import { ListRenderer } from "@web/views/list/list_renderer";
import { useService } from "@web/core/utils/hooks";
import { debounce } from "@web/core/utils/timing";
import { Component, onWillStart, onWillUpdateProps, useState } from "@odoo/owl";

// ─────────────────────────────────────────────────────────────────────────────
//  RFQ list KPI dashboard — colored filter tiles above the RFQ list, modelled
//  on the standard Purchase Order dashboard.
// ─────────────────────────────────────────────────────────────────────────────

export class RFQDashboard extends Component {
    static template = "ebshel_purchase_request.RFQDashboard";
    static props = { list: { type: Object, optional: true } };

    setup() {
        this.orm = useService("orm");
        this.state = useState({ data: null });
        // retrieve_dashboard is filter-independent; collapse the prop-update
        // bursts (search keystrokes, filter toggles, pager) into one trailing
        // call instead of a full aggregation per re-render.
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
            this.state.data = await this.orm.call("ebshel.purchase.rfq", "retrieve_dashboard");
        } finally {
            this._loading = false;
        }
    }

    /** Clear the current search and activate the filter(s) named on the tile. */
    setSearchContext(ev) {
        const filters = ev.currentTarget.getAttribute("filter_name").split(",");
        const items = this.env.searchModel.getSearchItems((i) => filters.includes(i.name));
        this.env.searchModel.query = [];
        for (const item of items) {
            this.env.searchModel.toggleSearchItem(item.id);
        }
    }
}

export class RFQDashboardRenderer extends ListRenderer {
    static template = "ebshel_purchase_request.RFQListRenderer";
    static components = { ...ListRenderer.components, RFQDashboard };
}

export const RFQDashboardListView = {
    ...listView,
    Renderer: RFQDashboardRenderer,
};

registry.category("views").add("ebshel_rfq_dashboard_list", RFQDashboardListView);
