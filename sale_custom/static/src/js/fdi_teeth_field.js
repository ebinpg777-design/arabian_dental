/** @odoo-module **/

import { Component, onMounted, onWillUnmount, proxy, signal, t, useProps } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { usePopover } from "@web/core/popover/popover_hook";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

/**
 * Teeth in FDI notation, picked off a chart instead of typed.
 *
 * The field stays a plain Char ("11, 21, 22"): the lab's old system stored it
 * that way, the migration writes it that way, and a technician who prefers to
 * type can still type. What the widget adds is the mouth: two arches of numbered
 * teeth, the patient's right on the viewer's left as on any dental chart, where
 * a tap selects a tooth and the text writes itself. Primary (baby) teeth live
 * behind a toggle so the everyday chart is not twice as tall.
 */

// FDI: quadrant digit then position, counted from the midline. Rows are written
// as they are read on a chart - the patient's RIGHT on the left of the screen.
export const ARCHES = [
    { key: "upper", label: _t("Upper"), primary: false,
      right: [18, 17, 16, 15, 14, 13, 12, 11], left: [21, 22, 23, 24, 25, 26, 27, 28] },
    { key: "lower", label: _t("Lower"), primary: false,
      right: [48, 47, 46, 45, 44, 43, 42, 41], left: [31, 32, 33, 34, 35, 36, 37, 38] },
    { key: "upper_primary", label: _t("Upper primary"), primary: true,
      right: [55, 54, 53, 52, 51], left: [61, 62, 63, 64, 65] },
    { key: "lower_primary", label: _t("Lower primary"), primary: true,
      right: [85, 84, 83, 82, 81], left: [71, 72, 73, 74, 75] },
];

const ALL_TEETH = new Set(ARCHES.flatMap((a) => [...a.right, ...a.left]).map(String));

/** "11, 21 , 22 Q1" -> { teeth: ["11", "21", "22"], extras: ["Q1"] } */
export function parseTeeth(value) {
    const teeth = [];
    const extras = [];
    for (const token of String(value || "").split(/[\s,;/]+/)) {
        if (!token) {
            continue;
        }
        if (ALL_TEETH.has(token)) {
            if (!teeth.includes(token)) {
                teeth.push(token);
            }
        } else if (!extras.includes(token)) {
            extras.push(token);
        }
    }
    return { teeth, extras };
}

/** Numbers in chart order (upper right → upper left → lower left → lower right),
    then whatever else was typed, so "11, 21, 22" reads the way a dentist says it. */
export function formatTeeth(teeth, extras = []) {
    const order = new Map();
    let i = 0;
    for (const arch of ARCHES) {
        for (const tooth of [...arch.right, ...arch.left]) {
            order.set(String(tooth), i++);
        }
    }
    const sorted = [...teeth].sort((a, b) => order.get(a) - order.get(b));
    return [...sorted, ...extras].join(", ");
}

export class FdiTeethChart extends Component {
    static template = "sale_custom.FdiTeethChart";
    props = useProps({
        value: t.string().optional(),
        readonly: t.boolean().optional(),
        onChange: t.function(),
        close: t.function(),
    });

    setup() {
        const parsed = parseTeeth(this.props.value);
        this.state = proxy({
            teeth: parsed.teeth,
            extras: parsed.extras.join(", "),
            // the baby-teeth rows open by themselves when one is already chosen
            primary: parsed.teeth.some((t) => t[0] >= "5"),
        });
        this.arches = ARCHES;
    }

    get visibleArches() {
        return this.arches.filter((a) => !a.primary || this.state.primary);
    }

    isSelected(tooth) {
        return this.state.teeth.includes(String(tooth));
    }

    toggle(tooth) {
        if (this.props.readonly) {
            return;
        }
        const key = String(tooth);
        const at = this.state.teeth.indexOf(key);
        if (at >= 0) {
            this.state.teeth.splice(at, 1);
        } else {
            this.state.teeth.push(key);
        }
        this.emit();
    }

    /** Whole arch on, or - when it already is - off. */
    toggleArch(arch) {
        if (this.props.readonly) {
            return;
        }
        const keys = [...arch.right, ...arch.left].map(String);
        const allOn = keys.every((k) => this.state.teeth.includes(k));
        for (const k of keys) {
            const at = this.state.teeth.indexOf(k);
            if (allOn && at >= 0) {
                this.state.teeth.splice(at, 1);
            } else if (!allOn && at < 0) {
                this.state.teeth.push(k);
            }
        }
        this.emit();
    }

    clear() {
        if (this.props.readonly) {
            return;
        }
        this.state.teeth.length = 0;
        this.state.extras = "";
        this.emit();
    }

    onExtrasInput(ev) {
        this.state.extras = ev.target.value;
    }

    onExtrasChange() {
        // anything numbered that was typed into the free box moves onto the chart
        const parsed = parseTeeth(this.state.extras);
        for (const t of parsed.teeth) {
            if (!this.state.teeth.includes(t)) {
                this.state.teeth.push(t);
            }
        }
        this.state.extras = parsed.extras.join(", ");
        this.emit();
    }

    emit() {
        this.props.onChange(formatTeeth(this.state.teeth, parseTeeth(this.state.extras).extras));
    }

    get summary() {
        const n = this.state.teeth.length;
        if (!n) {
            return _t("No tooth selected");
        }
        return n === 1 ? _t("1 tooth") : _t("%s teeth", n);
    }
}

