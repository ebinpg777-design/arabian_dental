/** @odoo-module **/

import { Component } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

import { CARD_COLORS, seriesColors } from "../core/board_colors";

/**
 * The card's palette as a row of swatches: each option shows the five
 * colours it would actually paint, "shades" in the card's own colour, and
 * "the dashboard's" as the tile that keeps every card matching.
 */
export class PalettePickerField extends Component {
    static template = "ebshel_dashboard.PalettePickerField";
    static props = { ...standardFieldProps };

    get options() {
        const description = this.props.record.fields[this.props.name];
        const base = CARD_COLORS[this.props.record.data.color] || CARD_COLORS.indigo;
        return (description.selection || []).map(([key, label]) => ({
            key,
            label,
            inherited: key === "board",
            dots: key === "board" ? [] : seriesColors(5, key, base),
        }));
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

export const palettePickerField = {
    component: PalettePickerField,
    displayName: _t("Card Palette Picker"),
    supportedTypes: ["selection"],
    fieldDependencies: [{ name: "color", type: "selection" }],
};

registry.category("fields").add("dashboard_palette_picker", palettePickerField);
