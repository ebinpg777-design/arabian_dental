import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { X2ManyField, x2ManyField } from "@web/views/fields/x2many/x2many_field";
import { useService } from "@web/core/utils/hooks";

import { markup, onWillStart, useEffect, useRef, useState } from "@odoo/owl";

/**
 * Element types offered by the "Add" menu, in the order a designer usually
 * reaches for them.  `w`/`h` are starting sizes in millimetres; inch templates
 * get them converted on the way in.
 */
const ELEMENT_PALETTE = [
    { type: "text", label: _t("Text"), icon: "fa-font", w: 30, h: 5, group: "content" },
    { type: "field", label: _t("Field"), icon: "fa-database", w: 30, h: 5, group: "content" },
    { type: "attributes", label: _t("Product Attributes"), icon: "fa-list-ul", w: 30, h: 5, group: "content" },
    { type: "address", label: _t("Contact Address"), icon: "fa-map-marker", w: 45, h: 20, group: "content" },
    { type: "date", label: _t("Date"), icon: "fa-clock-o", w: 20, h: 4, group: "content" },
    { type: "price", label: _t("Price"), icon: "fa-tag", w: 25, h: 8, group: "price" },
    { type: "price_promo", label: _t("Promotional Price"), icon: "fa-percent", w: 25, h: 8, group: "price" },
    { type: "price_diff", label: _t("Price Difference"), icon: "fa-arrow-down", w: 20, h: 5, group: "price" },
    { type: "price_uom", label: _t("Price per Unit"), icon: "fa-balance-scale", w: 25, h: 4, group: "price" },
    { type: "pricelist_rule", label: _t("Pricelist Rule"), icon: "fa-calendar", w: 30, h: 4, group: "price" },
    { type: "barcode", label: _t("Barcode"), icon: "fa-barcode", w: 35, h: 12, group: "code" },
    { type: "qrcode", label: _t("QR Code"), icon: "fa-qrcode", w: 15, h: 15, group: "code" },
    { type: "vcard", label: _t("vCard QR Code"), icon: "fa-address-card-o", w: 18, h: 18, group: "code" },
    { type: "image", label: _t("Image"), icon: "fa-picture-o", w: 20, h: 20, group: "shape" },
    { type: "box", label: _t("Box"), icon: "fa-square-o", w: 30, h: 10, group: "shape" },
    { type: "line", label: _t("Line"), icon: "fa-minus", w: 30, h: 0.3, group: "shape" },
    { type: "ellipse", label: _t("Ellipse"), icon: "fa-circle-o", w: 15, h: 15, group: "shape" },
    { type: "html", label: _t("Custom HTML"), icon: "fa-code", w: 30, h: 10, group: "shape" },
];

const PALETTE_GROUPS = [
    { key: "content", label: _t("Content") },
    { key: "price", label: _t("Pricing") },
    { key: "code", label: _t("Codes") },
    { key: "shape", label: _t("Images & shapes") },
];

const TYPE_ICONS = Object.fromEntries(ELEMENT_PALETTE.map((e) => [e.type, e.icon]));

/** Which corner/edge a resize handle drives. */
const HANDLES = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];

/**
 * Fields the canvas sends back to the server so the live render reflects edits
 * that have not been saved yet.  Restricted to what the embedded list actually
 * loads, and to fields that are writable on the element.  ``condition`` is not
 * sent: the server evaluates it, and only ever from the saved element.
 */
const PATCH_FIELDS = [
    "pos_x", "pos_y", "width", "height", "rotation", "z_index", "opacity",
    "font_size", "font_bold", "text_align", "name", "element_type",
    "field_path", "text_content", "active",
];

/** CSS pixels in one designer unit at 100% zoom. */
const PX_PER_UNIT = { mm: 3.7795, in: 96 };

/** How close (in screen pixels) an edge has to be before it snaps to a guide. */
const GUIDE_THRESHOLD_PX = 6;

export class LabelDesignerField extends X2ManyField {
    static template = "epg_product_label.LabelDesignerField";
    static props = { ...X2ManyField.props };

