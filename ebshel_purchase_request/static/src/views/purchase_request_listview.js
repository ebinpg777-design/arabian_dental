/** @odoo-module **/

import { registry } from "@web/core/registry";
import { listView } from "@web/views/list/list_view";
import { ListRenderer } from "@web/views/list/list_renderer";
import { PRDashboard } from "./purchase_request_dashboard";

export class PRDashboardRenderer extends ListRenderer {
    static template = "ebshel_purchase_request.PRListRenderer";
    static components = { ...ListRenderer.components, PRDashboard };
}

export const PRDashboardListView = {
    ...listView,
    Renderer: PRDashboardRenderer,
};

registry.category("views").add("pr_dashboard_list", PRDashboardListView);
