/** @odoo-module **/

import { Component } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { getTilePalette } from "../core/tile_colors";

/** Swatch row for the tile colour selection - faster to read than a dropdown. */
export class TileColorPickerField extends Component {
    static template = "ebshel_dynamic_filter.TileColorPickerField";
    static props = { ...standardFieldProps };

    get palette() {
        return getTilePalette();
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

export const tileColorPickerField = {
    component: TileColorPickerField,
    displayName: _t("Tile Colour Picker"),
    supportedTypes: ["selection"],
};

registry.category("fields").add("tile_color_picker", tileColorPickerField);
