/** @odoo-module **/

import { Component, useProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

/**
 * Month-end before it happens: one row per executive with what was billed, what came in
 * and how, which cheques still wait for the bank, and the incentive their sheet would
 * show - computed by the server exactly as the sheet computes it, written nowhere.
 */
export class LabIncentivePreview extends Component {
    static template = "lab_incentive.IncentivePreview";
    props = useProps(standardFieldProps);

    get data() {
        return this.props.record.data[this.props.name] || {};
    }

    get rows() {
        return this.data.rows || [];
    }

    get collected() {
        return this.data.basis === "collected";
    }

    /** Width of each part of a row's money bar, against the busiest executive. */
    part(row, key) {
        const scale = this.data.scale || 0;
        return scale > 0 ? `width: ${(100 * (row[key] || 0)) / scale}%` : "width: 0";
    }
}

registry.category("fields").add("lab_incentive_preview", {
    component: LabIncentivePreview,
    supportedTypes: ["json"],
});
