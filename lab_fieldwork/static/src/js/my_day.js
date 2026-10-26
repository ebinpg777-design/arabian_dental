/** @odoo-module **/

import { Component, onMounted, onPatched, onWillStart, onWillUnmount, proxy, signal, usePlugin, useProps } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { NotificationPlugin } from "@web/core/notifications/notification_plugin";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { session } from "@web/session";
import { user } from "@web/core/user";
import { formatMonetary } from "@web/views/fields/formatters";
import { clearPositionWarning, locate, warnNoPosition } from "@lab_fieldwork/js/position";
import { MyTrail } from "@lab_fieldwork/js/my_trail";

/** "1.2.10" is newer than "1.2.9": compared part by part, as numbers. */
function newerVersion(candidate, current) {
    const a = String(candidate).split(".").map((part) => parseInt(part, 10) || 0);
    const b = String(current).split(".").map((part) => parseInt(part, 10) || 0);
    for (let i = 0; i < Math.max(a.length, b.length); i++) {
        if ((a[i] || 0) !== (b[i] || 0)) {
            return (a[i] || 0) > (b[i] || 0);
        }
    }
    return false;
}

// How often the "Where I am" card re-reads the recorder: often enough that "last fix
// 2 min ago" is true, rare enough to cost nothing. No RPC - the numbers are local.
const WHERE_TICK_MS = 30000;
// ...and while the app's recorder has just started and has no fix yet
const WHERE_FIRST_FIX_MS = 5000;
// A dismissed nudge is forgotten after this long: the key is visit + kind, and a
// visit is one day's thing, so there is nothing to keep past the week.
const DISMISS_KEEP_MS = 7 * 24 * 3600 * 1000;

/**
 * The executive's whole application.
 *
 * One screen, one list, one button per card. Everything else in the module exists so
 * that this screen can be short.
 */
