/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";

/**
 * "Cancel this step?" — a job that does not belong at this bench.
 *
 * Only the step at this bench is cancelled; the rest of the case's route stands
 * and the case moves on to its next bench. The job is named first so a wrong
 * card is obvious, and the button stays dead until a reason is written — a
 * cancelled step with no reason is a question nobody can answer later.
 * (client, 2026-09-14)
 */
export class CancelDialog extends Component {
    static template = "lab_workcenter_scan.CancelDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        job: Object,            // the card
        onConfirm: Function,    // async (reason) => void
    };

    setup() {
        this.state = useState({ reason: "", busy: false, error: "" });
    }

    get ready() {
        return !!this.state.reason.trim() && !this.state.busy;
    }

    async confirm() {
        if (!this.ready) {
            return;
        }
        this.state.busy = true;
        this.state.error = "";
        try {
            await this.props.onConfirm(this.state.reason.trim());
            this.props.close();
        } catch (error) {
            // The floor's own refusal, beside the reason just written.
            this.state.error =
                error.data?.message || error.message || "That step could not be cancelled.";
        } finally {
            this.state.busy = false;
        }
    }
}
