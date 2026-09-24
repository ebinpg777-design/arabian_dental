/** @odoo-module **/

import { Component } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

import { getCardPalette } from "../core/board_colors";

/** Swatch row for the colour selection - faster to read than a dropdown. */
export class ColorPickerField extends Component {
    static template = "ebshel_dashboard.ColorPickerField";
    static props = { ...standardFieldProps };

    get palette() {
        return getCardPalette();
    }

    get current() {
        return this.props.record.data[this.props.name];
    }

    select(key) {
        if (this.props.readonly) {
            return;
        }
        this.props.record.update({ [this.props.name]: key });
    }
}

export const colorPickerField = {
    component: ColorPickerField,
    displayName: _t("Card Colour Picker"),
    supportedTypes: ["selection"],
};

registry.category("fields").add("dashboard_color_picker", colorPickerField);
