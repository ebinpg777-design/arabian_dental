/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";

/**
 * The bench's targets for the day, set where the day is read.
 *
 * A manager at the bench at eight o'clock sets the numbers here instead of
 * walking to a wizard under a menu: one row per person with what they have
 * done so far beside the number, big +/− for a tablet in a glove, and "same
 * for everyone" for the usual morning. A zero takes a target away.
 * (client, 2026-09-10)
 */
export class TargetsDialog extends Component {
    static template = "lab_workcenter_scan.TargetsDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        people: Array,        // [{id, name, day_count, target, is_lead}] - this bench only
        dayLabel: String,     // "today" or "Tue, Sep 8"
        station: String,
        onSave: Function,     // async ({userId: target}) => void
    };

    setup() {
        this.state = useState({
            busy: false,
            all: "",
            values: Object.fromEntries(this.props.people.map((p) => [p.id, p.target || 0])),
        });
    }

    get title() {
        return _t("Targets for %s", this.props.dayLabel);
    }

    get total() {
        return Object.values(this.state.values).reduce((a, b) => a + (parseInt(b, 10) || 0), 0);
    }

    /** Two letters, so a row reads at arm's length. */
    initials(name) {
        return (name || "")
            .split(/\s+/)
            .filter(Boolean)
            .slice(0, 2)
            .map((w) => w[0].toUpperCase())
            .join("");
    }

    setAll() {
        const n = parseInt(this.state.all, 10);
        if (!(n >= 0)) {
            return;
        }
        for (const p of this.props.people) {
            this.state.values[p.id] = n;
        }
    }

    bump(person, delta) {
        const now = parseInt(this.state.values[person.id], 10) || 0;
        this.state.values[person.id] = Math.max(0, now + delta);
    }

    onInput(person, ev) {
        this.state.values[person.id] = Math.max(0, parseInt(ev.target.value, 10) || 0);
    }

    async save() {
        if (this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            await this.props.onSave({ ...this.state.values });
            this.props.close();
        } catch (error) {
            this.state.busy = false;
            throw error;
        }
    }
}
