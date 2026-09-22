/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { formatMonetary } from "@web/views/fields/formatters";

/**
 * New clinics, a month at a time.
 *
 * A field executive is judged on the work they bring back and the doors they open, and
 * the lab could see the first everywhere and the second nowhere. Scoped by who is
 * asking: an executive sees their own routes, a manager sees the company.
 */
export class LabNewClinics extends Component {
    static template = "lab_fieldwork.NewClinics";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ month: false, data: null, loading: true });
        onWillStart(() => this.load());
    }

    async load(month) {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call(
                "lab.new.clinics", "get_new_clinics", [month || this.state.month]
            );
            this.state.month = this.state.data.month;
        } finally {
            this.state.loading = false;
        }
    }

    money(value) {
        return formatMonetary(value || 0, {
            currencyId: this.state.data && this.state.data.currency_id,
        });
    }

    /** Opening the list is how a name becomes a phone call. */
    async openList(routeId) {
        const action = await this.orm.call("lab.new.clinics", "open_clinics", [
            this.state.month,
            routeId || false,
        ]);
        this.action.doAction(action);
    }

    openClinic(row) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "res.partner",
            res_id: row.id,
            views: [[false, "form"]],
        });
    }
}

registry.category("actions").add("lab_new_clinics", LabNewClinics);