    setup() {
        super.setup();
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.canvasRef = useRef("canvas");
        this.palette = ELEMENT_PALETTE;
        this.paletteGroups = PALETTE_GROUPS;
        this.handles = HANDLES;
        this.state = useState({
            selectedIds: [],
            zoom: 0,          // 0 means "fit to the panel"
            snap: true,
            grid: true,
            guides: true,
            wysiwyg: true,
            showPalette: false,
            showLayers: true,
            // While a pointer gesture is running we keep the geometry here and only
            // write it back on pointerup, so a drag is one ORM update, not sixty.
            dragging: null,
            marquee: null,
            activeGuides: [],
            rendered: {},     // element id -> printed HTML, from the server
            labelStyle: "",
            background: "",
            previewName: "",
            rendering: false,
        });
        this.pointer = null;
        this.undoStack = [];
        this.redoStack = [];
        this.renderTimer = null;

        onWillStart(() => this.refreshPreview());
        // Re-render whenever the element set or its geometry changes.  The
        // signature is cheap to build and stops a redraw from firing on every
        // unrelated keystroke in the form.
        useEffect(
            () => {
                this.scheduleRender();
            },
            () => [this.renderSignature]
        );
    }

    // ------------------------------------------------------------------
    // geometry of the label itself
    // ------------------------------------------------------------------
    get unit() {
        return this.props.record.data.unit || "mm";
    }

    get labelWidth() {
        return this.props.record.data.label_width || 1;
    }

    get labelHeight() {
        return this.props.record.data.label_height || 1;
    }

    /** Screen pixels per designer unit. */
    get scale() {
        if (this.state.zoom) {
            return this.state.zoom;
        }
        // Fit a comfortable default: wide labels get a smaller scale so the whole
        // label stays on screen without scrolling.
        const target = 560;
        const base = target / Math.max(this.labelWidth, 0.1);
        const max = this.unit === "mm" ? 12 : 300;
        return Math.min(base, max);
    }

    get canvasStyle() {
        return `width:${this.labelWidth * this.scale}px;height:${
            this.labelHeight * this.scale
        }px;`;
    }

    /**
     * The printed label is rendered at its true physical size and then scaled as
     * a whole.  Re-rendering it at canvas scale would mean a second layout engine
     * that could disagree with the PDF; scaling the genuine article cannot.
     */
    get paperStyle() {
        const unit = this.unit;
        const factor = this.scale / PX_PER_UNIT[unit];
        return (
            `width:${this.labelWidth}${unit};height:${this.labelHeight}${unit};` +
            `transform:scale(${factor});transform-origin:top left;`
        );
    }

    get gridStyle() {
        if (!this.state.grid) {
            return "";
        }
        const step = this.gridStep * this.scale;
        if (step < 3) {
            return "";
        }
        return (
            `background-image:linear-gradient(to right, rgba(0,0,0,.08) 1px, transparent 1px),` +
            `linear-gradient(to bottom, rgba(0,0,0,.08) 1px, transparent 1px);` +
            `background-size:${step}px ${step}px;`
        );
    }

    get gridStep() {
        return this.unit === "mm" ? 1 : 0.05;
    }

    get snapStep() {
        return this.unit === "mm" ? 0.5 : 0.025;
    }

    // ------------------------------------------------------------------
    // live WYSIWYG render
    // ------------------------------------------------------------------
    /** A cheap fingerprint of everything that changes what the label looks like. */
    get renderSignature() {
        const parts = [this.labelWidth, this.labelHeight, this.unit];
        for (const record of this.elements) {
            const data = record.data;
            parts.push(
                this.key(record),
                data.element_type,
                data.pos_x,
                data.pos_y,
                data.width,
                data.height,
                data.font_size,
                data.font_bold,
                data.text_align,
                data.rotation,
                data.active,
                data.field_path,
                data.text_content,
                data.name
            );
        }
        return parts.join("|");
    }

    scheduleRender() {
        if (!this.state.wysiwyg) {
            return;
        }
        clearTimeout(this.renderTimer);
        // Long enough that typing in the list does not fire a request per key,
        // short enough that a drop feels immediate.
        this.renderTimer = setTimeout(() => this.refreshPreview(), 220);
    }

    async refreshPreview() {
        const templateId = this.props.record.resId;
        if (!this.state.wysiwyg || !templateId) {
            // A template that has never been saved has nothing on the server to
            // render against, so the canvas stays in wireframe until it does.
            return;
        }
        const values = {};
        for (const record of this.elements) {
            if (!record.resId) {
                continue;   // unsaved row: no server-side counterpart yet
            }
            const patch = {};
            for (const name of PATCH_FIELDS) {
                if (name in record.data) {
                    patch[name] = record.data[name];
                }
            }
            values[record.resId] = patch;
        }
        this.state.rendering = true;
        try {
            const result = await this.orm.call(
                "epg.label.template",
                "render_designer",
                [templateId, null, values]
            );
            this.state.rendered = result.elements || {};
            this.state.labelStyle = result.label_style || "";
            // Server-rendered label HTML (values escaped there): mark it safe, or
            // t-out would print the tags as text.
            this.state.background = markup(result.background || "");
            this.state.previewName = result.record_name || "";
        } catch {
            // A render failure is a preview problem, not a data problem: drop back
            // to wireframes rather than blocking the designer.
            this.state.rendered = {};
        } finally {
            this.state.rendering = false;
        }
    }

