/** @odoo-module **/

import { Component, onPatched, onWillStart, useRef, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { formatMonetary } from "@web/views/fields/formatters";

/**
 * The executive's whole application.
 *
 * One screen, one list, one button per card. Everything else in the module exists so
 * that this screen can be short.
 */
export class LabMyDay extends Component {
    static template = "lab_fieldwork.MyDay";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({
            track: { open: false, rows: [], busy: false, query: "", more: false,
                     openId: null, details: {}, here: null, partnerId: null,
                     refreshing: null },
            clinics: { open: false, rows: [], busy: false, query: "",
                       more: false, favouritesOnly: false, favourites: 0,
                       noRoute: false, loaded: false },
            day: null, data: null, loading: true, busy: 0 });
        // TAKE THE SCREEN TO THE ANSWER. The clinics panel opens below the day's
        // own work, which on a phone is far enough down that tapping the tile
        // looked like it had done nothing at all. The scroll has to wait for the
        // panel to exist, so it is armed here and spent on the next patch.
        // (client, 2026-09-12)
        this.clinicsPanel = useRef("clinicsPanel");
        // Track order had the same fault: its panel opened below the day's work
        // and the tap looked like nothing. Same answer, and the cursor goes into
        // the search box. (client, 2026-09-15)
        this.trackPanel = useRef("trackPanel");
        this.trackInput = useRef("trackInput");
        onPatched(() => {
            if (this._scrollToClinics) {
                this._scrollToClinics = false;
                this.clinicsPanel.el?.scrollIntoView(
                    { behavior: "smooth", block: "start" });
            }
            if (this._scrollToTrack) {
                this._scrollToTrack = false;
                this.trackPanel.el?.scrollIntoView({ behavior: "smooth", block: "start" });
                this.trackInput.el?.focus({ preventScroll: true });
            }
        });
        onWillStart(() => this.load());
    }

    // ---------------------------------------------------------------- the day
    // A round finished after the phone died gets written up the next morning, so
    // the screen can step back a configured number of days. Never forward past
    // today: tomorrow's visits are planned, not recorded. (client, 2026-08-25)
    /** The doors this round opened — the same screen the manager and board use. */
    openNewClinics() {
        this.action.doAction({
            type: "ir.actions.client",
            tag: "lab_new_clinics",
            name: "New Clinics",
        });
    }

    shiftDay(days) {
        // `luxon` is a global provided by the web bundle - importing it from a
        // module path is a resolve error that kills the whole bundle, and My Day
        // then renders as a blank page. (2026-08-25)
        const { DateTime } = luxon;
        const d = this.state.data;
        if (!d) {
            return;
        }
        const next = DateTime.fromISO(d.day).plus({ days });
        if (days > 0 && d.is_today) {
            return;                       // never past today
        }
        if (days < 0 && d.earliest && next < DateTime.fromISO(d.earliest)) {
            return;                       // never past the configured window
        }
        const iso = next.toISODate();
        // Arriving back at the real today clears the pin, matching backToToday().
        // The server's today, not the phone's: the two disagree around midnight.
        this.state.day = iso === d.today ? null : iso;
        this.load();
    }

    backToToday() {
        this.state.day = null;
        this.load();
    }

    async load() {
        this.state.loading = true;
        this.state.data = await this.orm.call(
            "lab.my.day", "get_day", [this.state.day || false]);
        this.state.loading = false;
    }

    money(v) {
        return formatMonetary(v || 0, {
            currencyId: this.state.data && this.state.data.currency_id,
        });
    }

    // A formatted amount is one unbreakable token - the formatter joins the
    // number to its symbol with a non-breaking space - so a five-figure sum
    // does not fit a quarter of a phone. Rather than let it wrap (which puts
    // the ₹ on a line of its own) or overflow (which is what "62,950.00 ₹0"
    // was), a long one is simply set smaller. (client, 2026-09-19)
    statCls(value) {
        const n = String(value === undefined || value === null ? "" : value).length;
        return n > 11 ? "o_fw_stat_v o_fw_stat_v_xlong"
             : n > 8  ? "o_fw_stat_v o_fw_stat_v_long"
             : "o_fw_stat_v";
    }

    // The card's single button. What it does depends on where the visit is, so the
    // executive never has to choose between actions — there is only ever one.
    primaryLabel(v) {
        return v.state === "planned" ? "I'm Here" : v.state === "open" ? "Finish" : "View";
    }

    primaryIcon(v) {
        return v.state === "planned" ? "fa-map-marker"
            : v.state === "open" ? "fa-check" : "fa-eye";
    }

    async onPrimary(v) {
        if (v.state === "done") {
            return this.openVisit(v);
        }
        if (this.state.busy) {
            return;
        }
        this.state.busy = v.id;
        try {
            if (v.state === "planned") {
                const coords = await this.getPosition();
                if (!coords) {
                    // The server refuses this too; saying so here means the
                    // executive gets an instruction instead of an error dialog.
                    this.notification.add(
                        "Turn on Location for this browser, wait for the arrow " +
                        "to appear, then tap again. A visit cannot be started " +
                        "without it.",
                        { type: "danger", title: "Location is off", sticky: true }
                    );
                    return;
                }
                await this.orm.call("lab.visit", "do_check_in", [[v.id]], {
                    latitude: coords.latitude,
                    longitude: coords.longitude,
                });
                await this.load();
            } else {
                // Finishing needs an outcome and possibly a reason, so it opens the
                // form rather than guessing. Trying to close from here would mean
                // reproducing the form's rules in the card.
                this.openVisit(v);
            }
        } finally {
            this.state.busy = 0;
        }
    }

    getPosition() {
        return new Promise((resolve) => {
            if (!navigator.geolocation) {
                return resolve(null);
            }
            // A WebView that was never granted location permission can call NEITHER
            // callback and not honour its own `timeout` either: the request is dropped
            // before it starts. This promise then stays pending for ever, and so does
            // the `await` in toggleAttendance — whose `finally` is the only thing that
            // clears `state.busy` and re-enables the button. The executive taps Start
            // the day, the button greys out, and nothing short of a reload brings it
            // back. Hence our own timer: whatever the platform does, this settles.
            // (client, 2026-09-01)
            let settled = false;
            const finish = (value) => {
                if (settled) {
                    return;
                }
                settled = true;
                resolve(value);
            };
            const timer = setTimeout(() => finish(null), 10000);
            navigator.geolocation.getCurrentPosition(
                (p) => {
                    clearTimeout(timer);
                    finish(p.coords);
                },
                () => {
                    clearTimeout(timer);
                    finish(null);
                },
                { enableHighAccuracy: true, timeout: 8000, maximumAge: 0 }
            );
        });
    }

    openVisit(v) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "lab.visit",
            res_id: v.id,
            views: [[false, "form"]],
        });
    }

    openTrip() {
        const trip = this.state.data.trip;
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "My Travel",
            res_model: "lab.trip",
            res_id: trip ? trip.id : false,
            views: [[false, "form"]],
            context: { default_date: this.state.data.date },
        });
    }

    /**
     * Add a suggested clinic to today.
     *
     * The suggestion exists because coverage is only worth measuring if somebody acts
     * on it, and the person who can act is standing outside a clinic — not the manager
     * reading a monthly report.
     */
    async addStop(s) {
        if (this.state.busy) {
            return;
        }
        this.state.busy = "s" + s.partner_id;
        try {
            const id = await this.orm.call("lab.my.day", "add_stop", [s.partner_id]);
            this.notification.add(`${s.clinic} added to today.`, { type: "success" });
            await this.load();
            return id;
        } finally {
            this.state.busy = 0;
        }
    }

    async reload() {
        await this.load();
    }

    // ------------------------------------------------------------- attendance
    get attendance() {
        return (this.state.data && this.state.data.attendance) || {};
    }

    get onDuty() {
        return this.attendance.state === "checked_in";
    }

    /** "2h 40m", because 2.67 is not a length of time anybody says out loud. */
    get hoursToday() {
        const h = this.attendance.hours || 0;
        const whole = Math.floor(h);
        const mins = Math.round((h - whole) * 60);
        return whole ? `${whole}h ${mins}m` : `${mins}m`;
    }

    get clockLabel() {
        if (this.attendance.state === "no_employee") {
            return "No employee record";
        }
        return this.onDuty ? "End the day" : "Start my day";
    }

    /**
     * Start or end the working day.
     *
     * One button in both directions: the state decides what happens, so there is never
     * a wrong one to press. The location is taken the same way a clinic check-in takes
     * it, and a refused fix never blocks the attendance — a day that cannot be started
     * is a day that cannot be worked.
     */
    async toggleAttendance() {
        if (this.state.busy || this.attendance.state === "no_employee") {
            return;
        }
        this.state.busy = "att";
        try {
            const coords = await this.getPosition();
            await this.orm.call("lab.my.day", "attendance_toggle", [], {
                latitude: coords ? coords.latitude : false,
                longitude: coords ? coords.longitude : false,
            });
            await this.load();
            this.notification.add(
                this.onDuty ? "You are on duty. Have a good day." : "Day ended.",
                { type: "success" }
            );
        } finally {
            this.state.busy = 0;
        }
    }

    openCash() {
        this.action.doAction("lab_fieldwork.action_my_cash");
    }

    /** The envelope goes to the office. The dialog opens with everything they
        hold already filled in; declaring it opens the slip with the code big
        enough to read across the desk. Reloads when they come back: the money
        moves to "waiting to be counted" the moment it is declared. */
    handOverCash() {
        this.action.doAction("lab_fieldwork.action_cash_handover_new", {
            additionalContext: { default_user_id: this.env.services.user?.userId },
            onClose: () => this.load(),
        });
    }

    openHandover(h) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "lab.cash.handover",
            res_id: h.id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    /** Cash taken at a door with no visit behind it. Reloads: the day's
        collected figure and the cash-in-hand line both move. */
    collectCash() {
        this.action.doAction("lab_fieldwork.action_collect_cash", {
            onClose: () => this.load(),
        });
    }

    openVisits() {
        this.action.doAction("lab_fieldwork.action_visit_my");
    }

    openQuickEntry() {
        // One tap from the home screen — the same wizard from a menu would be a
        // second thing to find on a screen used at a clinic door.
        this.action.doAction("lab_fieldwork.action_quick_entry");
    }

    // The Field Work menu, as buttons. An executive on a phone has one app and no
    // menu bar worth opening at a clinic door, so every item they can reach is on
    // this screen. (client, 2026-08-24)
    // ------------------------------------------------------ doctors on my round
    // Cards, never a form: res.partner opens on a page of accounting fields an
    // executive cannot read, and the four facts that decide whether to knock -
    // where it is, what it owes, what is still open, is it a favourite - fit on
    // a card. (client, 2026-09-02)
    toggleClinics() {
        this.state.clinics.open = !this.state.clinics.open;
        if (this.state.clinics.open) {
            if (!this.state.clinics.loaded) {
                this.loadClinics("");
            }
            // Opening is the only half that needs the screen moved: closing it
            // leaves the reader where they already are.
            this._scrollToClinics = true;
        }
    }

    onClinicInput(ev) {
        const value = ev.target.value;
        clearTimeout(this._clinicTimer);
        this._clinicTimer = setTimeout(() => this.loadClinics(value), 350);
    }

    /** Newest answer wins: a slow reply must never overwrite a fresher one. */
    async loadClinics(query) {
        const c = this.state.clinics;
        c.busy = true;
        c.query = query === undefined ? c.query : query;
        this._clinicToken = (this._clinicToken || 0) + 1;
        const token = this._clinicToken;
        try {
            const data = await this.orm.call("lab.my.day", "get_clinics", [
                c.query || "",
                c.favouritesOnly,
            ]);
            if (token !== this._clinicToken) {
                return;
            }
            c.rows = data.rows || [];
            c.more = !!data.more;
            c.favourites = data.favourites || 0;
            c.noRoute = !!data.no_route;
            c.loaded = true;
        } finally {
            if (token === this._clinicToken) {
                c.busy = false;
            }
        }
    }

    toggleFavouritesOnly() {
        this.state.clinics.favouritesOnly = !this.state.clinics.favouritesOnly;
        this.loadClinics();
    }

    /**
     * Star a clinic. The card moves to the top of its own list on the next
     * load; flipping it in place first means the tap is acknowledged on a
     * connection where the round trip takes a second and a half.
     */
    async toggleFavourite(row, ev) {
        if (ev) {
            ev.stopPropagation();
        }
        const wanted = !row.favourite;
        row.favourite = wanted;
        const c = this.state.clinics;
        c.favourites += wanted ? 1 : -1;
        try {
            const actual = await this.orm.call(
                "lab.my.day", "toggle_favourite", [row.id]);
            row.favourite = actual;
        } catch (error) {
            row.favourite = !wanted;            // put it back, honestly
            c.favourites += wanted ? -1 : 1;
            throw error;
        }
    }

    /** A card becomes today's visit, on the same call the suggestions use. */
    async newVisitFor(row, ev) {
        if (ev) {
            ev.stopPropagation();
        }
        if (this.state.busy) {
            return;
        }
        this.state.busy++;
        try {
            const visitId = await this.orm.call("lab.my.day", "add_stop", [
                row.id,
                this.state.day || false,
            ]);
            row.visited_today = true;
            this.notification.add(
                _t("%s is on today's round.", row.name), { type: "success" });
            await this.load();
            this.action.doAction({
                type: "ir.actions.act_window",
                res_model: "lab.visit",
                res_id: visitId,
                views: [[false, "form"]],
            });
        } finally {
            this.state.busy--;
        }
    }

    // ---------------------------------------------------------------- track a work
    toggleTrack() {
        this.state.track.open = !this.state.track.open;
        if (this.state.track.open) {
            if (!this.state.track.rows.length) {
                this.searchTrack("");
            }
            this._scrollToTrack = true;
        }
    }

    onTrackInput(ev) {
        // Typed at a clinic door on a poor connection: wait for the typing to stop
        // rather than firing a query per keystroke, and ignore an answer that arrives
        // after a newer one (the token), or the list flickers back to a stale result.
        const value = ev.target.value;
        clearTimeout(this._trackTimer);
        this._trackTimer = setTimeout(() => this.searchTrack(value), 350);
    }

    /** Tap a result: the job's journey, fetched once and kept. */
    async openTrackDetail(row) {
        if (this.state.track.openId === row.id) {
            this.state.track.openId = null;         // tap again to close
            return;
        }
        this.state.track.openId = row.id;
        if (!this.state.track.details[row.id]) {
            this.state.track.details[row.id] = null;   // spinner
            const d = await this.orm.call("lab.my.day", "track_detail", [row.id]);
            this.state.track.details[row.id] = d && d.ok ? d : { ok: false };
        }
    }

    trackDetail(id) {
        return this.state.track.details[id] || undefined;
    }

    /**
     * Re-read one job. The detail is fetched once and kept, which is right for
     * a panel opened and closed a dozen times at a door - but the lab moves
     * work while the executive stands there, and "is it done yet?" asked
     * twice deserves a fresh answer. In place: the old detail stays up with
     * the icon spinning, and the list line is patched from the same reply,
     * so nothing blanks and the panel does not close. (client, 2026-09-09)
     */
    async refreshTrack(row, ev) {
        if (ev) {
            ev.stopPropagation();
        }
        const id = row.id;
        if (this.state.track.refreshing === id) {
            return;
        }
        this.state.track.refreshing = id;
        try {
            const d = await this.orm.call("lab.my.day", "track_detail", [id]);
            this.state.track.details[id] = d && d.ok ? d : { ok: false };
            if (d && d.ok && d.row) {
                const rows = this.state.track.rows;
                const i = rows.findIndex((r) => r.id === id);
                if (i >= 0) {
                    rows[i] = d.row;
                }
            }
        } finally {
            this.state.track.refreshing = null;
        }
    }

    /** The status line, on the clipboard, ready to send the doctor. */
    async copyTrackSummary(detail, ev) {
        if (ev) {
            ev.stopPropagation();
        }
        try {
            await navigator.clipboard.writeText(detail.summary);
            this.notification.add("Status copied", { type: "success" });
        } catch {
            // A phone browser without clipboard permission: show it instead, so
            // the executive can still read it out.
            this.notification.add(detail.summary, { type: "info", sticky: true });
        }
    }

    /** One tap: everything pending for the clinic I am standing in. */
    toggleHere() {
        const here = this.state.track.here;
        if (!here) {
            return;
        }
        this.state.track.partnerId = this.state.track.partnerId ? null : here.id;
        this.searchTrack(this.state.track.query || "");
    }

    async searchTrack(query) {
        // `++(expr || 0)` is not a valid increment target - it threw "Invalid
        // left-hand side expression in prefix operation" and took the whole widget
        // down on load, because an asset-level SyntaxError stops the bundle.
        this._trackToken = (this._trackToken || 0) + 1;
        const token = this._trackToken;
        this.state.track.busy = true;
        try {
            const res = await this.orm.call("lab.my.day", "track_search", [], {
                query, partner_id: this.state.track.partnerId || false });
            if (token !== this._trackToken) {
                return;
            }
            this.state.track.rows = res.rows || [];
            this.state.track.query = res.query || "";
            this.state.track.more = !!res.more;
            this.state.track.no_route = !!res.no_route;
            this.state.track.here = res.here || null;
        } finally {
            if (token === this._trackToken) {
                this.state.track.busy = false;
            }
        }
    }

    openCases() {
        this.action.doAction("lab_fieldwork.action_case_my");
    }

    openTrips() {
        this.action.doAction("lab_fieldwork.action_trip_my");
    }

    openTargets() {
        this.action.doAction("lab_fieldwork.action_target_my");
    }

    openTarget() {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "lab.target",
            res_id: this.state.data.target.id,
            views: [[false, "form"]],
        });
    }
}

registry.category("actions").add("lab_my_day", LabMyDay);
