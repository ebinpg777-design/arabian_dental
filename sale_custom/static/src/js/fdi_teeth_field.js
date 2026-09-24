/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
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
    static props = {
        value: { type: String, optional: true },
        readonly: { type: Boolean, optional: true },
        onChange: Function,
        close: Function,
    };

    setup() {
        const parsed = parseTeeth(this.props.value);
        this.state = useState({
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
    static props = { ...standardFieldProps, placeholder: { type: String, optional: true } };

    setup() {
        this.popover = usePopover(FdiTeethChart, { position: "bottom-start" });
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

    openChart(ev) {
        if (this.popover.isOpen) {
            this.popover.close();
            return;
        }
        this.popover.open(ev.currentTarget, {
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
};

registry.category("fields").add("fdi_teeth", fdiTeethField);
