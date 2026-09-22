/** @odoo-module **/

import { Component, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { useService } from "@web/core/utils/hooks";

/**
 * "Where is it?" — searched the way Track Order searches.
 *
 * Part of the order number, the patient or the doctor, narrowed as it is typed.
 * Each case shows every piece still in the lab: the bench it is at and whether
 * it is waiting, on the bench or passed on — the answer a head needs with a
 * doctor on the phone. Nothing is done to any job. (client, 2026-09-14)
 */
export class FindDialog extends Component {
    static template = "lab_workcenter_scan.FindDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        term: { type: String, optional: true },
        workcenterId: { type: [Number, Boolean], optional: true },
        open: Function,          // (workorderId) => open the work order
    };

    setup() {
        this.orm = useService("orm");
        this.state = useState({
            term: this.props.term || "",
            rows: [],
            busy: false,
            searched: false,
        });
        this.timer = null;
        this.token = 0;
        onWillStart(() => this.search());
        onWillUnmount(() => clearTimeout(this.timer));
    }

    onInput(ev) {
        this.state.term = ev.target.value;
        clearTimeout(this.timer);
        this.timer = setTimeout(() => this.search(), 300);
    }

    /** Newest answer wins: a slow reply must never overwrite a fresher one. */
    async search() {
        const term = this.state.term.trim();
        if (term.length < 2) {
            this.state.rows = [];
            this.state.searched = false;
            return;
        }
        const token = ++this.token;
        this.state.busy = true;
        try {
            const rows = await this.orm.call("lab.station", "find_cases", [
                term,
                this.props.workcenterId || false,
            ]);
            if (token === this.token) {
                this.state.rows = rows;
                this.state.searched = true;
            }
        } finally {
            if (token === this.token) {
                this.state.busy = false;
            }
        }
    }

    standingLabel(piece) {
        return piece.standing === "passed"
            ? "Passed on"
            : piece.standing === "bench"
            ? (piece.technician ? `On the bench · ${piece.technician}` : "On the bench")
            : "Waiting to be taken";
    }

    openPiece(piece) {
        this.props.open(piece.workorder_id);
        this.props.close();
    }
}
