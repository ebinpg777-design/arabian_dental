import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { CharField, charField } from "@web/views/fields/char/char_field";
import { useService } from "@web/core/utils/hooks";

import { Component, onWillStart, useRef, useState } from "@odoo/owl";

/* ==========================================================================
 * Layout picker
 *
 * The assistant's layout choice is a choice between shapes.  A dropdown makes
 * you read nine names and imagine each one; a wireframe card lets you point at
 * the one that already looks right.
 * ========================================================================== */

/**
 * Wireframes, drawn as fractions of the card so one definition serves every
 * aspect ratio.  `k` picks the block's visual weight: "bar" a line of text,
 * "big" a headline, "code" a barcode hatch, "box" an image or QR placeholder.
 */
const LAYOUT_SHAPES = {
    retail_tag: [
        { x: 4, y: 6, w: 92, h: 16, k: "big" },
        { x: 4, y: 28, w: 42, h: 8, k: "bar" },
        { x: 52, y: 26, w: 44, h: 20, k: "price" },
        { x: 6, y: 54, w: 88, h: 38, k: "code" },
    ],
    shelf_edge: [
        { x: 3, y: 8, w: 55, h: 18, k: "big" },
        { x: 3, y: 30, w: 40, h: 8, k: "bar" },
        { x: 3, y: 44, w: 55, h: 46, k: "code" },
        { x: 64, y: 10, w: 33, h: 34, k: "price" },
        { x: 64, y: 50, w: 33, h: 8, k: "bar" },
    ],
    promo: [
        { x: 4, y: 6, w: 92, h: 14, k: "big" },
        { x: 4, y: 26, w: 34, h: 10, k: "strike" },
        { x: 4, y: 40, w: 52, h: 26, k: "price" },
        { x: 62, y: 24, w: 34, h: 26, k: "badge" },
        { x: 6, y: 72, w: 88, h: 22, k: "code" },
    ],
    barcode_only: [
        { x: 5, y: 8, w: 90, h: 64, k: "code" },
        { x: 20, y: 78, w: 60, h: 12, k: "bar" },
    ],
    product_card: [
        { x: 4, y: 8, w: 26, h: 50, k: "box" },
        { x: 34, y: 8, w: 62, h: 16, k: "big" },
        { x: 34, y: 28, w: 62, h: 8, k: "bar" },
        { x: 34, y: 40, w: 62, h: 18, k: "price" },
        { x: 4, y: 66, w: 60, h: 26, k: "code" },
        { x: 70, y: 66, w: 26, h: 26, k: "box" },
    ],
    asset_tag: [
        { x: 4, y: 10, w: 34, h: 80, k: "qr" },
        { x: 42, y: 8, w: 30, h: 16, k: "box" },
        { x: 42, y: 30, w: 54, h: 22, k: "big" },
        { x: 42, y: 58, w: 54, h: 10, k: "bar" },
        { x: 42, y: 74, w: 54, h: 8, k: "bar" },
    ],
    address: [
        { x: 4, y: 5, w: 22, h: 14, k: "box" },
        { x: 32, y: 6, w: 64, h: 10, k: "bar" },
        { x: 4, y: 26, w: 40, h: 8, k: "bar" },
        { x: 4, y: 38, w: 66, h: 44, k: "text" },
        { x: 74, y: 38, w: 22, h: 40, k: "qr" },
    ],
    address_vcard: [
        { x: 5, y: 10, w: 56, h: 16, k: "big" },
        { x: 5, y: 30, w: 40, h: 8, k: "bar" },
        { x: 5, y: 43, w: 56, h: 30, k: "text" },
        { x: 5, y: 78, w: 88, h: 8, k: "bar" },
        { x: 68, y: 12, w: 27, h: 52, k: "qr" },
    ],
    lot_trace: [
        { x: 4, y: 4, w: 92, h: 16, k: "big" },
        { x: 4, y: 26, w: 56, h: 18, k: "price" },
        { x: 64, y: 26, w: 32, h: 10, k: "bar" },
        { x: 64, y: 40, w: 32, h: 8, k: "bar" },
        { x: 6, y: 56, w: 88, h: 38, k: "code" },
    ],
};

export class LayoutPickerField extends Component {
    static template = "epg_product_label.LayoutPickerField";
    static props = { ...standardFieldProps };

    get options() {
        // The field's own selection is the source of truth for which layouts are
        // offered, so a domain change on the server needs no edit here.
        return this.props.record.fields[this.props.name].selection
            .filter(([value]) => value in LAYOUT_SHAPES)
            .map(([value, label]) => ({ value, label, shapes: LAYOUT_SHAPES[value] }));
    }

    get value() {
        return this.props.record.data[this.props.name];
    }

    isSelected(option) {
        return option.value === this.value;
    }

    blockClass(shape) {
        return `epg_layout_block epg_layout_block_${shape.k}`;
    }

    blockStyle(shape) {
        return `left:${shape.x}%;top:${shape.y}%;width:${shape.w}%;height:${shape.h}%;`;
    }

