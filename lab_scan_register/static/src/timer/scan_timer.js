/** @odoo-module **/
/**
 * A clock that runs: "1h 07m 32s" since the datetime in the field, ticking every
 * second while the record is on screen. Put on the register's "print started" time
 * so the printing room sees how long a plate has been going without reloading.
 */
import { Component, onWillDestroy, proxy, useProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

const { DateTime } = luxon;

export function elapsedText(start, now = DateTime.now()) {
    if (!start) {
        return "";
    }
    let s = Math.max(0, Math.floor(now.diff(start, "seconds").seconds));
    const h = Math.floor(s / 3600);
    s -= h * 3600;
    const m = Math.floor(s / 60);
    s -= m * 60;
    const mm = String(m).padStart(2, "0");
    const ss = String(s).padStart(2, "0");
    return h ? `${h}h ${mm}m ${ss}s` : `${m}m ${ss}s`;
}

export class ScanTimerField extends Component {
    static template = "lab_scan_register.ScanTimerField";
    props = useProps({ ...standardFieldProps });

    setup() {
        this.state = proxy({ text: "" });
        this.tick();
        this.interval = setInterval(() => this.tick(), 1000);
        onWillDestroy(() => clearInterval(this.interval));
    }

    tick() {
        this.state.text = elapsedText(this.props.record.data[this.props.name]);
    }
}

registry.category("fields").add("scan_timer", {
    component: ScanTimerField,
    displayName: "Running timer",
    supportedTypes: ["datetime"],
});
