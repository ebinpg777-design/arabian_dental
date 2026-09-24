/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

const COLUMNS = 12;

const NAMES = {
    1: _t("Sliver"),
    2: _t("Sixth"),
    3: _t("Quarter"),
    4: _t("Third"),
    6: _t("Half"),
    8: _t("Two thirds"),
    9: _t("Three quarters"),
    12: _t("Full row"),
};

/**
 * A card's width as the twelve columns it spans: click the column the card
 * should reach, and the segments up to it light up. Hovering previews.
 */
export class WidthPickerField extends Component {
    static template = "ebshel_dashboard.WidthPickerField";
    static props = { ...standardFieldProps };

    setup() {
        this.state = useState({ hover: 0 });
    }

    get columns() {
        return Array.from({ length: COLUMNS }, (_, index) => index + 1);
    }

    get current() {
        return Math.max(1, Math.min(Number(this.props.record.data[this.props.name]) || 3, COLUMNS));
    }

    get shown() {
        return this.state.hover || this.current;
    }

    get label() {
        const width = this.shown;
        const name = NAMES[width] || "";
        return name ? `${width}/12 · ${name}` : `${width}/12`;
    }

    select(width) {
        if (this.props.readonly) {
            return;
        }
        this.props.record.update({ [this.props.name]: width });
    }
}

export const widthPickerField = {
    component: WidthPickerField,
    displayName: _t("Card Width Picker"),
    supportedTypes: ["integer"],
};

registry.category("fields").add("dashboard_width_picker", widthPickerField);