    renderedHtml(record) {
        return markup(this.state.rendered[record.resId] || "");
    }

    get hasRendered() {
        return this.state.wysiwyg && Object.keys(this.state.rendered).length > 0;
    }

    async toggleWysiwyg() {
        this.state.wysiwyg = !this.state.wysiwyg;
        if (this.state.wysiwyg) {
            await this.refreshPreview();
        }
    }

    // ------------------------------------------------------------------
    // element access
    // ------------------------------------------------------------------
    get elements() {
        return this.list.records;
    }

    /** Layers are listed top-first, the way they stack on the label. */
    get layers() {
        return [...this.elements].sort((a, b) => {
            const za = a.data.z_index || 0;
            const zb = b.data.z_index || 0;
            return zb - za || (b.data.sequence || 0) - (a.data.sequence || 0);
        });
    }

    get selected() {
        const ids = this.state.selectedIds;
        if (ids.length !== 1) {
            return null;
        }
        return this.elements.find((rec) => this.key(rec) === ids[0]) || null;
    }

    get selectedRecords() {
        const ids = new Set(this.state.selectedIds);
        return this.elements.filter((rec) => ids.has(this.key(rec)));
    }

    get hasSelection() {
        return this.state.selectedIds.length > 0;
    }

    get isMultiSelection() {
        return this.state.selectedIds.length > 1;
    }

    key(record) {
        return record.resId || record._virtualId;
    }

    icon(record) {
        return TYPE_ICONS[record.data.element_type] || "fa-square-o";
    }

    /** Live geometry: the in-flight drag values if there is one, else the stored ones. */
    geometry(record) {
        const drag = this.state.dragging;
        if (drag && drag.geometries && this.key(record) in drag.geometries) {
            return drag.geometries[this.key(record)];
        }
        return {
            x: record.data.pos_x || 0,
            y: record.data.pos_y || 0,
            width: record.data.width || 0,
            height: record.data.height || 0,
        };
    }

    /** Built here rather than in the template: a computed key in a QWeb object
     *  literal is not something the expression compiler reliably handles. */
    elementClass(record) {
        const classes = ["epg_designer_element"];
        classes.push(`epg_designer_type_${record.data.element_type}`);
        if (this.isSelected(record)) {
            classes.push("epg_designer_selected");
        }
        if (this.isOutOfBounds(record)) {
            classes.push("epg_designer_oob");
        }
        if (this.hasRendered && this.renderedHtml(record)) {
            classes.push("epg_designer_wysiwyg");
        }
        return classes.join(" ");
    }

    elementStyle(record) {
        const geometry = this.geometry(record);
        const scale = this.scale;
        const rotation = parseInt(record.data.rotation || "0", 10);
        const style = [
            `left:${geometry.x * scale}px`,
            `top:${geometry.y * scale}px`,
            `width:${Math.max(geometry.width * scale, 3)}px`,
            `height:${Math.max(geometry.height * scale, 3)}px`,
        ];
        if (rotation) {
            style.push(`transform:rotate(${rotation}deg)`);
        }
        if (!record.data.active) {
            style.push("opacity:.35");
        }
        return style.join(";") + ";";
    }

    labelFor(record) {
        return record.data.name || _t("Element");
    }

    isSelected(record) {
        return this.state.selectedIds.includes(this.key(record));
    }

    isOutOfBounds(record) {
        const geometry = this.geometry(record);
        return (
            geometry.x < -0.001 ||
            geometry.y < -0.001 ||
            geometry.x + geometry.width > this.labelWidth + 0.001 ||
            geometry.y + geometry.height > this.labelHeight + 0.001
        );
    }

    get outOfBoundsCount() {
        return this.elements.filter((rec) => this.isOutOfBounds(rec)).length;
    }

