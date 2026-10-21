/** @odoo-module **/

import { onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { LiveMap } from "@lab_fieldwork/js/live_map";

/**
 * Field Live (2026-10-20): the managers' and the CEO's one screen.
 *
 * The map, the trails, the doors, the halts, the replay and the follow mode are
 * the Live Map's, unchanged - this component extends it. What it adds is what the
 * three desks it replaced never had in one place: the team's numbers across the
 * top, every executive as a card with their day's facts, the alerts worth a
 * manager's minute, the phones (who is on ADL FieldX, who has gone quiet), the
 * feed of what is happening, and - for the person in focus - the day as a story.
 *
 * Beside the map, three reports on the same payload (2026-10-21): the day as a
 * timeline of everyone (on duty, at a clinic, stopped, not recorded, over the
 * limit, on one clock), a scoreboard by person and by route, and the trends -
 * fourteen days of visits, cases, money and km, and the month so far.
 *
 * And it is pushed. The server announces every batch of positions on the bus to
 * the manager group; a marker here moves within a second of the phone, and the
 * poll (every minute, down from every twenty seconds) only fetches what the push
 * does not carry.
 */

// After a push the full payload (statuses, alerts, facts) is re-read once things
// have settled, not on every single fix of a moving phone.
const RELOAD_AFTER_PUSH_MS = 8000;
const MOVING_KMH = 4;
const STATUS_RANK = { moving: 0, at_clinic: 1, stopped: 2, offline: 3, off: 4 };
const STATUS_WORDS = {
    moving: _t("on the road"), at_clinic: _t("at a clinic"), stopped: _t("stopped"),
    offline: _t("no signal"), off: _t("off"),
};
const EVENT_ICONS = {
    start: "fa-play-circle", end: "fa-stop-circle", visit_in: "fa-sign-in",
    visit_out: "fa-sign-out", halt: "fa-pause-circle", speed: "fa-tachometer",
    gap: "fa-eye-slash", cash: "fa-money", sheet: "fa-file-text-o",
};
const LEVEL_ICONS = { danger: "fa-exclamation-circle", warning: "fa-exclamation-triangle", info: "fa-info-circle" };
const MOBILE = "(max-width: 767.98px)";
// The timeline's clock: the working day, stretched to whatever was recorded.
const AXIS_START = 7;
const AXIS_END = 20;
const FEED_MAX = 60;
const SORT_KEYS = {
    name: (ex) => String(ex.name).toLowerCase(),
    route: (ex) => String(ex.route || "").toLowerCase(),
    visits: (ex) => ex.visits_done,
    cases: (ex) => (ex.facts || {}).cases || 0,
    collected: (ex) => (ex.facts || {}).collected || 0,
    cash: (ex) => (ex.facts || {}).cash_with || 0,
    km: (ex) => ex.km,
    claimed: (ex) => (ex.trip && ex.trip.claimed_km) || 0,
    clinic: (ex) => (ex.split || {}).clinic || 0,
    halts: (ex) => ex.halt_minutes,
    speed: (ex) => ex.overspeed,
};

function pad(n) {
    return String(n).padStart(2, "0");
}

function isoDate(date) {
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export class FieldLive extends LiveMap {
    static template = "lab_fieldwork.FieldLive";

    setup() {
        super.setup();
        this.busService = useService("bus_service");
        // The Live Map's state, plus this screen's own.
        Object.assign(this.state, {
            view: "map",             // "map" | "timeline" | "scoreboard" | "trends"
            rail: "people",          // "people" | "alerts" | "phones" | "feed"
            query: "",
            storyOpen: true,         // the day story beside the map, when somebody is in focus
            pushes: 0,               // positions that arrived by push this session
            sortKey: "visits",
            sortDir: -1,
        });
        this.reloadTimer = null;
        this.unsubscribe = null;
        onMounted(() => {
            this.unsubscribe = this.busService.subscribe(
                "lab_fieldwork.live", (payload) => this.onLive(payload));
            // The web client sizes the action a beat after mounting: frame the
            // territory once the canvas has its real size.
            setTimeout(() => {
                if (this.map) {
                    this.map.invalidateSize({ animate: false });
                    if (!this.fitted) {
                        this.fitAll();
                    }
                }
            }, 400);
        });
        onWillUnmount(() => {
            if (this.unsubscribe) {
                this.unsubscribe();
            }
            clearTimeout(this.reloadTimer);
        });
    }

    // --------------------------------------------------------------- loading
    async load(quiet = false) {
        if (!quiet) {
            this.state.loading = true;
        }
        try {
            const data = await this.orm.call("lab.field.live", "get_board", [], {
                day: this.state.day || null,
                user_ids: null,
            });
            this.state.data = data;
            this.state.day = data.day;
            this.state.isToday = Boolean(data.is_today);
            this.state.lost = false;
            this.loadedAt = Date.now();
            this.state.range = this.rangeOf(data);
            if (!data.is_today && !quiet && this.state.show === "duty" && !this.touchedShow) {
                this.state.show = "all";
            }
            this.state.updatedAt = new Date().toLocaleTimeString(
                [], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
        } catch (error) {
            // Keep the last drawing: a manager would rather see where everybody
            // was a minute ago than a blank screen with an Oops dialog.
            this.state.lost = true;
            if (!this.state.data) {
                this.state.data = {
                    day: this.state.day, is_today: true, now_epoch: Math.floor(Date.now() / 1000),
                    tiles: "", attribution: "", config: {}, executives: [], clinics: [],
                    kpis: null, alerts: [], phones: [], leaders: [], routes: [], month: [],
                    trend: null, currency: "",
                };
            }
            console.warn("field live: reload failed", error?.message || error);
        } finally {
            this.state.loading = false;
        }
    }

    // --------------------------------------------------------------- the push
    /**
     * A phone just posted: move its marker now, extend its trail, and re-read
     * the whole board a little later for everything a position alone cannot say.
     */
    onLive(payload) {
        const data = this.state.data;
        if (!data || !this.state.isToday || !payload || !payload.user_id) {
            return;
        }
        const ex = (data.executives || []).find((e) => e.user_id === payload.user_id);
        if (!ex) {
            // Somebody the last load did not know (a day just started): fetch.
            this.scheduleReload();
            return;
        }
        const speed = Number(payload.speed_kmh) || 0;
        const point = [payload.lat, payload.lon, payload.epoch, speed];
        const legs = ex.legs || (ex.legs = []);
        const lastLeg = legs[legs.length - 1];
        if (lastLeg && lastLeg.length && payload.epoch - lastLeg[lastLeg.length - 1][2] <= 900) {
            lastLeg.push(point);
        } else {
            legs.push([point]);
        }
        ex.last = {
            lat: payload.lat, lon: payload.lon, epoch: payload.epoch, age_s: 0,
            speed_kmh: speed, accuracy_m: ex.last ? ex.last.accuracy_m : 0,
            battery: payload.battery ?? (ex.last ? ex.last.battery : null),
            source: payload.source || "browser",
        };
        if (["moving", "stopped", "offline"].includes(ex.status)) {
            ex.status = speed >= MOVING_KMH ? "moving" : "stopped";
            ex.status_label = STATUS_WORDS[ex.status];
        }
        if (ex.phone_app) {
            ex.phone_app.last_age_s = 0;
            ex.phone_app.silent = false;
            if (payload.battery !== null && payload.battery !== undefined) {
                ex.phone_app.battery = payload.battery;
            }
            if (payload.source) {
                ex.phone_app.source = payload.source;
            }
        }
        this.state.pushes++;
        this.redraw();
        this.scheduleReload();
    }

    scheduleReload() {
        clearTimeout(this.reloadTimer);
        this.reloadTimer = setTimeout(() => {
            this.reloadTimer = null;
            this.tick();
        }, RELOAD_AFTER_PUSH_MS);
    }

    // --------------------------------------------------------------- what is shown
    /** The Live Map's filter plus the search box; the people with alerts first. */
    get executives() {
        const all = (this.state.data && this.state.data.executives) || [];
        const focus = this.state.focusId;
        const needle = this.state.query.trim().toLowerCase();
        const rows = all.filter((ex) => {
            if (!(this.state.show === "all" || ex.on_duty || ex.user_id === focus)) {
                return false;
            }
            if (!needle) {
                return true;
            }
            return String(ex.name).toLowerCase().includes(needle)
                || String(ex.route || "").toLowerCase().includes(needle);
        });
        return [...rows].sort(
            (a, b) => this.alertRank(a) - this.alertRank(b)
                || (STATUS_RANK[a.status] ?? 9) - (STATUS_RANK[b.status] ?? 9)
                || String(a.name).localeCompare(String(b.name)));
    }

    alertRank(ex) {
        const levels = (ex.alerts || []).map((a) => a.level);
        if (levels.includes("danger")) {
            return 0;
        }
        if (levels.includes("warning")) {
            return 1;
        }
        return 2;
    }

    get kpis() {
        return (this.state.data && this.state.data.kpis) || null;
    }

    get alerts() {
        return (this.state.data && this.state.data.alerts) || [];
    }

    get phones() {
        return (this.state.data && this.state.data.phones) || [];
    }

    get leaders() {
        return (this.state.data && this.state.data.leaders) || [];
    }

    get routes() {
        return (this.state.data && this.state.data.routes) || [];
    }

    get month() {
        return (this.state.data && this.state.data.month) || [];
    }

    get trend() {
        return (this.state.data && this.state.data.trend) || null;
    }

    get currency() {
        return (this.state.data && this.state.data.currency) || "";
    }

    get story() {
        const ex = this.focused;
        return ex ? ex.story || [] : [];
    }

    get showStory() {
        return Boolean(this.focused) && this.state.storyOpen;
    }

    get pulseMax() {
        const kpis = this.kpis;
        return kpis ? Math.max(1, ...kpis.pulse.map((p) => p.count)) : 1;
    }

    get dangerCount() {
        return this.alerts.filter((a) => a.level === "danger").length;
    }

    get silentPhones() {
        return this.phones.filter((p) => p.silent).length;
    }

    get onAppPhones() {
        return this.phones.filter((p) => p.on_app).length;
    }

    /** Everyone's events in one stream, newest first: what is happening. */
    get feed() {
        const all = (this.state.data && this.state.data.executives) || [];
        const items = [];
        for (const ex of all) {
            for (const event of ex.story || []) {
                if (event.epoch && event.kind !== "sheet") {
                    items.push({ ...event, user_id: ex.user_id, name: ex.name, colour: ex.colour });
                }
            }
        }
        items.sort((a, b) => b.epoch - a.epoch);
        return items.slice(0, FEED_MAX);
    }

    // --------------------------------------------------------------- the timeline
    get dayStart() {
        return (this.state.data && this.state.data.day_start_epoch) || 0;
    }

    /** The clock across the top: the working day, stretched to what was recorded. */
    get axis() {
        let start = AXIS_START;
        let end = AXIS_END;
        for (const ex of this.executives) {
            for (const band of ex.bands || []) {
                start = Math.min(start, Math.floor(this.hourOf(band.from_epoch)));
                end = Math.max(end, Math.ceil(this.hourOf(band.to_epoch)));
            }
        }
        start = Math.max(0, start);
        end = Math.min(24, Math.max(end, start + 1));
        const hours = [];
        for (let h = start; h <= end; h++) {
            hours.push(h);
        }
        return { start, end, hours };
    }

    hourOf(epoch) {
        return (epoch - this.dayStart) / 3600;
    }

    pctOf(epoch) {
        const axis = this.axis;
        const hour = Math.min(axis.end, Math.max(axis.start, this.hourOf(epoch)));
        return ((hour - axis.start) / (axis.end - axis.start)) * 100;
    }

    bandStyle(band) {
        const left = this.pctOf(band.from_epoch);
        const right = this.pctOf(band.to_epoch);
        const width = Math.max(right - left, band.kind === "speed" ? 0.3 : 0.6);
        return `left: ${left.toFixed(2)}%; width: ${width.toFixed(2)}%;`;
    }

    bandTitle(band) {
        const span = `${this.clock(band.from_epoch)} – ${band.open ? _t("now") : this.clock(band.to_epoch)}`;
        return `${band.label} · ${span}`;
    }

    openBand(band) {
        if (band.visit_id) {
            this.openRecord("lab.visit", band.visit_id);
        } else if (band.halt_id) {
            this.openRecord("lab.location.halt", band.halt_id);
        }
    }

    get nowPct() {
        if (!this.state.isToday) {
            return null;
        }
        const hour = this.hourOf(this.nowEpoch());
        const axis = this.axis;
        if (hour < axis.start || hour > axis.end) {
            return null;
        }
        return ((hour - axis.start) / (axis.end - axis.start)) * 100;
    }

    hourLabel(hour) {
        return `${pad(hour)}`;
    }

    // --------------------------------------------------------------- the scoreboard
    get scoreRows() {
        const key = SORT_KEYS[this.state.sortKey] || SORT_KEYS.visits;
        const dir = this.state.sortDir;
        return [...this.executives].sort((a, b) => {
            const va = key(a);
            const vb = key(b);
            if (va === vb) {
                return String(a.name).localeCompare(String(b.name));
            }
            return (va > vb ? 1 : -1) * dir;
        });
    }

    setSort(key) {
        if (this.state.sortKey === key) {
            this.state.sortDir = -this.state.sortDir;
        } else {
            this.state.sortKey = key;
            this.state.sortDir = key === "name" || key === "route" ? 1 : -1;
        }
    }

    sortIcon(key) {
        if (this.state.sortKey !== key) {
            return "fa-sort";
        }
        return this.state.sortDir > 0 ? "fa-sort-asc" : "fa-sort-desc";
    }

    get scoreTotals() {
        const rows = this.scoreRows;
        const sum = (fn) => rows.reduce((acc, ex) => acc + (Number(fn(ex)) || 0), 0);
        return {
            people: rows.length,
            visits: sum((ex) => ex.visits_done),
            planned: sum((ex) => ex.visits_planned),
            cases: sum(SORT_KEYS.cases),
            collected: sum(SORT_KEYS.collected),
            cash: sum(SORT_KEYS.cash),
            km: sum((ex) => ex.km),
            claimed: sum(SORT_KEYS.claimed),
            clinic: sum(SORT_KEYS.clinic),
            halts: sum((ex) => ex.halt_minutes),
            speed: sum((ex) => ex.overspeed),
        };
    }

    // --------------------------------------------------------------- the trends
    trendMax(metric) {
        const trend = this.trend;
        if (!trend) {
            return 1;
        }
        return Math.max(1, ...trend.days.map((d) => Number(d[metric]) || 0));
    }

    trendHeight(day, metric) {
        return Math.max(2, Math.round(((Number(day[metric]) || 0) / this.trendMax(metric)) * 100));
    }

    trendTotal(metric) {
        const trend = this.trend;
        return trend ? trend.days.reduce((acc, d) => acc + (Number(d[metric]) || 0), 0) : 0;
    }

    dayNum(iso) {
        return String(Number(String(iso).split("-")[2]) || "");
    }

    dayLabel(iso) {
        const [y, m, d] = String(iso).split("-").map(Number);
        const date = new Date(y, m - 1, d);
        return date.toLocaleDateString([], { weekday: "short" }).slice(0, 2) + " " + d;
    }

    dotClass(cell) {
        if (!cell) {
            return "o_fw_fl_dot_none";
        }
        if (cell.state === "live") {
            return "o_fw_fl_dot_live";
        }
        if (cell.clean) {
            return "o_fw_fl_dot_clean";
        }
        return "o_fw_fl_dot_flag";
    }

    dotTitle(cell, iso) {
        if (!cell) {
            return `${iso}: ${_t("no day sheet")}`;
        }
        const state = cell.state === "live" ? _t("running now") : cell.clean ? _t("clean day") : _t("with issues");
        return `${iso}: ${cell.visits} ${_t("visits")} · ${state}`;
    }

    // --------------------------------------------------------------- the split
    /** The duty minutes as shares: at clinics, on the road, stopped, not recorded. */
    splitParts(ex) {
        const split = ex.split || {};
        const total = Math.max(1, split.duty || 0);
        const parts = [
            ["clinic", _t("at clinics")], ["road", _t("on the road")],
            ["stopped", _t("stopped")], ["unrecorded", _t("not recorded")],
        ];
        return parts.map(([key, label]) => ({
            key, label, minutes: split[key] || 0,
            pct: Math.round(((split[key] || 0) / total) * 100),
        })).filter((p) => p.minutes > 0);
    }

    hoursWord(minutes) {
        const m = Math.max(0, Math.round(minutes || 0));
        if (m < 60) {
            return `${m} min`;
        }
        return `${Math.floor(m / 60)}h ${pad(m % 60)}`;
    }

    // --------------------------------------------------------------- actions
    setView(name) {
        this.state.view = name;
        if (name === "map") {
            setTimeout(() => this.map && this.map.invalidateSize(), 50);
        }
    }

    setRail(name) {
        this.state.rail = name;
        if (window.matchMedia(MOBILE).matches) {
            this.state.panelOpen = true;
        }
    }

    clearQuery() {
        this.state.query = "";
        this.redraw();
    }

    onQuery(ev) {
        this.state.query = ev.target.value;
        this.fitted = false;
        this.redraw();
    }

    shiftDay(days) {
        const [y, m, d] = String(this.state.day).split("-").map(Number);
        const date = new Date(y, m - 1, d + days);
        const today = new Date();
        if (date > today) {
            return;
        }
        this.pickDate(isoDate(date));
    }

    get dayWord() {
        const [y, m, d] = String(this.state.day || "").split("-").map(Number);
        if (!y) {
            return "";
        }
        return new Date(y, m - 1, d).toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
    }

    focus(ex, fit = true) {
        super.focus(ex, fit);
        if (this.state.focusId === ex.user_id) {
            this.state.storyOpen = true;
            setTimeout(() => this.map && this.map.invalidateSize(), 250);
        }
    }

    clearFocus() {
        super.clearFocus();
        setTimeout(() => this.map && this.map.invalidateSize(), 250);
    }

    toggleStory() {
        this.state.storyOpen = !this.state.storyOpen;
        setTimeout(() => this.map && this.map.invalidateSize(), 250);
    }

    focusUser(userId) {
        const ex = ((this.state.data && this.state.data.executives) || [])
            .find((e) => e.user_id === userId);
        if (!ex) {
            return;
        }
        if (this.state.focusId !== ex.user_id) {
            this.focus(ex, true);
        } else {
            this.state.storyOpen = true;
        }
    }

    /** An alert about a person focuses them; one about records opens the records. */
    openAlert(alert) {
        if (alert.user_id) {
            this.focusUser(alert.user_id);
            return;
        }
        if (alert.model && alert.ids && alert.ids.length) {
            this.action.doAction({
                type: "ir.actions.act_window",
                res_model: alert.model,
                views: [[false, "list"], [false, "form"]],
                domain: [["id", "in", alert.ids]],
                name: alert.title,
            });
        }
    }

    openRecord(model, id) {
        if (!id) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window", res_model: model, res_id: id,
            views: [[false, "form"]], target: "current",
        });
    }

    openEvent(event) {
        if (event.visit_id) {
            this.openRecord("lab.visit", event.visit_id);
        } else if (event.sheet_id) {
            this.openRecord("lab.daily.update", event.sheet_id);
        } else if (event.halt_id) {
            this.openRecord("lab.location.halt", event.halt_id);
        }
    }

    openVisits(ex) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "lab.visit",
            views: [[false, "list"], [false, "form"]],
            domain: [["user_id", "=", ex.user_id], ["date", "=", this.state.day]],
            name: _t("%(name)s — visits", { name: ex.name }),
        });
    }

    // --------------------------------------------------------------- words and numbers
    money(value) {
        const amount = Number(value || 0);
        return `${this.currency}${amount.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
    }

    statusWord(ex) {
        return ex.status_label || STATUS_WORDS[ex.status] || ex.status;
    }

    /** "since 09:40" for a state that started, or the last fix's age. */
    sinceLabel(ex) {
        if (ex.status === "at_clinic" && ex.at_clinic) {
            return _t("since %s", this.clock(ex.at_clinic.since_epoch));
        }
        if (ex.status === "off") {
            return ex.on_duty ? "" : _t("not on duty");
        }
        return this.lastSeen(ex);
    }

    batteryClass(level) {
        if (level === null || level === undefined) {
            return "";
        }
        return level < 15 ? "o_fw_fl_bat_low" : level < 40 ? "o_fw_fl_bat_mid" : "o_fw_fl_bat_ok";
    }

    batteryIcon(level) {
        if (level === null || level === undefined) {
            return "fa-battery-empty";
        }
        if (level >= 85) {
            return "fa-battery-full";
        }
        if (level >= 60) {
            return "fa-battery-three-quarters";
        }
        if (level >= 35) {
            return "fa-battery-half";
        }
        if (level >= 15) {
            return "fa-battery-quarter";
        }
        return "fa-battery-empty";
    }

    phoneWord(phone) {
        if (phone.on_app) {
            return phone.app_version ? _t("ADL FieldX %s", phone.app_version) : _t("ADL FieldX");
        }
        if (phone.traccar) {
            return _t("Traccar");
        }
        return _t("browser");
    }

    ageLabel(seconds) {
        if (seconds === null || seconds === undefined) {
            return this.state.isToday ? _t("not today") : "";
        }
        const minutes = Math.round(seconds / 60);
        if (minutes < 1) {
            return _t("just now");
        }
        if (minutes < 60) {
            return _t("%s min", minutes);
        }
        return _t("%(h)sh %(m)sm", { h: Math.floor(minutes / 60), m: minutes % 60 });
    }

    eventIcon(kind) {
        return EVENT_ICONS[kind] || "fa-circle";
    }

    eventClass(event) {
        const classes = [`o_fw_fl_ev_${event.kind}`];
        if (event.open) {
            classes.push("o_fw_fl_ev_open");
        }
        if (event.visit_id || event.sheet_id || event.halt_id) {
            classes.push("o_fw_fl_ev_link");
        }
        return classes.join(" ");
    }

    eventIconClass(event) {
        const classes = [this.eventIcon(event.kind)];
        if (event.state) {
            classes.push(`o_fw_fl_door_${event.state}`);
        }
        return classes.join(" ");
    }

    levelIcon(level) {
        return LEVEL_ICONS[level] || "fa-circle";
    }

    pct(part, whole) {
        return whole ? Math.min(100, Math.round((part / whole) * 100)) : 0;
    }

    pulseHeight(count) {
        return Math.max(8, Math.round((count / this.pulseMax) * 100));
    }
}

registry.category("actions").add("lab_field_live", FieldLive);
