/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { useDropdownState } from "@web/core/dropdown/dropdown_hooks";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

/**
 * A curated slice of the FontAwesome 4.7 set Odoo already ships - the icons
 * that actually read at tile size, grouped so the grid tells a story.
 */
export const TILE_ICONS = [
    // status
    "fa-bolt", "fa-check-circle", "fa-check-square-o", "fa-times-circle", "fa-ban",
    "fa-exclamation-triangle", "fa-exclamation-circle", "fa-question-circle", "fa-info-circle",
    "fa-hourglass-half", "fa-clock-o", "fa-history", "fa-refresh", "fa-spinner", "fa-flag",
    "fa-flag-checkered", "fa-thumbs-o-up", "fa-thumbs-o-down", "fa-star", "fa-star-o",
    "fa-heart", "fa-bookmark-o", "fa-bell", "fa-bullhorn", "fa-lock", "fa-unlock",
    // documents
    "fa-file-text-o", "fa-file-o", "fa-files-o", "fa-folder-open-o", "fa-clipboard",
    "fa-pencil", "fa-pencil-square-o", "fa-paperclip", "fa-print", "fa-envelope-o",
    "fa-paper-plane", "fa-inbox", "fa-archive", "fa-sticky-note-o", "fa-book", "fa-newspaper-o",
    // money
    "fa-money", "fa-credit-card", "fa-bank", "fa-calculator", "fa-percent", "fa-tag", "fa-tags",
    "fa-shopping-cart", "fa-shopping-basket", "fa-shopping-bag", "fa-gift", "fa-balance-scale",
    // logistics
    "fa-truck", "fa-cube", "fa-cubes", "fa-industry", "fa-wrench", "fa-cogs",
    "fa-barcode", "fa-qrcode", "fa-recycle", "fa-map-marker", "fa-globe", "fa-plane", "fa-ship",
    // people
    "fa-user", "fa-user-o", "fa-users", "fa-user-plus", "fa-user-secret", "fa-address-card-o",
    "fa-handshake-o", "fa-comments-o", "fa-comment-o", "fa-phone", "fa-headphones", "fa-child",
    // analytics
    "fa-line-chart", "fa-bar-chart", "fa-area-chart", "fa-pie-chart", "fa-tachometer",
    "fa-signal", "fa-dashboard", "fa-trophy", "fa-bullseye", "fa-rocket", "fa-magic",
    "fa-lightbulb-o", "fa-fire", "fa-leaf", "fa-diamond", "fa-crosshairs",
    // time & places
    "fa-calendar", "fa-calendar-check-o", "fa-calendar-times-o", "fa-hourglass-end",
    "fa-building-o", "fa-home", "fa-briefcase", "fa-suitcase", "fa-shield", "fa-stethoscope",
    "fa-medkit", "fa-graduation-cap", "fa-coffee", "fa-life-ring", "fa-key", "fa-filter",
    "fa-search", "fa-list-ul", "fa-th-large", "fa-sitemap", "fa-random", "fa-share-alt",
    "fa-circle-o", "fa-square-o", "fa-dot-circle-o", "fa-ellipsis-h", "fa-plus-circle",
    "fa-minus-circle", "fa-arrow-circle-up", "fa-arrow-circle-down", "fa-long-arrow-right",
];

/** Searchable FontAwesome picker for a char field holding an icon class. */
export class FaIconPickerField extends Component {
    static template = "dynamic_filter_tiles.FaIconPickerField";
    static components = { Dropdown };
    static props = { ...standardFieldProps };

    setup() {
        this.state = useState({ query: "" });
        this.dropdown = useDropdownState();
    }

    get currentIcon() {
        return (this.props.record.data[this.props.name] || "").trim() || "fa-circle-o";
    }

    get icons() {
        const query = this.state.query.trim().toLowerCase().replace(/^fa-/, "");
        if (!query) {
            return TILE_ICONS;
        }
        return TILE_ICONS.filter((icon) => icon.includes(query));
    }

    iconClass(icon) {
        return icon.startsWith("fa-") ? `fa ${icon}` : icon;
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

export const faIconPickerField = {
    component: FaIconPickerField,
    displayName: _t("Icon Picker"),
    supportedTypes: ["char"],
};

registry.category("fields").add("fa_icon_picker", faIconPickerField);