    // ------------------------------------------------------------------
    // selection
    // ------------------------------------------------------------------
    select(record, additive = false) {
        const id = this.key(record);
        if (!additive) {
            this.state.selectedIds = [id];
            return;
        }
        const index = this.state.selectedIds.indexOf(id);
        if (index >= 0) {
            this.state.selectedIds.splice(index, 1);
        } else {
            this.state.selectedIds.push(id);
        }
    }

    selectAll() {
        this.state.selectedIds = this.elements.map((rec) => this.key(rec));
    }

    clearSelection() {
        this.state.selectedIds = [];
    }

    // ------------------------------------------------------------------
    // undo / redo
    // ------------------------------------------------------------------
    /**
     * Snapshot the geometry of every element before a mutating gesture.
     *
     * Only geometry is tracked: additions and deletions go through the x2many
     * list, which the form's own discard already covers, and a half-undo that
     * resurrects a deleted row without its data is worse than no undo.
     */
    snapshot() {
        const state = {};
        for (const record of this.elements) {
            state[this.key(record)] = {
                pos_x: record.data.pos_x,
                pos_y: record.data.pos_y,
                width: record.data.width,
                height: record.data.height,
            };
        }
        this.undoStack.push(state);
        if (this.undoStack.length > 40) {
            this.undoStack.shift();
        }
        this.redoStack = [];
    }

    get canUndo() {
        return this.undoStack.length > 0;
    }

    get canRedo() {
        return this.redoStack.length > 0;
    }

    async applySnapshot(state) {
        for (const record of this.elements) {
            const values = state[this.key(record)];
            if (!values) {
                continue;
            }
            const changed = ["pos_x", "pos_y", "width", "height"].some(
                (name) => record.data[name] !== values[name]
            );
            if (changed) {
                await record.update({ ...values });
            }
        }
    }

    async undo() {
        const state = this.undoStack.pop();
        if (!state) {
            return;
        }
        const current = {};
        for (const record of this.elements) {
            current[this.key(record)] = {
                pos_x: record.data.pos_x,
                pos_y: record.data.pos_y,
                width: record.data.width,
                height: record.data.height,
            };
        }
        this.redoStack.push(current);
        await this.applySnapshot(state);
    }

    async redo() {
        const state = this.redoStack.pop();
        if (!state) {
            return;
        }
        this.undoStack.push(state);
        await this.applySnapshot(state);
    }

    // ------------------------------------------------------------------
    // smart guides
    // ------------------------------------------------------------------
    /**
     * Alignment targets contributed by everything that is *not* moving: the
     * label's own edges and centre, plus the edges and centres of the other
     * elements.  Lining a price up with the name above it is the single most
     * common thing a designer does, and eyeballing it at 8× zoom never quite
     * works.
     */
    guideTargets(movingIds) {
        const vertical = [
            { at: 0, kind: "label" },
            { at: this.labelWidth / 2, kind: "label" },
            { at: this.labelWidth, kind: "label" },
        ];
        const horizontal = [
            { at: 0, kind: "label" },
            { at: this.labelHeight / 2, kind: "label" },
            { at: this.labelHeight, kind: "label" },
        ];
        for (const record of this.elements) {
            if (movingIds.has(this.key(record)) || !record.data.active) {
                continue;
            }
            const box = this.geometry(record);
            vertical.push(
                { at: box.x, kind: "element" },
                { at: box.x + box.width / 2, kind: "element" },
                { at: box.x + box.width, kind: "element" }
            );
            horizontal.push(
                { at: box.y, kind: "element" },
                { at: box.y + box.height / 2, kind: "element" },
                { at: box.y + box.height, kind: "element" }
            );
        }
        return { vertical, horizontal };
    }

    /**
     * Nudge a moving box onto the nearest guide on each axis.
     * Returns the corrected offsets and the guides that were hit.
     */
    applyGuides(box, targets) {
        const tolerance = GUIDE_THRESHOLD_PX / this.scale;
        const hits = [];
        let { x, y } = box;

        const axes = [
            {
                anchors: [box.x, box.x + box.width / 2, box.x + box.width],
                targets: targets.vertical,
                orientation: "v",
            },
            {
                anchors: [box.y, box.y + box.height / 2, box.y + box.height],
                targets: targets.horizontal,
                orientation: "h",
            },
        ];
        for (const axis of axes) {
            let best = null;
            for (const [index, anchor] of axis.anchors.entries()) {
                for (const target of axis.targets) {
                    const distance = Math.abs(anchor - target.at);
                    if (distance <= tolerance && (!best || distance < best.distance)) {
                        best = { distance, delta: target.at - anchor, at: target.at, index };
                    }
                }
            }
            if (best) {
                if (axis.orientation === "v") {
                    x += best.delta;
                } else {
                    y += best.delta;
                }
                hits.push({ orientation: axis.orientation, at: best.at });
            }
        }
        return { x, y, hits };
    }

