/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { toCanvas, decodeBarcodes, JPEG_QUALITY } from "@lab_delivery/js/photo_barcode";
import { scanBarcode } from "@web/core/barcode/barcode_dialog";
import { isBarcodeScannerSupported } from "@web/core/barcode/barcode_video_scanner";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

/**
 * Photograph (or upload) a courier receipt and fill the consignment number and
 * courier from it. Sits on the receipt image field; the sibling fields it fills
 * are named in the widget's options.
 *
 * Nothing here is final until the person taps a candidate or the form is saved:
 * the widget proposes, with a reason for each proposal, and the human decides.
 */
export class CourierReceiptScanField extends Component {
    static template = "lab_delivery.CourierReceiptScanField";
    static props = {
        ...standardFieldProps,
        awbField: { type: String, optional: true },
        courierField: { type: String, optional: true },
        courierNameField: { type: String, optional: true },
        sourceField: { type: String, optional: true },
        dispatchedField: { type: String, optional: true },
    };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({
            busy: false,
            stage: "",
            preview: this.props.record.data[this.props.name] || "",
            candidates: [],
            applied: "",
            courier: null,
            error: "",
            note: "",
            engines: [],
        });
        this.cameraScan = isBarcodeScannerSupported();
    }

    get previewSrc() {
        const value = this.state.preview;
        if (!value) {
            return "";
        }
        return value.startsWith("data:") ? value : `data:image/jpeg;base64,${value}`;
    }

    get chosenCourierId() {
        const field = this.props.courierField;
        const value = field && this.props.record.data[field];
        return value ? value.id : false;
    }

    // ---------------------------------------------------------------- inputs
    async onFile(ev) {
        const file = ev.target.files && ev.target.files[0];
        ev.target.value = "";
        if (!file) {
            return;
        }
        await this.run(async () => {
            this.state.stage = _t("Preparing the photo…");
            const canvas = await toCanvas(file);
            const dataUrl = canvas.toDataURL("image/jpeg", JPEG_QUALITY);
            const b64 = dataUrl.split(",")[1];
            this.state.preview = dataUrl;
            await this.props.record.update({ [this.props.name]: b64 });

            this.state.stage = _t("Reading the barcode…");
            const barcodes = await decodeBarcodes(canvas, { budgetMs: 4000 });

            this.state.stage = _t("Reading the receipt…");
            return this.orm.call("lab.courier.receipt", "read_receipt",
                [b64, barcodes, this.chosenCourierId]);
        });
    }

    /** Live camera: Odoo's own scanner, one barcode, no photo kept. */
    async onLiveScan() {
        let value;
        try {
            value = await scanBarcode(this.env);
        } catch (err) {
            this.notification.add(err.message || String(err), { type: "warning" });
            return;
        }
        if (!value) {
            return;
        }
        await this.run(() => {
            this.state.stage = _t("Checking the number…");
            return this.orm.call("lab.courier.receipt", "read_receipt",
                [false, [value], this.chosenCourierId]);
        });
    }

    // ---------------------------------------------------------------- results
    async run(work) {
        this.state.busy = true;
        this.state.error = "";
        this.state.note = "";
        try {
            const result = await work();
            this.consume(result);
        } catch (err) {
            this.state.error = err.message || String(err);
        } finally {
            this.state.busy = false;
            this.state.stage = "";
        }
    }

    async consume(result) {
        this.state.candidates = result.candidates || [];
        this.state.engines = result.engines || [];
        this.state.courier = result.courier;
        this.state.applied = "";
        if (result.vision_error) {
            this.state.error = result.vision_error;
        }
        if (!this.state.candidates.length) {
            this.state.note = result.vision_enabled
                ? _t("No consignment number could be read. Type it from the receipt.")
                : _t("No barcode found. Type the number, or set up receipt reading under Settings.");
            return;
        }
        if (result.legible === false) {
            this.state.note = _t("The receipt reader was not confident — check the number against the paper.");
        }
        if (result.auto) {
            await this.apply(this.state.candidates[0], result);
        } else {
            this.state.note = this.state.note || _t("Pick the consignment number from the receipt.");
        }
    }

    /** Put a candidate into the form. Tapping another swaps it. */
    async apply(candidate, result) {
        const changes = {};
        if (this.props.awbField) {
            changes[this.props.awbField] = candidate.token;
        }
        const sources = this.state.engines.includes("vision") ? "vision" : "barcode";
        const agree = /barcode/.test(candidate.reason) && /receipt/.test(candidate.reason);
        if (this.props.sourceField) {
            changes[this.props.sourceField] = agree ? "both" : sources;
        }
        // The courier, from the number's shape or the receipt - only when the
        // person has not already chosen one.
        const suggested = (result && result.courier) || this.state.courier;
        if (suggested && !this.chosenCourierId) {
            if (this.props.courierField) {
                changes[this.props.courierField] = {
                    id: suggested.id,
                    display_name: suggested.display_name,
                };
            }
            if (this.props.courierNameField) {
                changes[this.props.courierNameField] = suggested.display_name;
            }
        } else if (candidate.courier_names.length === 1 && this.props.courierNameField
            && !this.props.record.data[this.props.courierNameField]) {
            changes[this.props.courierNameField] = candidate.courier_names[0];
        }
        // A receipt written up the next morning: the booking date on the paper is
        // the dispatch date, not the moment somebody got round to typing it.
        if (result && result.booking_date && this.props.dispatchedField) {
            const today = new Date().toISOString().slice(0, 10);
            if (result.booking_date < today) {
                changes[this.props.dispatchedField] = luxon.DateTime.fromISO(result.booking_date);
            }
        }
        await this.props.record.update(changes);
        this.state.applied = candidate.token;
        if (candidate.clash) {
            this.state.error = _t("%s is already on %s — two parcels cannot share a number.",
                candidate.token, candidate.clash);
        }
    }

    scoreClass(candidate) {
        if (candidate.clash) {
            return "o_crs_bad";
        }
        return candidate.score >= 70 ? "o_crs_good" : candidate.score >= 40 ? "o_crs_mid" : "o_crs_low";
    }
}

export const courierReceiptScanField = {
    component: CourierReceiptScanField,
    displayName: _t("Courier Receipt Scan"),
    supportedTypes: ["binary"],
    extractProps: ({ options }) => ({
        awbField: options.awb,
        courierField: options.courier,
        courierNameField: options.courier_name,
        sourceField: options.source,
        dispatchedField: options.dispatched,
    }),
};

registry.category("fields").add("courier_receipt_scan", courierReceiptScanField);
