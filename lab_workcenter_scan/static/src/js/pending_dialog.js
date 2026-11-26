/** @odoo-module **/

import { Component, proxy, useProps, t } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";

/**
 * "Pending" — the job cannot go on for now, and what it is waiting for.
 *
 * The job is named first. Not pending: it asks what the job waits for - the
 * usual answers are one tap, and can be added to - and marks it. Pending: it
 * shows the reason, who said so and when, and offers Resume, or a new reason
 * if what it waits for has changed. A job taken or handed on at a bench stops
 * being pending by itself. (client, 2026-11-26)
 */
export class PendingDialog extends Component {
    static template = "lab_workcenter_scan.PendingDialog";
    static components = { Dialog };
    props = useProps({
        close: t.function(),
        job: t.object(),            // lab.station._pending_card
        onMark: t.function(),       // async (reason) => void
        onResume: t.function(),     // async () => void
    });

    setup() {
        this.state = proxy({ reason: "", busy: "", error: "" });
        // What a job in a dental lab usually waits for. One tap; the words can
        // then be added to.
        this.usual = [
            _t("Waiting for the doctor's reply"),
            _t("Try-in not back from the clinic"),
            _t("Shade to be confirmed"),
            _t("Impression or model not clear"),
            _t("Material not available"),
            _t("Machine not available"),
        ];
    }

    get hasReason() {
        return !!this.state.reason.trim();
    }

    pick(text) {
        this.state.reason = text;
    }

    async run(what, call) {
        if (this.state.busy) {
            return;
        }
        this.state.busy = what;
        this.state.error = "";
        try {
            await call();
            this.props.close();
        } catch (error) {
            this.state.error =
                error.data?.message || error.message || _t("That could not be saved.");
        } finally {
            this.state.busy = "";
        }
    }

    mark() {
        if (this.hasReason) {
            this.run("mark", () => this.props.onMark(this.state.reason.trim()));
        }
    }

    resume() {
        this.run("resume", () => this.props.onResume());
    }
}
