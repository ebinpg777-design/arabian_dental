/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { useDebounced } from "@web/core/utils/timing";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { ARCHES, formatTeeth, parseTeeth } from "@sale_custom/js/fdi_teeth_field";

/**
 * What is being made, entered with a thumb.
 *
 * The editable grid this replaces asked for a dropdown of 1,600 products, a column
 * of tiny cells and the phone keyboard for every row. Here each item is a card, and
 * every choice on it is a coloured target: the arch (blue upper, teal lower), the
 * quantity (amber), the shade (pink), urgent (red). Adding an item opens a full
 * screen picker that starts with what THIS clinic usually orders and what the lab
 * makes most, so the common appliance is one tap and the rare one is one search -
 * and the picker stays open, because a doctor seldom hands over just one thing.
 */
const PALETTE = ["blue", "teal", "violet", "amber", "pink", "green", "indigo", "orange", "cyan", "rose"];

// The mouth as four rows a thumb can hit: a quadrant is eight teeth, and eight fit the
// width of a phone at a size that can be tapped. Read as on any chart - the patient's
// right first, from the back tooth to the midline.
const QUADRANTS = ARCHES.flatMap((arch) => [
    { key: arch.key + "_r", arch: arch.key.startsWith("upper") ? "upper" : "lower", primary: arch.primary,
      name: arch.key.startsWith("upper") ? _t("Upper right") : _t("Lower right"), teeth: arch.right.map(String) },
    { key: arch.key + "_l", arch: arch.key.startsWith("upper") ? "upper" : "lower", primary: arch.primary,
      name: arch.key.startsWith("upper") ? _t("Upper left") : _t("Lower left"), teeth: arch.left.map(String) },
]);
const UPPER_FRONT = ["13", "12", "11", "21", "22", "23"];
const LOWER_FRONT = ["43", "42", "41", "31", "32", "33"];