    guideStyle(guide) {
        const scale = this.scale;
        if (guide.orientation === "v") {
            return `left:${guide.at * scale}px;top:0;width:1px;height:100%;`;
        }
        return `top:${guide.at * scale}px;left:0;height:1px;width:100%;`;
    }

    // ------------------------------------------------------------------
    // pointer gestures
    // ------------------------------------------------------------------
    round(value) {
        if (!this.state.snap) {
            return Math.round(value * 1000) / 1000;
        }
        const step = this.snapStep;
        return Math.round(value / step) * step;
    }

    onElementPointerDown(record, event, handle) {
        if (this.props.readonly) {
            return;
        }
        event.stopPropagation();
        event.preventDefault();
        if (!this.isSelected(record)) {
            this.select(record, event.shiftKey || event.ctrlKey || event.metaKey);
        } else if (event.shiftKey || event.ctrlKey || event.metaKey) {
            this.select(record, true);
            return;
        }
        this.snapshot();

        // A resize drives one element; a move carries the whole selection.
        const moving = handle ? [record] : this.selectedRecords;
        const origins = {};
        const geometries = {};
        for (const target of moving) {
            const box = { ...this.geometry(target) };
            origins[this.key(target)] = box;
            geometries[this.key(target)] = { ...box };
        }
        this.pointer = {
            leadId: this.key(record),
            records: moving,
            handle: handle || null,
            startX: event.clientX,
            startY: event.clientY,
            origins,
        };
        this.state.dragging = { geometries };
        this.state.activeGuides = [];
        event.target.setPointerCapture?.(event.pointerId);
        this.pointer.pointerId = event.pointerId;
        this.pointer.target = event.target;
    }

    onPointerMove(event) {
        if (this.state.marquee) {
            return this.onMarqueeMove(event);
        }
        const pointer = this.pointer;
        if (!pointer) {
            return;
        }
        const scale = this.scale;
        let dx = (event.clientX - pointer.startX) / scale;
        let dy = (event.clientY - pointer.startY) / scale;
        const movingIds = new Set(pointer.records.map((rec) => this.key(rec)));
        const geometries = {};

        if (!pointer.handle) {
            const lead = pointer.origins[pointer.leadId];
            let x = this.round(lead.x + dx);
            let y = this.round(lead.y + dy);
            let hits = [];
            if (this.state.guides) {
                const corrected = this.applyGuides(
                    { x, y, width: lead.width, height: lead.height },
                    this.guideTargets(movingIds)
                );
                x = corrected.x;
                y = corrected.y;
                hits = corrected.hits;
            }
            this.state.activeGuides = hits;
            // Everything in the selection moves by the same corrected delta, so a
            // multi-selection keeps its internal spacing while the lead snaps.
            dx = x - lead.x;
            dy = y - lead.y;
            for (const record of pointer.records) {
                const origin = pointer.origins[this.key(record)];
                geometries[this.key(record)] = this.clean({
                    x: origin.x + dx,
                    y: origin.y + dy,
                    width: origin.width,
                    height: origin.height,
                });
            }
        } else {
            const origin = pointer.origins[pointer.leadId];
            let { x, y, width, height } = origin;
            const handle = pointer.handle;
            if (handle.includes("w")) {
                const right = origin.x + origin.width;
                x = Math.min(this.round(origin.x + dx), right - this.snapStep);
                width = right - x;
            }
            if (handle.includes("e")) {
                width = Math.max(this.round(origin.width + dx), this.snapStep);
            }
            if (handle.includes("n")) {
                const bottom = origin.y + origin.height;
                y = Math.min(this.round(origin.y + dy), bottom - this.snapStep);
                height = bottom - y;
            }
            if (handle.includes("s")) {
                height = Math.max(this.round(origin.height + dy), this.snapStep);
            }
            // Shift keeps the aspect ratio, the way every drawing tool does.
            if (event.shiftKey && origin.width && origin.height && handle.length === 2) {
                const ratio = origin.width / origin.height;
                height = Math.max(this.snapStep, width / ratio);
            }
            this.state.activeGuides = [];
            geometries[pointer.leadId] = this.clean({ x, y, width, height });
        }
        this.state.dragging = { geometries };
    }

