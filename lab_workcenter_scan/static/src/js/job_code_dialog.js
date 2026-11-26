/** @odoo-module **/

import { Component, onMounted, proxy, signal, useProps, t } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";

/**
 * "Which job?" — the number, typed or scanned.
 *
 * Correction, Cancel, Start again, Amendment, Pending and Reassign all begin by
 * finding one job. On a phone they went straight to the camera, and a label
 * that would not read left no way to say which job was meant. This asks for
 * the number and offers the camera beside it. (client, 2026-11-26)
 */
export class JobCodeDialog extends Component {
    static template = "lab_workcenter_scan.JobCodeDialog";
    static components = { Dialog };
    props = useProps({
        close: t.function(),
        title: t.string(),
        canScan: t.boolean(),
        onCode: t.function(),   // (code) => void
        onScan: t.function(),   // () => void: open the camera instead
    });

    setup() {
        this.state = proxy({ code: "" });
        this.inputRef = signal.ref();
        onMounted(() => this.inputRef()?.focus());
    }

    go() {
        const code = this.state.code.trim();
        if (code) {
            this.props.onCode(code);
            this.props.close();
        }
    }

    scan() {
        this.props.onScan();
        this.props.close();
    }

    onKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.go();
        }
    }
}
