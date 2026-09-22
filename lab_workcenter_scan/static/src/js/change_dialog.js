/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";

/**
 * "Correct this job" — the wrong name, or a card scanned twice.
 *
 * Every one of these three can already be set somewhere on this board, but only
 * while doing something else: a technician is named at the moment a job hands
 * on, a finisher at the moment it is finished, and a status only moves by
 * scanning the job again. What the bench had no way to say was "this is right
 * except for one thing" — the wrong tile was tapped at the hand-over, or the
 * same card went through the scanner twice.
 *
 * So the three are gathered here and corrected deliberately: the job is named
 * first so a wrong card is obvious, nothing is applied until Confirm, and the
 * button stays dead until something has actually been changed — a dialog that
 * can be confirmed without a change is a dialog people confirm by reflex.
 * (client, 2026-09-12)
 */
export class ChangeDialog extends Component {
    static template = "lab_workcenter_scan.ChangeDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        job: Object,             // the card
        people: Array,           // [{id, name, is_lead}]
        status: String,          // 'waiting' | 'bench' | 'passed'
        wantsFinisher: Boolean,
        benchUserId: [Number, Boolean],
        finisherUserId: [Number, Boolean],
        canAssign: Boolean,      // giving work out is the lead's call
        onConfirm: Function,     // async ({benchUserId, finisherUserId, status}) => void
    };

    setup() {
        this.state = useState({
            benchUserId: this.props.benchUserId || false,
            finisherUserId: this.props.finisherUserId || false,
            status: this.props.status,
            busy: false,
            error: "",
        });
    }

    /** The three places a job can stand at its bench, in the board's own words. */
    get statuses() {
        return [
            { key: "waiting", label: "Waiting", hint: "not taken yet" },
            { key: "bench", label: "On the bench", hint: "accepted here" },
            { key: "passed", label: "Passed on", hint: "finished here" },
        ];
    }

    get dirty() {
        return (
            (this.state.benchUserId || false) !== (this.props.benchUserId || false) ||
            (this.state.finisherUserId || false) !== (this.props.finisherUserId || false) ||
            this.state.status !== this.props.status
        );
    }

    pickPerson(id) {
        if (!this.props.canAssign) {
            return;
        }
        // Tapping the name already on the job clears it: the correction is as
        // often "nobody, this was never given out" as it is another name.
        this.state.benchUserId = this.state.benchUserId === id ? false : id;
    }

    pickFinisher(id) {
        this.state.finisherUserId = this.state.finisherUserId === id ? false : id;
    }

    pickStatus(key) {
        this.state.status = key;
    }

    async confirm() {
        if (!this.dirty || this.state.busy) {
            return;
        }
        this.state.busy = true;
        this.state.error = "";
        try {
            await this.props.onConfirm({
                benchUserId: this.state.benchUserId,
                finisherUserId: this.state.finisherUserId,
                status: this.state.status,
            });
            this.props.close();
        } catch (error) {
            // Shown in the dialog rather than behind it: the refusals here are
            // the floor's own rules ("nobody is named", "the next bench has it
            // already"), and the person needs them beside the thing they just
            // chose, not as a toast over a screen that has closed.
            this.state.error =
                error.data?.message || error.message || "That change was refused.";
        } finally {
            this.state.busy = false;
        }
    }
}