    clean(box) {
        return {
            x: Math.round(box.x * 1000) / 1000,
            y: Math.round(box.y * 1000) / 1000,
            width: Math.round(box.width * 1000) / 1000,
            height: Math.round(box.height * 1000) / 1000,
        };
    }

    async onPointerUp() {
        if (this.state.marquee) {
            return this.onMarqueeUp();
        }
        const pointer = this.pointer;
        const dragging = this.state.dragging;
        this.pointer = null;
        this.state.activeGuides = [];
        if (!pointer || !dragging) {
            this.state.dragging = null;
            return;
        }
        pointer.target?.releasePointerCapture?.(pointer.pointerId);
        this.state.dragging = null;
        let touched = false;
        for (const record of pointer.records) {
            const geometry = dragging.geometries[this.key(record)];
            const origin = pointer.origins[this.key(record)];
            if (!geometry) {
                continue;
            }
            const unchanged =
                geometry.x === origin.x &&
                geometry.y === origin.y &&
                geometry.width === origin.width &&
                geometry.height === origin.height;
            if (unchanged) {
                continue;
            }
            touched = true;
            await record.update({
                pos_x: geometry.x,
                pos_y: geometry.y,
                width: geometry.width,
                height: geometry.height,
            });
        }
        if (!touched) {
            this.undoStack.pop();   // nothing moved, so nothing to undo
        }
    }

    // ------------------------------------------------------------------
    // marquee selection
    // ------------------------------------------------------------------
    onCanvasPointerDown(event) {
        if (this.props.readonly || event.target !== event.currentTarget) {
            return;
        }
        const rect = event.currentTarget.getBoundingClientRect();
        this.state.marquee = {
            startX: event.clientX - rect.left,
            startY: event.clientY - rect.top,
            x: event.clientX - rect.left,
            y: event.clientY - rect.top,
            width: 0,
            height: 0,
            additive: event.shiftKey,
            rect,
        };
        if (!event.shiftKey) {
            this.clearSelection();
        }
    }

    onMarqueeMove(event) {
        const marquee = this.state.marquee;
        const currentX = event.clientX - marquee.rect.left;
        const currentY = event.clientY - marquee.rect.top;
        this.state.marquee = {
            ...marquee,
            x: Math.min(marquee.startX, currentX),
            y: Math.min(marquee.startY, currentY),
            width: Math.abs(currentX - marquee.startX),
            height: Math.abs(currentY - marquee.startY),
        };
    }

    onMarqueeUp() {
        const marquee = this.state.marquee;
        this.state.marquee = null;
        if (!marquee || marquee.width < 4 || marquee.height < 4) {
            return;
        }
        const scale = this.scale;
        const box = {
            x: marquee.x / scale,
            y: marquee.y / scale,
            right: (marquee.x + marquee.width) / scale,
            bottom: (marquee.y + marquee.height) / scale,
        };
        const caught = this.elements.filter((record) => {
            const geometry = this.geometry(record);
            // Intersection, not containment: a rubber band that only catches what
            // it fully encloses is maddening on a crowded label.
            return (
                geometry.x < box.right &&
                geometry.x + geometry.width > box.x &&
                geometry.y < box.bottom &&
                geometry.y + geometry.height > box.y
            );
        });
        const ids = caught.map((rec) => this.key(rec));
        this.state.selectedIds = marquee.additive
            ? [...new Set([...this.state.selectedIds, ...ids])]
            : ids;
    }

    get marqueeStyle() {
        const marquee = this.state.marquee;
        if (!marquee) {
            return "display:none;";
        }
        return `left:${marquee.x}px;top:${marquee.y}px;width:${marquee.width}px;height:${marquee.height}px;`;
    }

    onCanvasClick(event) {
        if (event.target === event.currentTarget && !this.state.marquee) {
            this.clearSelection();
        }
    }

