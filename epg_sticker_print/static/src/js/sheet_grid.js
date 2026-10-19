/** @odoo-module **/

import { Component, onMounted, onWillUnmount, proxy, useProps } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

/**
 * The label sheet in the printer tray, drawn as it is: the cells already peeled off
 * are hatched, and the run's stickers are laid on the free cells in reading order,
 * numbered, so the counter sees where each label will land before pressing Print.
 * Click a cell to mark it gone (or back); press and drag to mark a run of them.
 * (client, 2026-10-19: "able to select the cells based on the number of stickers")
 *
 * The field holds the used cells as a JSON list of slot indexes, 0 = top left; the
 * grid's shape and the run's size are read off the wizard record beside it.
 */
export class StickerSheetGrid extends Component {
    static template = "epg_sticker_print.StickerSheetGrid";
    props = useProps({ ...standardFieldProps });

    setup() {
        // while the mouse button is held: the state being painted over the cells
        this.paint = proxy({ to: null });
        this.stopPainting = () => {
            this.paint.to = null;
        };
        onMounted(() => window.addEventListener("mouseup", this.stopPainting));
        onWillUnmount(() => window.removeEventListener("mouseup", this.stopPainting));
    }

    get data() {
        return this.props.record.data;
    }
    get columns() {
        return Math.max(this.data.format_columns || 1, 1);
    }
    get rows() {
        return Math.max(this.data.format_rows || 1, 1);
    }
    get perPage() {
        return this.columns * this.rows;
    }
    get count() {
        return Math.max(this.data.sticker_count || 0, 0);
    }

    /** The cells already gone, as a Set of slot indexes within the sheet. */
    get used() {
        let raw = [];
        try {
            raw = JSON.parse(this.data[this.props.name] || "[]");
        } catch {
            raw = [];
        }
        const used = new Set();
        for (const value of Array.isArray(raw) ? raw : []) {
            const slot = Number(value);
            if (Number.isInteger(slot) && slot >= 0 && slot < this.perPage) {
                used.add(slot);
            }
        }
        return used;
    }

    /** Every cell of the sheet: gone, or the number of the sticker that lands on it. */
    get cells() {
        const used = this.used;
        const cells = [];
        let placed = 0;
        for (let slot = 0; slot < this.perPage; slot++) {
            const isUsed = used.has(slot);
            let number = 0;
            if (!isUsed && placed < this.count) {
                number = ++placed;
            }
            cells.push({ slot, used: isUsed, number });
        }
        return cells;
    }

    get freeCount() {
        return this.perPage - this.used.size;
    }
    get overflow() {
        return Math.max(this.count - this.freeCount, 0);
    }
    get freshSheets() {
        return Math.ceil(this.overflow / this.perPage);
    }

    get summary() {
        const free = this.freeCount;
        const total = this.perPage;
        if (!this.count) {
            return _t("%(free)s of %(total)s labels left on this sheet", { free, total });
        }
        if (this.overflow === 0) {
            return _t("This run takes %(count)s of the %(free)s labels left; %(after)s stay for next time.", {
                count: this.count,
                free,
                after: free - this.count,
            });
        }
        if (free === 0) {
            return _t("Nothing left on this sheet: the %(count)s stickers go on %(sheets)s fresh sheet(s).", {
                count: this.count,
                sheets: this.freshSheets,
            });
        }
        return _t(
            "The %(free)s labels left take the first %(free)s stickers; the other %(rest)s go on %(sheets)s fresh sheet(s).",
            { free, rest: this.overflow, sheets: this.freshSheets }
        );
    }

    get gridStyle() {
        return `grid-template-columns: repeat(${this.columns}, 1fr);`;
    }
    get cellStyle() {
        const w = this.data.format_width_mm || 1;
        const h = this.data.format_height_mm || 1;
        return `aspect-ratio: ${w} / ${h};`;
    }

    cellTitle(cell) {
        const where = _t("Row %(row)s, column %(col)s", {
            row: Math.floor(cell.slot / this.columns) + 1,
            col: (cell.slot % this.columns) + 1,
        });
        if (cell.used) {
            return _t("%(where)s: label already used. Click to mark it free.", { where });
        }
        if (cell.number) {
            return _t("%(where)s: sticker %(n)s prints here. Click to mark the label used.", {
                where,
                n: cell.number,
            });
        }
        return _t("%(where)s: free label. Click to mark it used.", { where });
    }

    set(used) {
        const value = JSON.stringify([...used].sort((a, b) => a - b));
        if (value !== (this.data[this.props.name] || "[]")) {
            this.props.record.update({ [this.props.name]: value });
        }
    }

    apply(slot, to) {
        const used = this.used;
        if (used.has(slot) === to) {
            return;
        }
        if (to) {
            used.add(slot);
        } else {
            used.delete(slot);
        }
        this.set(used);
    }

    paintStart(cell, ev) {
        if (this.props.readonly) {
            return;
        }
        ev.preventDefault();
        const to = !cell.used;
        this.paint.to = to;
        this.apply(cell.slot, to);
    }

    paintOver(cell) {
        if (this.paint.to !== null && !this.props.readonly) {
            this.apply(cell.slot, this.paint.to);
        }
    }

    fresh() {
        this.set(new Set());
    }

    allUsed() {
        this.set(new Set(Array.from({ length: this.perPage }, (_v, i) => i)));
    }

    invert() {
        const used = this.used;
        this.set(new Set(Array.from({ length: this.perPage }, (_v, i) => i).filter((i) => !used.has(i))));
    }
}

export const stickerSheetGrid = {
    component: StickerSheetGrid,
    displayName: _t("Label sheet grid"),
    supportedTypes: ["char"],
};

registry.category("fields").add("sticker_sheet_grid", stickerSheetGrid);
