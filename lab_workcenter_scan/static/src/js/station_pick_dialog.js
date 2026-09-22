/** @odoo-module **/

import { Component, useState, onMounted, useRef } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";

/**
 * Which bench am I standing at?
 *
 * The board used to ask that with a bare dropdown of names, and this floor has
 * four Wire Bending benches whose names differ only in their last two words -
 * so on a shared terminal the wrong one got picked, and nothing on the screen
 * said so afterwards. The question is now asked in the language of the floor:
 * the code printed on the bench, how deep its queue is right now, and a colour
 * that stays with that bench everywhere it appears. Typing filters; 1-9 picks.
 * (client, 2026-09-10)
 */
export class StationPickDialog extends Component {
    static template = "lab_workcenter_scan.StationPickDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        stations: Array,        // [{id, name, code, queue, is_lead, people}]
        currentId: { type: [Number, Boolean], optional: true },
        // "today", or the day the board is being read for: the finished count
        // on each card belongs to it. (client, 2026-09-10)
        dayLabel: { type: String, optional: true },
        onPick: Function,       // async (stationId) => void
    };

    setup() {
        this.state = useState({ query: "", busy: false });
        this.searchRef = useRef("search");
        onMounted(() => this.searchRef.el && this.searchRef.el.focus());
    }

    get title() {
        const day = this.props.dayLabel;
        return day && day !== "today"
            ? _t("Which station are you at? (%s)", day)
            : _t("Which station are you at?");
    }

    get matches() {
        const query = this.state.query.trim().toLowerCase();
        const rows = this.props.stations;
        if (!query) {
            return rows;
        }
        return rows.filter(
            (s) =>
                (s.name || "").toLowerCase().includes(query) ||
                (s.code || "").toLowerCase().includes(query)
        );
    }

    /** The bench's own colour, stable for its id: the same station is the same
     *  hue on the picker, in the header, and on tomorrow's screen. */
    hue(station) {
        return (station.id * 47) % 360;
    }

    /** Two letters, so a tile reads from a step away. */
    initials(name) {
        return (name || "")
            .split(/[\s/]+/)
            .filter(Boolean)
            .slice(0, 2)
            .map((w) => w[0].toUpperCase())
            .join("");
    }

    onKeydown(ev) {
        if (ev.key === "Enter" && this.matches.length === 1) {
            ev.preventDefault();
            this.pick(this.matches[0]);
        }
    }

    async pick(station) {
        if (this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            await this.props.onPick(station.id);
            this.props.close();
        } catch (error) {
            this.state.busy = false;
            throw error;
        }
    }
}
