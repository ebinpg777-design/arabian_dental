/** @odoo-module **/

import { Component, useProps } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

/**
 * The Authentication Card wizard's stock choice as two large tiles - a picture of
 * the card, what it is called, and what will be printed on it - instead of two
 * radio buttons (client, 2026-10-20: "advanced and attractive UI for the wizard").
 * The field stays a plain selection; a click writes the tile's value.
 */
const HINTS = {
    pre_printed: _t("Only the patient, doctor, job and date, onto the lab's printed cards - where Odoo 17 put them (80 × 50 mm)."),
    blank: _t("The whole card - title, quality badge, logo and contacts - on a plain white card."),
};

export class LabCardStockField extends Component {
    static template = "sale_custom.LabCardStockField";
    props = useProps({ ...standardFieldProps });

    get value() {
        return this.props.record.data[this.props.name];
    }

    get options() {
        const selection = this.props.record.fields[this.props.name].selection || [];
        return selection.map(([value, label]) => ({ value, label, hint: HINTS[value] || "" }));
    }

    select(value) {
        if (this.props.readonly || value === this.value) {
            return;
        }
        this.props.record.update({ [this.props.name]: value });
    }
}

export const labCardStockField = {
    component: LabCardStockField,
    displayName: _t("Card stock tiles"),
    supportedTypes: ["selection"],
};

registry.category("fields").add("lab_card_stock", labCardStockField);
