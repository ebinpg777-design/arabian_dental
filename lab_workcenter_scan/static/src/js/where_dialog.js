/** @odoo-module **/

import { Component } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";

/**
 * "Where is it?" — the answer, and nothing done to the job.
 *
 * A head takes a call about a case and needs one fact: which bench has it. The
 * board could say so before, but only by refusing a scan, which means finding
 * out by trying to move the work. This is the question asked on its own: the
 * station in the largest type on the card, the step and who is carrying it
 * underneath, and a way through to the record for anything more.
 * (client, 2026-09-09)
 */
export class WhereDialog extends Component {
    static template = "lab_workcenter_scan.WhereDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        job: Object,        // what lab.station.locate returned
        open: Function,     // () => open the work order
    };

    get title() {
        return _t("Where is it?");
    }

    /** One line for the state of the step, in the words the bench uses. */
    get standing() {
        const job = this.props.job;
        if (job.state === "done") {
            return _t("Finished at this station.");
        }
        if (job.accepted) {
            return job.technician
                ? _t("On the bench with %s.", job.technician)
                : _t("Accepted, with nobody named yet.");
        }
        return _t("Waiting to be taken.");
    }

    openRecord() {
        this.props.open();
        this.props.close();
    }
}