    async onKeyDown(event) {
        if (this.props.readonly) {
            return;
        }
        const ctrl = event.ctrlKey || event.metaKey;
        if (ctrl && event.key.toLowerCase() === "z") {
            event.preventDefault();
            return event.shiftKey ? this.redo() : this.undo();
        }
        if (ctrl && event.key.toLowerCase() === "y") {
            event.preventDefault();
            return this.redo();
        }
        if (ctrl && event.key.toLowerCase() === "a") {
            event.preventDefault();
            return this.selectAll();
        }
        if (ctrl && event.key.toLowerCase() === "d") {
            event.preventDefault();
            return this.duplicateSelected();
        }
        if (event.key === "Escape") {
            return this.clearSelection();
        }
        if (!this.hasSelection) {
            return;
        }
        const nudge = event.shiftKey ? this.snapStep * 10 : this.snapStep;
        const moves = {
            ArrowLeft: [-nudge, 0],
            ArrowRight: [nudge, 0],
            ArrowUp: [0, -nudge],
            ArrowDown: [0, nudge],
        };
        if (moves[event.key]) {
            event.preventDefault();
            const [dx, dy] = moves[event.key];
            this.snapshot();
            for (const record of this.selectedRecords) {
                await record.update({
                    pos_x: Math.round((record.data.pos_x + dx) * 1000) / 1000,
                    pos_y: Math.round((record.data.pos_y + dy) * 1000) / 1000,
                });
            }
        } else if (event.key === "Delete") {
            event.preventDefault();
            await this.deleteSelected();
        }
    }

    // ------------------------------------------------------------------
    // toolbar
    // ------------------------------------------------------------------
    togglePalette() {
        this.state.showPalette = !this.state.showPalette;
    }

    toggleLayers() {
        this.state.showLayers = !this.state.showLayers;
    }

    paletteFor(group) {
        return this.palette.filter((entry) => entry.group === group.key);
    }

    async addElement(entry) {
        this.state.showPalette = false;
        const factor = this.unit === "in" ? 1 / 25.4 : 1;
        // Drop it just inside the top-left corner, or below whatever is selected,
        // so a burst of additions does not stack on one spot.
        const previous = this.selected;
        const geometry = previous ? this.geometry(previous) : null;
        const x = geometry ? geometry.x : 1 * factor;
        const y = geometry ? geometry.y + geometry.height + 0.5 * factor : 1 * factor;
        const record = await this.list.addNewRecord({
            position: "bottom",
            mode: "readonly",
            context: {
                default_element_type: entry.type,
                default_name: entry.label.toString(),
                default_pos_x: Math.round(x * 1000) / 1000,
                default_pos_y: Math.round(Math.min(y, this.labelHeight - 1 * factor) * 1000) / 1000,
                default_width: Math.round(entry.w * factor * 1000) / 1000,
                default_height: Math.round(entry.h * factor * 1000) / 1000,
            },
        });
        if (record) {
            this.state.selectedIds = [this.key(record)];
        }
    }

    async deleteSelected() {
        const records = this.selectedRecords;
        if (!records.length) {
            return;
        }
        this.clearSelection();
        for (const record of records) {
            await this.list.delete(record);
        }
    }

    async duplicateSelected() {
        const records = this.selectedRecords;
        if (!records.length) {
            return;
        }
        const factor = this.unit === "in" ? 1 / 25.4 : 1;
        const created = [];
        for (const record of records) {
            const source = record.data;
            const copy = await this.list.addNewRecord({
                position: "bottom",
                mode: "readonly",
                context: {
                    default_element_type: source.element_type,
                    default_name: source.name,
                    default_pos_x: Math.round((source.pos_x + 2 * factor) * 1000) / 1000,
                    default_pos_y: Math.round((source.pos_y + 2 * factor) * 1000) / 1000,
                    default_width: source.width,
                    default_height: source.height,
                    default_font_size: source.font_size,
                    default_font_bold: source.font_bold,
                    default_text_align: source.text_align,
                    default_field_path: source.field_path,
                    default_text_content: source.text_content,
                    default_rotation: source.rotation,
                    default_z_index: source.z_index,
                },
            });
            if (copy) {
                created.push(this.key(copy));
            }
        }
        this.state.selectedIds = created;
    }

    async openSelected() {
        const record = this.selected || this.selectedRecords[0];
        if (!record) {
            return;
        }
        await this._openRecord({ record, context: this.props.context });
    }