export class FwCaseItems extends Component {
    static template = "lab_fieldwork.FwCaseItems";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({
            sheet: null,            // null | "product" | "shade" | "teeth"
            teethFor: null, teeth: [], extras: [], primary: false, follow: true,
            query: "", categ: null, catalog: null, loading: false, added: 0, lastAdded: "",
            shadeFor: null, shades: [], shadeQuery: "",
            noteFor: null,
        });
        this.search = useDebounced(() => this.loadCatalog(), 250);
        this.searchShades = useDebounced(() => this.loadShades(), 250);
    }

    // ------------------------------------------------------------ the lines
    get list() {
        return this.props.record.data[this.props.name];
    }
    get readonly() {
        return this.props.readonly;
    }
    get lines() {
        return this.list.records.map((record) => {
            const d = record.data;
            const product = d.product_id || false;
            return {
                record, key: record.id,
                product: product ? product.display_name : "",
                productId: product ? product.id : false,
                ul: d.ul, qty: d.quantity || 0,
                shade: d.colour_id ? d.colour_id.display_name : "",
                teeth: d.teeth || "", teethCount: parseTeeth(d.teeth).teeth.length, cast: d.cast || false,
                urgent: !!d.is_urgent, note: d.note || "",
                tone: this.tone(product ? product.id : 0),
            };
        });
    }
    get summary() {
        const lines = this.lines;
        return {
            items: lines.length,
            units: lines.reduce((t, l) => t + l.qty, 0),
            urgent: lines.filter((l) => l.urgent).length,
            teeth: lines.reduce((t, l) => t + l.teethCount, 0),
        };
    }
    tone(n) {
        return PALETTE[Math.abs(Number(n) || 0) % PALETTE.length];
    }
    has(line, arch) {
        return line.ul === arch || line.ul === "ul";
    }
    archLabel(line) {
        return { upper: _t("Upper"), lower: _t("Lower"), ul: _t("Upper + Lower") }[line.ul] || "";
    }
    qtyLabel(q) {
        return Number.isInteger(q) ? String(q) : q.toFixed(2);
    }

    // ------------------------------------------------------------ editing a line
    toggleArch(line, arch) {
        if (this.readonly) {
            return;
        }
        const upper = arch === "upper" ? !this.has(line, "upper") : this.has(line, "upper");
        const lower = arch === "lower" ? !this.has(line, "lower") : this.has(line, "lower");
        let value;
        if (upper && lower) {
            value = "ul";
        } else if (upper) {
            value = "upper";
        } else if (lower) {
            value = "lower";
        } else {
            // the last arch cannot be switched off: the tap moves to the other one
            value = arch === "upper" ? "lower" : "upper";
        }
        line.record.update({ ul: value });
    }
    step(line, by) {
        if (this.readonly) {
            return;
        }
        line.record.update({ quantity: Math.max(1, (line.qty || 0) + by) });
    }
    onQty(line, ev) {
        const n = parseFloat(String(ev.target.value || "").replace(/[^0-9.]/g, ""));
        const value = Number.isFinite(n) && n > 0 ? n : 1;
        line.record.update({ quantity: value });
        ev.target.value = this.qtyLabel(value);
    }
    toggleUrgent(line) {
        if (!this.readonly) {
            line.record.update({ is_urgent: !line.urgent });
        }
    }
    toggleNote(line) {
        this.state.noteFor = this.state.noteFor === line.key ? null : line.key;
    }
    onNote(line, ev) {
        line.record.update({ note: ev.target.value || false });
    }
    async remove(line) {
        if (this.readonly) {
            return;
        }
        await this.list.delete(line.record);
    }
    async duplicate(line) {
        if (this.readonly || !line.productId) {
            return;
        }
        const other = line.ul === "upper" ? "lower" : line.ul === "lower" ? "upper" : "ul";
        await this.list.addNewRecord({
            position: "bottom", mode: "readonly",
            context: { default_product_id: line.productId, default_ul: other, default_quantity: line.qty || 1,
                       default_colour_id: line.record.data.colour_id ? line.record.data.colour_id.id : false,
                       default_cast: line.cast || false },
        });
    }

    // ------------------------------------------------------------ the cast that came
    setCast(line, cast) {
        if (!this.readonly) {
            // a second tap takes it off: not every case comes with a cast
            line.record.update({ cast: line.cast === cast ? false : cast });
        }
    }

    // ------------------------------------------------------------ the teeth
    get quadrants() {
        return QUADRANTS.filter((q) => !q.primary || this.state.primary);
    }
    get teethLine() {
        return this.lines.find((l) => l.key === this.state.teethFor);
    }
    openTeeth(line) {
        if (this.readonly) {
            return;
        }
        const parsed = parseTeeth(line.teeth);
        this.state.teethFor = line.key;
        this.state.teeth = parsed.teeth;
        this.state.extras = parsed.extras;
        this.state.primary = parsed.teeth.some((t) => t[0] >= "5");
        // the quantity follows the teeth while it still says what the teeth said
        this.state.follow = line.qty === 1 || line.qty === parsed.teeth.length;
        this.state.sheet = "teeth";
    }
    hasTooth(tooth) {
        return this.state.teeth.includes(tooth);
    }
    toggleTooth(tooth) {
        const at = this.state.teeth.indexOf(tooth);
        if (at >= 0) {
            this.state.teeth.splice(at, 1);
        } else {
            this.state.teeth.push(tooth);
        }
    }
    /** Every tooth of the set on - or, when they all are already, off. */
    toggleSet(teeth) {
        const allOn = teeth.every((t) => this.state.teeth.includes(t));
        for (const tooth of teeth) {
            const at = this.state.teeth.indexOf(tooth);
            if (allOn && at >= 0) {
                this.state.teeth.splice(at, 1);
            } else if (!allOn && at < 0) {
                this.state.teeth.push(tooth);
            }
        }
    }
    setOn(teeth) {
        return teeth.length > 0 && teeth.every((t) => this.state.teeth.includes(t));
    }
    get teethSets() {
        const of = (arch) => QUADRANTS.filter((q) => q.arch === arch && !q.primary).flatMap((q) => q.teeth);
        return [
            { key: "uf", name: _t("Upper front 13–23"), teeth: UPPER_FRONT, tone: "blue" },
            { key: "lf", name: _t("Lower front 33–43"), teeth: LOWER_FRONT, tone: "teal" },
            { key: "ua", name: _t("All upper"), teeth: of("upper"), tone: "blue" },
            { key: "la", name: _t("All lower"), teeth: of("lower"), tone: "teal" },
        ];
    }
    get teethText() {
        return formatTeeth(this.state.teeth, this.state.extras);
    }
    /** The arch the chosen teeth are in: it is written on the item with them. */
    get teethArch() {
        const upper = this.state.teeth.some((t) => "1256".includes(t[0]));
        const lower = this.state.teeth.some((t) => "3478".includes(t[0]));
        return upper && lower ? "ul" : upper ? "upper" : lower ? "lower" : false;
    }
    clearTeeth() {
        this.state.teeth = [];
        this.state.extras = [];
    }
    saveTeeth() {
        const line = this.teethLine;
        if (line) {
            const vals = { teeth: this.teethText || false };
            if (this.teethArch) {
                vals.ul = this.teethArch;
            }
            if (this.state.follow && this.state.teeth.length) {
                vals.quantity = this.state.teeth.length;
            }
            line.record.update(vals);
        }
        this.closeSheet();
    }

    // ------------------------------------------------------------ the product picker
    get partnerId() {
        const p = this.props.record.data.partner_id;
        return p ? p.id : false;
    }
    async openPicker() {
        if (this.readonly) {
            return;
        }
        this.state.sheet = "product";
        this.state.query = "";
        this.state.categ = null;
        this.state.added = 0;
        this.state.lastAdded = "";
        await this.loadCatalog();
    }
    async loadCatalog() {
        this.state.loading = true;
        try {
            const res = await this.orm.call("lab.case", "picker_products", [], {
                query: this.state.query, categ_id: this.state.categ, partner_id: this.partnerId,
            });
            // the category chips come with the first, unfiltered read and then stay
            if (this.state.catalog && !res.categories.length) {
                res.categories = this.state.catalog.categories;
            }
            this.state.catalog = res;
        } finally {
            this.state.loading = false;
        }
    }
    onQuery(ev) {
        this.state.query = ev.target.value;
        this.search();
    }
    clearQuery() {
        this.state.query = "";
        this.loadCatalog();
    }
    pickCateg(id) {
        this.state.categ = this.state.categ === id ? null : id;
        this.loadCatalog();
    }
    inCase(productId) {
        return this.lines.filter((l) => l.productId === productId).length;
    }
    async add(product) {
        const lines = this.lines;
        const last = lines[lines.length - 1];
        await this.list.addNewRecord({
            position: "bottom", mode: "readonly",
            context: { default_product_id: product.id, default_ul: last ? last.ul : "upper", default_quantity: 1 },
        });
        this.state.added += 1;
        this.state.lastAdded = product.name;
    }
    closeSheet() {
        this.state.sheet = null;
        this.state.shadeFor = null;
        this.state.teethFor = null;
    }

    // ------------------------------------------------------------ the shade picker
    async openShades(line) {
        if (this.readonly) {
            return;
        }
        this.state.shadeFor = line.key;
        this.state.shadeQuery = "";
        this.state.sheet = "shade";
        await this.loadShades();
    }
    async loadShades() {
        this.state.shades = await this.orm.call("lab.case", "picker_colours", [], { query: this.state.shadeQuery });
    }
    onShadeQuery(ev) {
        this.state.shadeQuery = ev.target.value;
        this.searchShades();
    }
    get shadeLine() {
        return this.lines.find((l) => l.key === this.state.shadeFor);
    }
    pickShade(shade) {
        const line = this.shadeLine;
        if (line) {
            line.record.update({ colour_id: shade ? { id: shade.id, display_name: shade.name } : false });
        }
        this.closeSheet();
    }
}

export const fwCaseItems = {
    component: FwCaseItems,
    displayName: _t("Case items (touch)"),
    supportedTypes: ["one2many"],
    // the inline <list> names the fields to load; the cards replace how it is drawn
    useSubView: true,
};
registry.category("fields").add("fw_case_items", fwCaseItems);
