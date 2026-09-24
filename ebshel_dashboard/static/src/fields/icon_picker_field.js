/** @odoo-module **/

import { Component, useExternalListener, useRef, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

import { CARD_COLORS } from "../core/board_colors";
import { IconPanel } from "./icon_panel";

/**
 * The icon of a card: the current glyph in the card's own colour, the class
 * in a box for anyone who knows it by name, and the shared picker underneath.
 */
export class IconPickerField extends Component {
    static template = "ebshel_dashboard.IconPickerField";
    static components = { IconPanel };
    static props = { ...standardFieldProps };

    setup() {
        this.rootRef = useRef("root");
        this.state = useState({ open: false });
        useExternalListener(window, "click", (ev) => {
            if (this.state.open && this.rootRef.el && !this.rootRef.el.contains(ev.target)) {
                this.state.open = false;
            }
        });
    }

    get current() {
        return this.props.record.data[this.props.name] || "";
    }

    get color() {
        return CARD_COLORS[this.props.record.data.color] || CARD_COLORS.indigo;
    }

    toggle() {
        if (!this.props.readonly) {
            this.state.open = !this.state.open;
        }
    }

    select(icon) {
        this.props.record.update({ [this.props.name]: icon });
        this.state.open = false;
    }

    onTyped(ev) {
        // The plain input still works for anyone who knows the exact class.
        this.props.record.update({ [this.props.name]: ev.target.value.trim() });
    }
}

export const iconPickerField = {
    component: IconPickerField,
    displayName: _t("Icon Picker"),
    supportedTypes: ["char"],
};

registry.category("fields").add("dashboard_icon_picker", iconPickerField);
