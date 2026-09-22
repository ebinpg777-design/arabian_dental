/** @odoo-module **/

import { Component, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

const REFRESH_MS = 90000;

/**
 * The whole floor, for one day.
 *
 * The station board answers "what is on my bench". This answers the two questions
 * that actually improve a lab: where is the work piling up (the live pipeline,
 * each case counted once at the step it is waiting at), and what did the floor
 * DO on a given day - taken, finished, by whom, when, how long. Today unless a
 * date is chosen; the live parts never change with the date, because last
 * Tuesday's queues are gone. (client, 2026-09-09)
 */
export class LabFlow extends Component {
    static template = "lab_workcenter_scan.Flow";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            data: null,
            loading: true,
            refreshing: false,
            // ISO date being read; null until the server says what today is.
            day: null,
        });
        onWillStart(() => this.load());
        // Poll only while the page is visible AND showing today: a board left on a
        // back tab refreshes for nobody, and one reading back last Tuesday must not
        // be dragged to today every ninety seconds.
        this.tick = () => {
            if (!document.hidden && this.state.data?.day?.is_today) {
                this.load(true);
            }
        };
        this.timer = setInterval(this.tick, REFRESH_MS);
        document.addEventListener("visibilitychange", this.tick);
        onWillUnmount(() => {
            clearInterval(this.timer);
            document.removeEventListener("visibilitychange", this.tick);
        });
    }

    /** 21856 is a phone number; 21,856 is a count. */
    fmt(n) {
        return (n || 0).toLocaleString("en-IN");
    }

    /** Bench minutes as something a manager reads: "3h 20m", "45m", "—". */
    hoursOf(minutes) {
        if (!minutes) {
            return "—";
        }
        const h = Math.floor(minutes / 60);
        const m = minutes % 60;
        return h ? _t("%(h)sh %(m)sm", { h, m }) : _t("%sm", m);
    }

    /** Hours as days once they are days. */
    daysOf(hours) {
        if (!hours) {
            return "";
        }
        if (hours < 48) {
            return _t("%sh", hours);
        }
        return _t("%sd", Math.floor(hours / 24));
    }

    /**
     * The stations worth a row: anything with a live queue or any activity on
     * the day, deepest queue first. A bench with nothing standing and nothing
     * done is a line of dashes nobody needs to scan past.
     */
    get activeStations() {
        return (this.state.data?.stations || [])
            .filter((s) => s.here || s.in_day || s.out_day || s.urgent)
            .sort((a, b) => b.here - a.here || b.out_day - a.out_day);
    }

    /** "+12" for a queue that grew, "−4" for one that shrank, "0" for level. */
    signed(n) {
        if (!n) {
            return "0";
        }
        return n > 0 ? `+${n}` : `−${Math.abs(n)}`;
    }

    /** Minutes as a manager reads them: "45 min", "2h 10m", "—". */
    minutesOf(minutes) {
        if (!minutes) {
            return "—";
        }
        if (minutes < 60) {
            return _t("%s min", minutes);
        }
        const h = Math.floor(minutes / 60);
        const m = minutes % 60;
        return m ? _t("%(h)sh %(m)sm", { h, m }) : _t("%sh", h);
    }

    /** Two letters, so a row reads at arm's length. */
    initials(name) {
        return (name || "").split(/\s+/).filter(Boolean).slice(0, 2)
            .map((w) => w[0].toUpperCase()).join("");
    }

    /** A person's finished count against the day's best, for the bar beside it. */
    shareOf(finished) {
        const best = Math.max(...(this.state.data?.people || []).map((p) => p.finished), 0);
        return best ? Math.round((finished || 0) * 100 / best) : 0;
    }

    async openCloseout() {
        const act = await this.orm.call("lab.flow", "action_open_closeout", []);
        this.action.doAction(act);
    }

    async load(quiet) {
        // A slow connection must not let auto-refreshes stack up behind each other.
        // Only the quiet ones yield: a refresh the user actually asked for must never
        // be silently dropped because a background poll happened to be in flight.
        if (this.inFlight && quiet) {
            return;
        }
        this.inFlight = true;
        if (!quiet) {
            this.state.refreshing = !!this.state.data;
            this.state.loading = !this.state.data;
        }
        try {
            const data = await this.orm.call("lab.flow", "get_flow", [this.state.day]);
            this.state.data = data;
            this.state.day = data.day.date;
        } finally {
            this.inFlight = false;
            this.state.loading = false;
            this.state.refreshing = false;
        }
    }

    /** Read another day. The date box and the arrows both land here. */
    setDay(day) {
        if (!day) {
            return;
        }
        const today = this.state.data?.day?.today;
        this.state.day = today && day > today ? today : day;
        this.load();
    }

    stepDay(days) {
        const day = this.state.day;
        if (!day) {
            return;
        }
        const [y, m, d] = day.split("-").map((n) => +n);
        const moved = new Date(y, m - 1, d + days);
        const pad = (n) => String(n).padStart(2, "0");
        this.setDay(`${moved.getFullYear()}-${pad(moved.getMonth() + 1)}-${pad(moved.getDate())}`);
    }

    get rush() {
        return (this.state.data && this.state.data.urgent) || { total: 0, rows: [] };
    }

    /** The rush cases as a list: all of them, or one priority. */
    async openRush(priority) {
        const action = await this.orm.call("lab.flow", "action_open_rush",
                                          [priority || false]);
        this.action.doAction(action);
    }

    /** One case, opened from the rush report. */
    openCase(id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "mrp.production",
            res_id: id,
            views: [[false, "form"]],
        });
    }

    async openStation(station) {
        // Server-built: the drill must hold exactly the live work the number
        // counted, and only the server knows which orders are live.
        const act = await this.orm.call("lab.flow", "action_open_station",
                                        [station.id]);
        this.action.doAction(act);
    }

    /** One person's day, as the work orders it was made of. */
    async openPerson(person) {
        const act = await this.orm.call("lab.flow", "action_open_person",
                                        [person.id, this.state.day]);
        this.action.doAction(act);
    }

    // The number the row already printed, made answerable: which steps were they?
    // The row around this is itself a button, so the click has to be stopped here
    // or the general "their whole day" list opens on top of the answer.
    async openOverTime(person, ev) {
        if (ev && ev.type === "keydown" && ev.key !== "Enter" && ev.key !== " ") {
            return;
        }
        if (ev) {
            ev.stopPropagation();
            ev.preventDefault();
        }
        const act = await this.orm.call("lab.flow", "action_open_person",
                                        [person.id, this.state.day], { over_only: true });
        this.action.doAction(act);
    }

    openJob(row) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "mrp.workorder",
            res_id: row.id,
            views: [[false, "form"]],
        });
    }
}

registry.category("actions").add("lab_flow", LabFlow);
