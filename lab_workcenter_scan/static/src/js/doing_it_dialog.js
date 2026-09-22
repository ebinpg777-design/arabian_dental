/** @odoo-module **/

import { Component, onMounted, onWillUnmount, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";

/**
 * "Who did this?" — asked at the one moment it cannot be skipped.
 *
 * A job cannot leave a bench without a name on it, but a rule that only says NO
 * sends the lead back to the card, down to the dropdown, and then back to the
 * button: three taps with gloves on, so the rule gets resented. This dialog is
 * the rule made into a single tap: the warning is a label at the top, the people
 * of the bench are tiles below it, and touching a tile assigns the job AND hands
 * it on. A keyboard bench presses the tile's number. (client, 2026-08-29)
 */
export class DoingItDialog extends Component {
    static template = "lab_workcenter_scan.DoingItDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        job: Object,            // the card (name, production, patient, clinic, next_station)
        people: Array,          // [{id, name, load, is_lead}] — this bench only
        canAssign: Boolean,     // a lead or manager; a technician only gets the label
        message: { type: String, optional: true },
        // 'accepted': the answer only names who is doing it (job stays on the bench);
        // 'handover': the answer names who did it and hands it on;
        // 'finisher': the answer names who FINISHED it and hands it on - asked at a
        // bench whose work centre records the two separately.
        stage: { type: String, optional: true },
        onPick: Function,       // async (userId) => void
    };

    setup() {
        this.state = useState({ busy: false, picked: false });
        // 1..9 on a keyboard bench picks that tile; Escape is the dialog's own.
        this.onKey = (ev) => {
            if (ev.target && ["INPUT", "TEXTAREA", "SELECT"].includes(ev.target.tagName)) {
                return;
            }
            const n = parseInt(ev.key, 10);
            if (n >= 1 && n <= 9 && this.props.people[n - 1] && this.props.canAssign) {
                ev.preventDefault();
                this.pick(this.props.people[n - 1]);
            }
        };
        onMounted(() => window.addEventListener("keydown", this.onKey, true));
        onWillUnmount(() => window.removeEventListener("keydown", this.onKey, true));
    }

    get handsOn() {
        return this.props.stage !== "accepted";
    }

    get isFinisher() {
        return this.props.stage === "finisher";
    }

    get title() {
        if (this.isFinisher) {
            return _t("Who finished this?");
        }
        return this.handsOn ? _t("Which technician did this?") : _t("Which technician?");
    }

    get lead() {
        if (this.isFinisher) {
            return _t("Tap the finishing technician — it may be somebody other than "
                      + "whoever did the work, and one tap hands the job on.");
        }
        return this.handsOn
            ? _t("Tap the technician who did the work — one tap names them and hands it on.")
            : _t("Tap the technician taking it — the job stays on the bench under their name.");
    }

    get warning() {
        return (
            this.props.message ||
            _t("No technician is named on %s. Choose who did the work and it hands on straight away.",
               this.props.job.production || this.props.job.name)
        );
    }

    /** Two letters, so a tile reads at arm's length. */
    initials(name) {
        return (name || "")
            .split(/\s+/)
            .filter(Boolean)
            .slice(0, 2)
            .map((w) => w[0].toUpperCase())
            .join("");
    }

    /** The lightest bench gets a nudge: spreading work is the lead's whole job. */
    get lightestId() {
        const real = this.props.people.filter((p) => typeof p.id === "number");
        if (real.length < 2) {
            return null;
        }
        const min = Math.min(...real.map((p) => p.load || 0));
        const lightest = real.filter((p) => (p.load || 0) === min);
        return lightest.length === 1 ? lightest[0].id : null;
    }

    async pick(person) {
        if (this.state.busy || !this.props.canAssign) {
            return;
        }
        this.state.busy = true;
        this.state.picked = person.id;
        try {
            await this.props.onPick(person.id);
            this.props.close();
        } catch (error) {
            this.state.busy = false;
            this.state.picked = false;
            throw error;
        }
    }
}
