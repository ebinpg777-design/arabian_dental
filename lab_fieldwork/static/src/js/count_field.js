/** @odoo-module **/

import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

/**
 * A count typed with one thumb at a clinic door: a large number with a
 * minus and a plus beside it. Typing still works (numeric keypad on a
 * phone), the buttons are for the common case of "one more". Never below
 * zero, never above `max` (option, default 999). (client, 2026-09-08)
 */
export class CountField extends Component {
    static template = "lab_fieldwork.CountField";
    static props = {
        ...standardFieldProps,
        max: { type: Number, optional: true },
    };
    static defaultProps = { max: 999 };

    get value() {
        return this.props.record.data[this.props.name] || 0;
    }

    set(raw) {
        const n = Math.round(Number(raw));
        const value = Number.isFinite(n) ? Math.min(this.props.max, Math.max(0, n)) : 0;
        if (value !== this.value) {
            this.props.record.update({ [this.props.name]: value });
        }
    }

    bump(delta) {
        if (!this.props.readonly) {
            this.set(this.value + delta);
        }
    }

    onInput(ev) {
        // An emptied box is someone about to type a new number, not a zero.
        if (ev.target.value !== "") {
            this.set(ev.target.value);
        }
    }

    onChange(ev) {
        this.set(ev.target.value);
        ev.target.value = this.value;
    }
}

export const countField = {
    component: CountField,
    supportedTypes: ["integer"],
    extractProps: ({ options }) => (options?.max ? { max: options.max } : {}),
};

registry.category("fields").add("fw_count", countField);
