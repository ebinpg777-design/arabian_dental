/** @odoo-module **/

import { Component, proxy, useProps } from "@odoo/owl";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { useDropdownState } from "@web/core/dropdown/dropdown_hooks";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { tileIconName } from "../core/tile_icons";

/**
 * A curated slice of the icons Odoo 20 ships - Material Symbols, plus the
 * odoo_ui_icons "oi_*" set - the ones that actually read at tile size, grouped
 * so the grid tells a story. (Odoo 20 has no FontAwesome: this list replaces
 * the 19.0 one name for name, see models/tile_icons.py.)
 */
export const TILE_ICONS = [
    // status
    "flash_on", "check_circle", "check_box", "cancel", "block", "warning", "error", "help",
    "info", "priority_high", "emergency", "hourglass_top", "schedule", "timer", "history",
    "refresh", "progress_activity", "flag", "sports_score", "thumb_up", "thumb_down", "star",
    "favorite", "bookmark", "notifications", "campaign", "lock", "lock_open", "visibility",
    "verified",
    // documents
    "description", "article", "content_copy", "folder_open", "assignment", "task", "checklist",
    "edit", "edit_square", "attach_file", "print", "mail", "send", "inbox", "archive",
    "sticky_note_2", "book", "newspaper", "receipt_long",
    // money
    "payments", "attach_money", "euro", "credit_card", "account_balance", "calculate", "percent",
    "sell", "label", "shopping_cart", "shopping_basket", "shopping_bag", "storefront",
    "card_giftcard", "balance",
    // logistics
    "local_shipping", "deployed_code", "inventory_2", "factory", "build", "settings", "barcode",
    "qr_code", "recycling", "location_on", "public", "flight", "directions_boat", "directions_car",
    // people
    "person", "group", "person_add", "oi_user-secret", "badge", "handshake", "forum",
    "chat_bubble", "phone", "oi_whatsapp", "headphones", "child_care",
    // analytics
    "show_chart", "bar_chart", "area_chart", "pie_chart", "speed", "signal_cellular_4_bar",
    "trophy", "radio_button_checked", "rocket_launch", "wand_stars", "lightbulb", "whatshot",
    "eco", "diamond", "my_location",
    // time & places
    "calendar_today", "today", "event_available", "event_busy", "event_upcoming",
    "hourglass_bottom", "business", "home", "work", "luggage", "security", "stethoscope",
    "medical_services", "local_hospital", "school", "coffee", "restaurant", "support", "key",
    "filter_alt", "search", "format_list_bulleted", "grid_view", "account_tree", "shuffle",
    "share", "radio_button_unchecked", "square", "more_horiz", "add_circle", "remove_circle",
    "arrow_circle_up", "arrow_circle_down", "arrow_forward",
];

/** Searchable icon picker for a char field holding an Odoo 20 icon name. */
export class TileIconPickerField extends Component {
    static template = "ebshel_dynamic_filter.TileIconPickerField";
    static components = { Dropdown };
    props = useProps({ ...standardFieldProps });

    setup() {
        this.state = proxy({ query: "" });
        this.dropdown = useDropdownState();
    }

    get currentIcon() {
        return tileIconName(this.props.record.data[this.props.name]);
    }

    get icons() {
        // "local shipping", "Local-Shipping" and "local_shipping" all find it.
        const query = this.state.query.trim().toLowerCase().replace(/[\s-]+/g, "_");
        if (!query) {
            return TILE_ICONS;
        }
        return TILE_ICONS.filter((icon) => icon.includes(query));
    }

    select(icon) {
        if (this.props.readonly) {
            return;
        }
        this.props.record.update({ [this.props.name]: icon });
        this.dropdown.close();
    }

    onManualInput(ev) {
        this.props.record.update({ [this.props.name]: ev.target.value.trim() });
    }
}

export const tileIconPickerField = {
    component: TileIconPickerField,
    displayName: _t("Icon Picker"),
    supportedTypes: ["char"],
};

registry.category("fields").add("tile_icon_picker", tileIconPickerField);
// The 19.0 name of the widget, for views written against it.
registry.category("fields").add("fa_icon_picker", tileIconPickerField);
