/** @odoo-module **/

import { Component, onWillUnmount, useRef, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";

// How long the confirm button must be held. Long enough that no tap, double-tap or
// sleeve can reach it; short enough that somebody who means it does not wonder
// whether the screen is broken. Measured on the bench: 1.2s reads as deliberate.
const HOLD_MS = 1200;

/**
 * "Start this case again" — the one destructive act on the Station Board.
 *
 * A redo throws away every bench's work on a case and sends it back to the first
 * step. The people using this screen are technicians with gloves on and little
 * software experience, and the cost of an accidental redo is a whole appliance's
 * work plus a case that now reads as having failed. So the dialog is built to be
 * hard to do by accident and easy to do on purpose:
 *
 *   1. it opens from a quiet icon, never from a button beside Accept or Hand on;
 *   2. it names the case — patient, clinic and MO — so a wrong card is visible
 *      before anything happens;
 *   3. it says in plain words what will be lost, and how many operations that is;
 *   4. it will not arm until a reason has been chosen from the lab's own list;
 *   5. the confirm must be HELD, not tapped, and the button fills as it is held.
 *      Letting go early cancels and says so.
 *
 * (client, 2026-08-29)
 */
export class RestartDialog extends Component {
    static template = "lab_workcenter_scan.RestartDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        job: Object,            // the card
        reasons: Array,         // [{id, name, station}]
        onConfirm: Function,    // async (reasonId, note) => void
    };

    setup() {
        this.state = useState({
            reasonId: false,
            note: "",
            holding: false,
            progress: 0,
            busy: false,
            slipped: false,     // they let go early — say so rather than doing nothing
        });
        this.noteRef = useRef("note");
        this.timer = null;
        this.tick = null;
        onWillUnmount(() => this.stopHold(false));
    }

    get reason() {
        return this.props.reasons.find((r) => r.id === this.state.reasonId);
    }

    get armed() {
        return !!this.state.reasonId && !this.state.busy;
    }

    get holdLabel() {
        if (this.state.busy) {
            return _t("Starting it again…");
        }
        if (!this.state.reasonId) {
            return _t("Choose a reason first");
        }
        return _t("Hold to start %s again", this.props.job.production || this.props.job.name);
    }

    pickReason(reason) {
        this.state.reasonId = reason.id;
        this.state.slipped = false;
    }

    // ------------------------------------------------------------- hold to confirm
    startHold() {
        if (!this.armed || this.state.holding) {
            return;
        }
        this.state.holding = true;
        this.state.slipped = false;
        this.state.progress = 0;
        const started = performance.now();
        this.tick = setInterval(() => {
            this.state.progress = Math.min(
                100, ((performance.now() - started) / HOLD_MS) * 100);
        }, 40);
        this.timer = setTimeout(() => this.fire(), HOLD_MS);
    }

    stopHold(announce = true) {
        const wasHolding = this.state.holding;
        clearTimeout(this.timer);
        clearInterval(this.tick);
        this.timer = this.tick = null;
        this.state.holding = false;
        // Only complain when they had actually started and let go before the end;
        // a plain click that never held long enough must still say why nothing happened.
        if (announce && wasHolding && this.state.progress < 100 && !this.state.busy) {
            this.state.slipped = true;
        }
        this.state.progress = 0;
    }

    onKeyDown(ev) {
        if (ev.key === " " || ev.key === "Enter") {
            ev.preventDefault();
            this.startHold();
        }
    }

    onKeyUp(ev) {
        if (ev.key === " " || ev.key === "Enter") {
            this.stopHold();
        }
    }

    async fire() {
        clearInterval(this.tick);
        this.tick = null;
        this.state.holding = false;
        this.state.progress = 100;
        this.state.busy = true;
        try {
            await this.props.onConfirm(this.state.reasonId, this.state.note);
            this.props.close();
        } catch (error) {
            this.state.busy = false;
            this.state.progress = 0;
            throw error;
        }
    }
}
