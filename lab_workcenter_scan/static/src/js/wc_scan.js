/** @odoo-module **/

import { Component } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";
import { scanBarcode } from "@web/core/barcode/barcode_dialog";
import { Dialog } from "@web/core/dialog/dialog";
import { isBarcodeScannerSupported } from "@web/core/barcode/barcode_video_scanner";

/**
 * "Move by Scan" on a work order.
 *
 * The camera is Odoo's own scanner (ZXing), so this inherits torch, focus and the
 * permission handling rather than reinventing three things that are easy to get subtly
 * wrong on a phone.
 *
 * Where the camera is not available — a desktop terminal, or a browser that refuses
 * because the page is not on HTTPS — it falls back to typing the code. A shop floor
 * cannot be told "the feature needs a different browser"; the label has the code printed
 * on it underneath the QR precisely so it can always be entered by hand.
 */
export class WcScanButton extends Component {
    static template = "lab_workcenter_scan.ScanButton";
    static props = { ...standardWidgetProps };

    setup() {
        this.orm = useService("orm");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
    }

    get canScan() {
        return isBarcodeScannerSupported();
    }

    async onScan() {
        let code;
        try {
            code = await scanBarcode(this.env);
        } catch {
            // Refused, unsupported, or dismissed — all the same to the user standing
            // at the machine: they still need to move the job.
            code = null;
        }
        if (!code) {
            return this.onType();
        }
        await this.apply(code);
    }

    onType() {
        this.dialog.add(WcScanPrompt, {
            confirm: (code) => this.apply(code),
        });
    }

    async apply(code) {
        const record = this.props.record;
        if (record.isDirty) {
            await record.save();
        }
        const result = await this.orm.call(
            "mrp.workorder",
            "move_to_scanned_workcenter",
            [[record.resId], code]
        );
        this.notification.add(result.message, {
            type: result.moved ? "success" : "info",
        });
        if (result.was_running) {
            this.notification.add(
                _t("The timer was stopped — restart it at the new station."),
                { type: "warning" }
            );
        }
        await record.load();
    }
}

/** The typed fallback. Deliberately plain: it is used with gloves on. */
export class WcScanPrompt extends Component {
    static template = "lab_workcenter_scan.ScanPrompt";
    static components = { Dialog };
    static props = { close: Function, confirm: Function };

    setup() {
        this.value = "";
    }

    onInput(ev) {
        this.value = ev.target.value;
    }

    async onConfirm() {
        const code = (this.value || "").trim();
        if (!code) {
            return;
        }
        this.props.close();
        await this.props.confirm(code);
    }

    onKeydown(ev) {
        // A hardware scanner types the code and presses Enter. Without this the wedge
        // fills the box and nothing happens, which reads as the scanner being broken.
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.onConfirm();
        }
    }
}

export const wcScanButton = { component: WcScanButton };
registry.category("view_widgets").add("wc_scan_button", wcScanButton);