    async select(option) {
        if (this.props.readonly) {
            return;
        }
        await this.props.record.update({ [this.props.name]: option.value });
    }
}

registry.category("fields").add("epg_layout_picker", {
    component: LayoutPickerField,
    displayName: _t("Layout Picker"),
    supportedTypes: ["selection"],
});

/* ==========================================================================
 * Field path picker
 *
 * A dotted path is easy to write and easy to get subtly wrong - categ_id.name
 * or categ_id.complete_name, uom_id.name or uom_id.display_name.  This walks
 * the real models one hop at a time and only offers what is actually there.
 * ========================================================================== */

export class FieldPathField extends CharField {
    static template = "epg_product_label.FieldPathField";
    static props = { ...CharField.props };

    setup() {
        super.setup();
        this.orm = useService("orm");
        this.pickerState = useState({
            open: false,
            query: "",
            browsePath: "",
            suggestions: [],
            model: "",
            status: null,
            loading: false,
        });
        this.rootRef = useRef("root");
        onWillStart(() => this.refreshStatus());
    }

    /** The model a path on this element is resolved against. */
    get modelName() {
        return this.props.record.data.model_name || "product.template";
    }

    get pathValue() {
        return this.props.record.data[this.props.name] || "";
    }

    get statusClass() {
        const status = this.pickerState.status;
        if (!status || !this.pathValue) {
            return "";
        }
        return status.valid ? "text-success" : "text-danger";
    }

    async refreshStatus() {
        const path = this.pathValue;
        if (!path) {
            this.pickerState.status = null;
            return;
        }
        try {
            this.pickerState.status = await this.orm.call(
                "epg.label.element",
                "resolve_field_path",
                [this.modelName, path]
            );
        } catch {
            this.pickerState.status = null;
        }
    }

    async togglePicker() {
        this.pickerState.open = !this.pickerState.open;
        if (this.pickerState.open) {
            // Open on the branch the current value already points into, so
            // refining an existing path does not start from the root again.
            const path = this.pathValue;
            this.pickerState.browsePath = path.includes(".")
                ? path.slice(0, path.lastIndexOf("."))
                : "";
            this.pickerState.query = "";
            await this.loadSuggestions();
        }
    }

    async loadSuggestions() {
        this.pickerState.loading = true;
        try {
            const result = await this.orm.call(
                "epg.label.element",
                "field_suggestions",
                [this.modelName, this.pickerState.browsePath, this.pickerState.query]
            );
            this.pickerState.suggestions = result.fields;
            this.pickerState.model = result.model;
        } finally {
            this.pickerState.loading = false;
        }
    }

    async onQueryInput(event) {
        this.pickerState.query = event.target.value;
        await this.loadSuggestions();
    }

    /** Step into a relation without committing it as the final value. */
    async browseInto(suggestion) {
        this.pickerState.browsePath = suggestion.path;
        this.pickerState.query = "";
        await this.loadSuggestions();
    }

    async browseUp() {
        const path = this.pickerState.browsePath;
        this.pickerState.browsePath = path.includes(".")
            ? path.slice(0, path.lastIndexOf("."))
            : "";
        this.pickerState.query = "";
        await this.loadSuggestions();
    }

    get breadcrumbs() {
        if (!this.pickerState.browsePath) {
            return [];
        }
        return this.pickerState.browsePath.split(".");
    }

    async choose(suggestion) {
        await this.props.record.update({ [this.props.name]: suggestion.path });
        this.pickerState.open = false;
        await this.refreshStatus();
    }

    async onInputBlur() {
        await this.refreshStatus();
    }
}

registry.category("fields").add("epg_field_path", {
    ...charField,
    component: FieldPathField,
    displayName: _t("Field Path"),
    supportedTypes: ["char"],
});

/* ==========================================================================
 * Barcode status badge
 *
 * Renders the server-side verdict from epg.label.element.check_barcode_value
 * as a badge with the sample payload, so the answer to "will this scan?" is
 * visible without a test print.
 * ========================================================================== */

const STATUS_LOOK = {
    ok: { css: "text-bg-success", icon: "fa-check-circle" },
    warning: { css: "text-bg-warning", icon: "fa-exclamation-triangle" },
    error: { css: "text-bg-danger", icon: "fa-times-circle" },
    empty: { css: "text-bg-secondary", icon: "fa-circle-o" },
};

export class BarcodeStatusField extends Component {
    static template = "epg_product_label.BarcodeStatusField";
    static props = { ...standardFieldProps };

    get state() {
        return this.props.record.data[this.props.name] || "empty";
    }

    get look() {
        return STATUS_LOOK[this.state] || STATUS_LOOK.empty;
    }

    get message() {
        return this.props.record.data.barcode_status_message || "";
    }

    get sample() {
        return this.props.record.data.barcode_sample || "";
    }
}

registry.category("fields").add("epg_barcode_status", {
    component: BarcodeStatusField,
    displayName: _t("Barcode Status"),
    supportedTypes: ["selection"],
    fieldDependencies: [
        { name: "barcode_status_message", type: "char" },
        { name: "barcode_sample", type: "char" },
    ],
});