export class LabMyDay extends Component {
    static template = "lab_fieldwork.MyDay";
    static components = { MyTrail };
    props = useProps();

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.notification = usePlugin(NotificationPlugin);
        // Lives in the web client, not in this component: the watch has to
        // survive the executive walking off this screen into a visit form.
        this.tracker = useService("lab_live_track");
        this.state = proxy({
            track: { open: false, rows: [], busy: false, query: "", more: false,
                     openId: null, details: {}, here: null, partnerId: null,
                     refreshing: null },
            clinics: { open: false, rows: [], busy: false, query: "",
                       more: false, favouritesOnly: false, favourites: 0,
                       noRoute: false, loaded: false },
            day: null, data: null, loading: true, busy: 0,
            // The "Where I am" card: the recorder's own numbers (status), the
            // server's answer to tracking_state (km, nudges), whether the trail map
            // is open, and the nudges this phone has waved away.
            where: { status: null, server: null, trail: false, dismissed: {},
                     now: Date.now() } });
        this.state.where.dismissed = this.readDismissed();
        onMounted(() => this.scheduleWhere());
        onWillUnmount(() => clearTimeout(this._whereTimer));
        // TAKE THE SCREEN TO THE ANSWER. The clinics panel opens below the day's
        // own work, which on a phone is far enough down that tapping the tile
        // looked like it had done nothing at all. The scroll has to wait for the
        // panel to exist, so it is armed here and spent on the next patch.
        // (client, 2026-09-12)
        this.clinicsPanel = signal.ref();
        // Track order had the same fault: its panel opened below the day's work
        // and the tap looked like nothing. Same answer, and the cursor goes into
        // the search box. (client, 2026-09-15)
        this.trackPanel = signal.ref();
        this.trackInput = signal.ref();
        onPatched(() => {
            if (this._scrollToClinics) {
                this._scrollToClinics = false;
                this.clinicsPanel()?.scrollIntoView(
                    { behavior: "smooth", block: "start" });
            }
            if (this._scrollToTrack) {
                this._scrollToTrack = false;
                this.trackPanel()?.scrollIntoView({ behavior: "smooth", block: "start" });
                this.trackInput()?.focus({ preventScroll: true });
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
        // Every reload also asks the recorder to re-check with the server, so the
        // km and the nudges on the card are as fresh as the visits above them -
        // and the watch is told at once when the attendance button changed sides.
        // Not awaited: the screen is up, the card fills in behind it.
        this.refreshWhere();
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
                // A phone the lab has excused from giving a position is not
                // asked for one, and never told to switch it on: the time is
                // the record. (client, 2026-09-28)
                const exempt = Boolean(session.fw_location_exception);
                const found = exempt ? {} : await this.getPosition();
                const coords = found.coords || null;
                if (!coords && !exempt) {
                    // The server refuses this too; saying so here - and saying WHICH
                    // thing is wrong - means the executive gets an instruction
                    // instead of an error dialog.
                    warnNoPosition(this.notification, found.error,
                        "A visit cannot be started without it.");
                    return;
                }
                await this.orm.call("lab.visit", "do_check_in", [[v.id]], {
                    latitude: coords ? coords.latitude : false,
                    longitude: coords ? coords.longitude : false,
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

    /** {coords} or {error} - see position.js for everything this used to get wrong. */
    async getPosition() {
        const found = await locate();
        if (found.coords) {
            clearPositionWarning();
        }
        return found;
    }

    /**
     * Take a visit added by mistake off the day - asked first, because a wrong tap
     * here is the very thing being undone. The server decides between deleting and
     * cancelling, and refuses a visit with work on it, saying what is on it.
     * (client, 2026-10-26)
     */
    removeVisit(v) {
        if (this.state.busy) {
            return;
        }
        this.dialog.add(ConfirmationDialog, {
            title: _t("Remove this visit?"),
            body: v.state === "open"
                ? _t("You checked in at %s. The visit will be cancelled and taken off your day. The office still sees the check-in.", v.clinic)
                : _t("%s will be taken off your day. Use this only for a visit added by mistake.", v.clinic),
            confirmLabel: String(_t("Remove")),
            confirmClass: "btn-danger",
            cancelLabel: String(_t("Keep")),
            confirm: async () => {
                this.state.busy = v.id;
                try {
                    const how = await this.orm.call("lab.visit", "action_remove_mistake", [[v.id]]);
                    this.notification.add(how === "deleted"
                        ? _t("%s removed from your day.", v.clinic)
                        : _t("%s cancelled and removed from your day.", v.clinic),
                        { type: "success" });
                } finally {
                    this.state.busy = 0;
                }
                await this.load();
            },
            cancel: () => {},
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

    /**
     * Whether the route is being recorded right now.
     *
     * Shown on the card, always, whenever it is true. An executive is entitled to know
     * their movements are being kept, and a light they can see is the difference
     * between a tool and something done to them behind their back.
     */
    get tracking() {
        return Boolean(this.attendance.tracking);
    }

    // -------------------------------------------------------------- where I am
    // The light on the attendance card says the route is being kept; this card says
    // what that amounts to - how many fixes, how fresh, how far - so the executive
    // sees the thing the manager's board sees about them, and a dead GPS is noticed
    // at the clinic door rather than at the day sheet. (client, 2026-10-19)
    get userId() {
        return user.userId;
    }

    /** Only today, only on duty, only while the lab records at all - and never on a
        phone the lab has excused from giving a position (tracking_state says so;
        the attendance light alone does not know). */
    get showWhere() {
        const server = this.state.where.server;
        // With the tracking app the browser is told to stand down (enabled false), but
        // the card still belongs on the screen: it says the app is recording and how
        // to set it up. (2026-10-19)
        if (server && server.app_mode) {
            return this.onDuty && Boolean(this.state.data && this.state.data.is_today);
        }
        return this.onDuty && this.tracking
            && Boolean(this.state.data && this.state.data.is_today)
            && !(server && server.enabled === false);
    }

    get whereStatus() {
        return this.state.where.status || {};
    }

    /** Re-check with the server, then copy the recorder's numbers onto the card. */
    refreshWhere() {
        this.tracker.refresh().then(() => this.syncWhere(), () => this.syncWhere());
    }

    /**
     * Read the recorder onto the card again, soon. Every 30 s normally - but every
     * 5 s while the app's recorder runs with no fix yet: at 30 s a fix that had
     * arrived stayed "waiting for the first fix" on the card for up to half a
     * minute after Start my day. (client, 2026-10-22)
     */
    scheduleWhere() {
        const s = this.state.where.status || {};
        const waiting = s.native && s.recording && !s.lastFix;
        this._whereTimer = setTimeout(() => {
            this.syncWhere();
            this.scheduleWhere();
        }, waiting ? WHERE_FIRST_FIX_MS : WHERE_TICK_MS);
    }

    /** No RPC: the recorder already knows; this reads it onto reactive state. */
    syncWhere() {
        const w = this.state.where;
        w.status = this.tracker.getStatus();
        w.server = this.tracker.server;
        w.now = Date.now();
    }

    /** "12 fixes sent · last fix 2 min ago · ±18 m" */
    get whereLine() {
        const s = this.whereStatus;
        const sent = s.sent || 0;
        const bits = [`${sent} ${sent === 1 ? "fix" : "fixes"} sent`];
        if (s.lastFix) {
            bits.push(`last fix ${this.ago(s.lastFix.at)}`);
            if (s.lastFix.accuracy_m) {
                bits.push(`±${Math.round(s.lastFix.accuracy_m)} m`);
            }
        } else if (s.recording) {
            bits.push("waiting for the first fix");
        }
        if (s.queued) {
            // Out of coverage: the phone is holding them, and says so.
            bits.push(`${s.queued} waiting to send`);
        }
        return bits.join(" · ");
    }

    /** From the server, not the phone: the kilometres it has actually kept. */
    get kmToday() {
        const server = this.state.where.server;
        const km = Number(server && server.km_today) || 0;
        return km.toFixed(1);
    }

    /** Why the badge is grey, when it is. Only the case the executive can fix. */
    /** The Traccar Client app's settings when it records for this person, else null. */
    get appSetup() {
        const server = this.state.where.server;
        return (server && server.app) || null;
    }

    get appLine() {
        const app = this.appSetup;
        if (!app || app.last_app_fix_age_s === null || app.last_app_fix_age_s === undefined) {
            return "no position from the app yet";
        }
        return `last position ${this.ago(this.state.where.now - app.last_app_fix_age_s * 1000)}`;
    }

    /** Silent for a quarter of an hour while the day runs: the app is off or blocked. */
    get appProblem() {
        const app = this.appSetup;
        if (!app) {
            return "";
        }
        const age = app.last_app_fix_age_s;
        if (age === null || age === undefined || age > 900) {
            return "The tracking app has not sent a position for a while. Open Traccar Client "
                + "and check the service is on, location is allowed all the time and battery "
                + "saving is off for it.";
        }
        return "";
    }

    // ------------------------------------------------------------- the Lab app
    /** The Lab app's own recorder: this page runs inside the app. (2026-10-20) */
    get nativeStatus() {
        const s = this.whereStatus;
        return s && s.native ? s : null;
    }

    /** The Lab app on this person's phone is posting, and this page is not it. */
    get appLive() {
        const server = this.state.where.server;
        return Boolean(server && server.app_live);
    }

    get appLiveLine() {
        const server = this.state.where.server;
        const age = server && server.native_app ? server.native_app.last_fix_age_s : null;
        if (age === null || age === undefined) {
            return "no position from the app yet";
        }
        return `last position ${this.ago(this.state.where.now - age * 1000)}`;
    }

    /** Only what the executive can fix, each with the button that fixes it. */
    get nativeProblem() {
        const s = this.nativeStatus;
        if (!s) {
            return "";
        }
        if (s.permission === "denied") {
            return "Location is not allowed for the app, so your route is not being recorded.";
        }
        if (s.permission === "ask") {
            return "The app has not been allowed to use your location yet.";
        }
        if (s.permission === "coarse") {
            return "Only an approximate location is allowed for the app: allow the precise one.";
        }
        if (!s.recording && this.onDuty) {
            return "The app is not recording. Allow location and notifications, then open My Day again.";
        }
        return "";
    }

    get nativeBatteryHint() {
        const s = this.nativeStatus;
        return s && s.recording && s.batteryOptimised
            ? "Battery saving may stop the app once the screen locks: allow it to run unrestricted."
            : "";
    }

    nativeAllow() {
        try {
            this.tracker.native.requestPermissions();
        } catch {
            // An older shell without the call: the phone's own settings remain.
        }
    }

    nativeBattery() {
        try {
            this.tracker.native.openBatterySettings();
        } catch {
            // As above.
        }
    }

    /** A newer build of the app than the one this page runs in, or "". */
    get appUpdate() {
        const s = this.nativeStatus;
        const server = this.state.where.server;
        const latest = server && server.native && server.native.latest_version;
        if (!s || !latest || !s.version || !newerVersion(latest, s.version)) {
            return "";
        }
        return latest;
    }

    /** The app, offered on a phone that is not running it: the APK on Android, the
     *  iPhone build (or its TestFlight link) on an iPhone, once there is one. */
    get appOffer() {
        const server = this.state.where.server;
        const agent = navigator.userAgent || "";
        if (!server || this.nativeStatus || this.appLive || /ADLField\//.test(agent)) {
            return null;
        }
        if (/iPhone|iPad|iPod/.test(agent)) {
            return server.ios_download || null;
        }
        return /Android/i.test(agent) ? server.app_download || null : null;
    }

    /** The browser's honest limit, named when it bit. */
    get gapNotice() {
        const gap = this.whereStatus.gap;
        if (!gap) {
            return "";
        }
        const minutes = Math.max(1, Math.round(gap.seconds / 60));
        return `Not recorded for ${minutes} min while the phone was locked or another app was in front.`;
    }

    get whereProblem() {
        const s = this.whereStatus;
        if (s.denied) {
            return "Location is blocked for this site, so your route is not being "
                + "recorded. Allow it in Chrome's site settings, then reload the page.";
        }
        if (!s.recording && !navigator.geolocation) {
            return "This browser cannot share a location. Open the app in Google Chrome.";
        }
        return "";
    }

    ago(epochMs) {
        const s = Math.max(0, Math.round((this.state.where.now - epochMs) / 1000));
        if (s < 45) {
            return "just now";
        }
        if (s < 3600) {
            return `${Math.round(s / 60)} min ago`;
        }
        return `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min ago`;
    }

    toggleTrail() {
        this.state.where.trail = !this.state.where.trail;
    }

    // ------------------------------------------------------------------ nudges
    // The phone has noticed something the executive has not pressed: inside a
    // planned clinic's radius without a check-in ("arrived"), or well outside one
    // while still checked in ("left"). The server decides both; this screen only
    // offers the tap. Nothing is written until they take it.
    get nudges() {
        const server = this.state.where.server;
        if (!server || !Array.isArray(server.nudges)) {
            return [];
        }
        const dismissed = this.state.where.dismissed;
        const visits = (this.state.data && this.state.data.visits) || [];
        return server.nudges.filter((n) => {
            if (dismissed[this.nudgeKey(n)]) {
                return false;
            }
            // The card list is fresher than the last tracking_state answer for a
            // few seconds after a check-in: a nudge whose visit has already moved
            // on is not shown again in that window.
            const visit = visits.find((v) => v.id === n.visit_id);
            if (visit && n.kind === "arrived" && visit.state !== "planned") {
                return false;
            }
            if (visit && n.kind === "left" && visit.state !== "open") {
                return false;
            }
            return true;
        });
    }

    nudgeKey(n) {
        return `${n.visit_id}:${n.kind}`;
    }

    // Per user, like the recorder's queue: a shared phone must not carry one
    // person's refusals into the next login.
    get dismissKey() {
        return `lab_fw.nudges.${user.userId || 0}`;
    }

    readDismissed() {
        try {
            const saved = JSON.parse(localStorage.getItem(this.dismissKey)) || {};
            const floor = Date.now() - DISMISS_KEEP_MS;
            return Object.fromEntries(
                Object.entries(saved).filter(([, at]) => Number(at) > floor));
        } catch {
            return {};
        }
    }

    /** The X. Remembered for that visit and kind: a new kind for the same visit
        - left, after arrived - is a new question and shows again. */
    dismissNudge(n) {
        const dismissed = { ...this.state.where.dismissed, [this.nudgeKey(n)]: Date.now() };
        this.state.where.dismissed = dismissed;
        try {
            localStorage.setItem(this.dismissKey, JSON.stringify(dismissed));
        } catch {
            // Private mode: it will simply ask again after a reload.
        }
    }

    /**
     * "Check in?" - the same path as the visit card's "I'm Here": the fix is taken
     * now, by getPosition(), and the server measures it against the clinic itself.
     * The card is looked up first so a visit already opened from its form is not
     * checked in twice.
     */
    async checkInFromNudge(n) {
        const visits = (this.state.data && this.state.data.visits) || [];
        const visit = visits.find((v) => v.id === n.visit_id);
        if (visit && visit.state !== "planned") {
            this.dismissNudge(n);
            return;
        }
        await this.onPrimary(visit || { id: n.visit_id, state: "planned" });
    }

    /** "Check out" needs an outcome, so it opens the form - the existing path. */
    checkOutFromNudge(n) {
        this.openVisit({ id: n.visit_id });
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
            const exempt = Boolean(session.fw_location_exception);
            const found = exempt ? {} : await this.getPosition();
            const coords = found.coords || null;
            await this.orm.call("lab.my.day", "attendance_toggle", [], {
                latitude: coords ? coords.latitude : false,
                longitude: coords ? coords.longitude : false,
            });
            // load() tells the watch which side of the privacy boundary the button
            // just put us on - at once, not on its five-minute timer.
            await this.load();
            this.notification.add(
                this.onDuty ? "You are on duty. Have a good day." : "Day ended.",
                { type: "success" }
            );
            if (!coords && !exempt) {
                // The day is still marked - attendance is about time - but the
                // executive hears that it carries no position, and why, instead of
                // finding out from the day sheet. (client, 2026-09-29)
                warnNoPosition(this.notification, found.error,
                    this.onDuty ? "Your day started without a location."
                                : "Your day ended without a location.",
                    { sticky: false });
            }
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
            additionalContext: { default_user_id: user.userId },
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
            row.visit_under_way = true;
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