    // ------------------------------------------------------------------
    // align, distribute, order
    // ------------------------------------------------------------------
    /**
     * With one element selected, align to the label.  With several, align to the
     * selection's own bounding box - which is what you mean when you have picked
     * three captions and want their left edges to agree.
     */
    async alignSelected(how) {
        const records = this.selectedRecords;
        if (!records.length) {
            return;
        }
        this.snapshot();
        const boxes = records.map((rec) => this.geometry(rec));
        const bounds = this.isMultiSelection
            ? {
                  left: Math.min(...boxes.map((b) => b.x)),
                  right: Math.max(...boxes.map((b) => b.x + b.width)),
                  top: Math.min(...boxes.map((b) => b.y)),
                  bottom: Math.max(...boxes.map((b) => b.y + b.height)),
              }
            : { left: 0, right: this.labelWidth, top: 0, bottom: this.labelHeight };

        for (const record of records) {
            const geometry = this.geometry(record);
            const values = {};
            if (how === "left") {
                values.pos_x = this.fix(bounds.left);
            } else if (how === "right") {
                values.pos_x = this.fix(bounds.right - geometry.width);
            } else if (how === "hcenter") {
                values.pos_x = this.fix(
                    (bounds.left + bounds.right) / 2 - geometry.width / 2
                );
            } else if (how === "top") {
                values.pos_y = this.fix(bounds.top);
            } else if (how === "bottom") {
                values.pos_y = this.fix(bounds.bottom - geometry.height);
            } else if (how === "vcenter") {
                values.pos_y = this.fix(
                    (bounds.top + bounds.bottom) / 2 - geometry.height / 2
                );
            } else if (how === "stretch") {
                values.pos_x = this.fix(bounds.left);
                values.width = this.fix(bounds.right - bounds.left);
            }
            await record.update(values);
        }
    }

    /** Space three or more elements evenly along an axis. */
    async distributeSelected(axis) {
        const records = this.selectedRecords;
        if (records.length < 3) {
            this.notification.add(
                _t("Select at least three elements to distribute them."),
                { type: "warning" }
            );
            return;
        }
        this.snapshot();
        const horizontal = axis === "h";
        const sorted = [...records].sort((a, b) =>
            horizontal
                ? this.geometry(a).x - this.geometry(b).x
                : this.geometry(a).y - this.geometry(b).y
        );
        const first = this.geometry(sorted[0]);
        const last = this.geometry(sorted[sorted.length - 1]);
        const start = horizontal ? first.x : first.y;
        const end = horizontal ? last.x : last.y;
        const step = (end - start) / (sorted.length - 1);
        for (const [index, record] of sorted.entries()) {
            if (index === 0 || index === sorted.length - 1) {
                continue;
            }
            const position = this.fix(start + step * index);
            await record.update(horizontal ? { pos_x: position } : { pos_y: position });
        }
    }

    /** Give every selected element the lead element's width and/or height. */
    async matchSize(what) {
        const records = this.selectedRecords;
        if (records.length < 2) {
            return;
        }
        this.snapshot();
        const reference = this.geometry(records[0]);
        for (const record of records.slice(1)) {
            const values = {};
            if (what !== "height") {
                values.width = this.fix(reference.width);
            }
            if (what !== "width") {
                values.height = this.fix(reference.height);
            }
            await record.update(values);
        }
    }

    async changeLayer(record, delta) {
        await record.update({ z_index: Math.max(0, (record.data.z_index || 1) + delta) });
    }

    async toggleVisible(record) {
        await record.update({ active: !record.data.active });
    }

    fix(value) {
        return Math.round(value * 1000) / 1000;
    }

    // ------------------------------------------------------------------
    // view controls
    // ------------------------------------------------------------------
    zoomIn() {
        this.state.zoom = Math.min(this.scale * 1.25, this.unit === "mm" ? 40 : 1000);
    }

    zoomOut() {
        this.state.zoom = Math.max(this.scale / 1.25, this.unit === "mm" ? 1.5 : 40);
    }

    zoomFit() {
        this.state.zoom = 0;
    }

    toggleSnap() {
        this.state.snap = !this.state.snap;
    }

    toggleGrid() {
        this.state.grid = !this.state.grid;
    }

    toggleGuides() {
        this.state.guides = !this.state.guides;
    }

    // ------------------------------------------------------------------
    // quick property strip
    // ------------------------------------------------------------------
    async updateSelected(field, event) {
        const record = this.selected;
        if (!record) {
            return;
        }
        const raw = event.target.type === "checkbox" ? event.target.checked : event.target.value;
        const value = event.target.type === "number" ? parseFloat(raw) || 0 : raw;
        await record.update({ [field]: value });
    }

    formatNumber(value) {
        return Math.round((value || 0) * 1000) / 1000;
    }
}

export const labelDesignerField = {
    ...x2ManyField,
    component: LabelDesignerField,
    displayName: _t("Label Designer"),
    supportedTypes: ["one2many"],
};

registry.category("fields").add("epg_label_designer", labelDesignerField);