export class FdiTeethField extends Component {
    static template = "sale_custom.FdiTeethField";
    props = useProps({ ...standardFieldProps, placeholder: t.string().optional() });
    rootRef = signal.ref();
    inputRef = signal.ref();

    setup() {
        this.popover = usePopover(FdiTeethChart, {
            position: "bottom-start",
            onClose: () => this.chartClosed(),
        });
        this.handingBack = false;
        onWillUnmount(() => clearTimeout(this.handBackTimer));
        // Entering the cell is entering the mouth: when the list puts the row in
        // edition and hands this field the focus, the chart is already open.
        onMounted(() => {
            if (!this.props.readonly && this.inputRef() && document.activeElement === this.inputRef()) {
                this.openChart();
            }
        });
    }

    get value() {
        return this.props.record.data[this.props.name] || "";
    }

    get parsed() {
        return parseTeeth(this.value);
    }

    get chips() {
        const { teeth, extras } = this.parsed;
        return [...teeth, ...extras];
    }

    /**
     * The teeth as a dentist writes them: a cross, the patient's right on the left
     * of it, upper above the line. Each quadrant reads towards the midline, so the
     * tooth nearest the vertical line is the front one (client, 2026-11-24).
     */
    get cross() {
        const { teeth, extras } = this.parsed;
        const cells = { ur: [], ul: [], lr: [], ll: [] };
        const of = { 1: "ur", 5: "ur", 2: "ul", 6: "ul", 4: "lr", 8: "lr", 3: "ll", 7: "ll" };
        for (const tooth of teeth) {
            cells[of[tooth[0]]].push(tooth);
        }
        // Palmer, as the invoice prints it: the cell already says the quadrant, so a
        // tooth is written by its own number - 7, not 17 - and a milk tooth by its
        // letter A-E (client, 2026-11-24). The full numbers stay in the tooltip.
        const mark = (tooth) => (Number(tooth[0]) >= 5 ? "ABCDE"[Number(tooth[1]) - 1] : tooth[1]);
        const position = (a, b) => Number(a[1]) - Number(b[1]) || Number(a[0]) - Number(b[0]);
        const towardsMidline = (side) => (a, b) => (side === "right" ? position(b, a) : position(a, b));
        // a thin space: a whole arch, 8 7 6 5 4 3 2 1 | 1 2 3 4 5 6 7 8, fits a list column
        const write = (cell, side) => cell.sort(towardsMidline(side)).map(mark).join("\u2009");
        return {
            ur: write(cells.ur, "right"),
            ul: write(cells.ul, "left"),
            lr: write(cells.lr, "right"),
            ll: write(cells.ll, "left"),
            extras: extras.join(", "),
            any: teeth.length > 0,
        };
    }

    /**
     * The chart has just closed - by a click outside it, or Escape.
     *
     * As a popover goes, the web client hands the focus back to whatever had it
     * before the popover opened: this field's own box. The field opens the chart
     * whenever focus arrives, so the chart closed and was open again before the eye
     * could tell - "it does not close when I click outside", and Escape was a trap
     * a keyboard could not leave. Focus that is being handed BACK is not somebody
     * arriving, and for a moment after a close it is not answered.
     * (client, 2026-09-30)
     */
    chartClosed() {
        this.handingBack = true;
        clearTimeout(this.handBackTimer);
        this.handBackTimer = setTimeout(() => {
            this.handingBack = false;
        }, 250);
    }

    onFocusIn(ev) {
        const from = ev.relatedTarget;
        if (this.handingBack || (from && from.closest && from.closest(".o_fdi_chart"))) {
            return;
        }
        this.openChart();
    }

    /** With the chart closed the box is an ordinary box: the down arrow brings the
        chart back, and Backspace in the empty box takes the last tooth off. */
    onInputKeydown(ev) {
        if (ev.key === "ArrowDown" && !this.popover.isOpen) {
            ev.preventDefault();
            this.openChart();
        } else if (ev.key === "Backspace" && !ev.target.value && this.chips.length) {
            ev.preventDefault();
            this.removeChip(this.chips[this.chips.length - 1]);
        }
    }

    openChart() {
        if (this.props.readonly || this.popover.isOpen || !this.rootRef()) {
            return;
        }
        this.popover.open(this.rootRef(), {
            value: this.value,
            readonly: this.props.readonly,
            onChange: (value) => this.update(value),
        });
    }

    async update(value) {
        if (this.props.readonly) {
            return;
        }
        await this.props.record.update({ [this.props.name]: value || false });
    }

    /** Typing still works: what is typed is added to what is there, and the box clears. */
    onInputChange(ev) {
        const typed = parseTeeth(ev.target.value);
        const { teeth, extras } = this.parsed;
        const allTeeth = [...teeth, ...typed.teeth.filter((t) => !teeth.includes(t))];
        const allExtras = [...extras, ...typed.extras.filter((e) => !extras.includes(e))];
        ev.target.value = "";
        this.update(formatTeeth(allTeeth, allExtras));
    }

    removeChip(chip) {
        const { teeth, extras } = this.parsed;
        this.update(formatTeeth(teeth.filter((t) => t !== chip), extras.filter((e) => e !== chip)));
    }
}

export const fdiTeethField = {
    component: FdiTeethField,
    displayName: _t("Teeth (FDI chart)"),
    supportedTypes: ["char"],
    extractProps: ({ attrs }) => ({ placeholder: attrs.placeholder }),
    // Room in a list for the cross of a full arch, so no quadrant ends in "..."
    listViewWidth: [160, 260],
};

registry.category("fields").add("fdi_teeth", fdiTeethField);
