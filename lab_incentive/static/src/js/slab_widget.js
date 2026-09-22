/** @odoo-module **/

import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

/**
 * The slab-progress widget (the "calculation widget" the client asked for).
 *
 * The sheet already computes a final number; what a manager approving it and an
 * executive reading it cannot see at a glance is WHY — which slab was hit, how much
 * more sales would reach the next one. This renders exactly that, from the JSON the
 * server already computed — no second calculation living in JS to drift from the
 * real one.
 */
export class LabSlabWidget extends Component {
    static template = "lab_incentive.SlabWidget";
    static props = { ...standardFieldProps };

    get data() {
        return this.props.record.data[this.props.name] || {};
    }

    get slabs() {
        return this.data.slabs || [];
    }

    barWidth(slab) {
        const base = this.data.base || 0;
        const span = slab.to != null ? slab.to - slab.from : Math.max(base, slab.from) - slab.from || 1;
        const filled = Math.max(0, Math.min(base, slab.to != null ? slab.to : base) - slab.from);
        return span > 0 ? Math.round((filled / span) * 100) : (base > slab.from ? 100 : 0);
    }
}

registry.category("fields").add("lab_slab_widget", {
    component: LabSlabWidget,
    supportedTypes: ["json"],
});
