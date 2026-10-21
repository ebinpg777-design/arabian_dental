/** @odoo-module **/

import { usePlugin } from "@odoo/owl";
import { NotificationPlugin } from "@web/core/notifications/notification_plugin";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { warnNoPosition } from "@lab_fieldwork/js/position";

/**
 * The trail between the doors.
 *
 * While an executive's day is running, the browser watches the phone's position and
 * posts what it sees. The day sheet then draws the route actually taken rather than
 * three dots and a guess.
 *
 * This is a SERVICE, not a component, and that is the whole design. My Day is one
 * screen among many: the executive opens it, presses Start my day, and then spends the
 * morning in visit forms, the case wizard and the cash screen. A watcher owned by the
 * My Day component would stop the moment they navigated away — which is to say, five
 * seconds after it started, every day, silently. A service is created once per web
 * client and lives until the tab is closed.
 *
 * Be honest about the limit, because the lab will otherwise assume otherwise: a browser
 * only runs while its page is open. Lock the phone, switch to WhatsApp, or let the tab
 * be discarded and the watch is suspended by the operating system. The trail then has a
 * gap. A gap is drawn as a gap and never bridged with an invented straight line.
 * Genuine background location needs a native app and a foreground-service permission,
 * which is a decision about people and not about a map.
 *
 * What the browser decides here is only how often to OFFER a position. What is kept is
 * decided entirely on the server, which is where the rules that matter live: no ping
 * outside the attendance window, none that shows no movement, none kept past the
 * retention window.
 *
 * What the first version got wrong, and this one does not (client, 2026-10-19):
 *
 * - Five watch errors in a row stopped the recording for the day. A phone in a
 *   clinic basement produces five timeouts in three minutes, so most days ended
 *   before lunch and nobody was told. Errors are now read for what they mean.
 * - A reload — the PWA restarting, Chrome discarding the tab — threw away whatever
 *   was waiting to be sent. The queue now lives in localStorage until the server
 *   has acknowledged it.
 * - The phone dimmed and locked itself in the dashboard holder, and the watch went
 *   with it. A screen wake lock keeps it awake while the app is in front.
 */

// What the phone keeps before offering: a fix closer than this to the last one kept,
// and sooner than this after it, says nothing new. The server applies the same two
// numbers, so what is dropped here would have been dropped there.
const MIN_MOVE_M = 50;
const MIN_WAIT_S = 300;
// Nothing kept for this long while the phone still knows where it last was: say so
// once more with a fresh time, so a long stop reads as a stop and not as a gap.
const HEARTBEAT_S = 300;
const HEARTBEAT_CHECK_MS = 60000;
// What one POST carries, and what the phone will hold for the server across a day
// out of coverage. The server keeps 2000 a day too; more than that is never useful.
const BATCH_MAX = 200;
const QUEUE_MAX = 2000;
// "Position unavailable" is a provider hiccup more often than a switched-off GPS:
// ask again after this long rather than giving up.
const RETRY_UNAVAILABLE_MS = 30000;
// The web client is told how often to ask the server whether to watch at all.
const POLL_MS = 300000;
// The browser fallback: back in front after this long in the background, the gap is
// named on the card - and, on Android, the app that would have closed it is offered.
const GAP_NOTICE_S = 120;

/**
 * The Lab app's bridge (2026-10-20). Inside the app's WebView, Android injects
 * `window.LabApp`; everywhere else it is undefined. Through it the page turns the
 * app's own recorder - a native service that keeps going with the screen off - on
 * and off, so a day started here starts it and a day ended anywhere stops it. The
 * contract is in projects/adl_field_app/README.md: version(), start(configJson),
 * stop(), status() -> json, requestPermissions(), openBatterySettings().
 */
export function nativeBridge() {
    const bridge = window.LabApp;
    return bridge && typeof bridge.start === "function" && typeof bridge.status === "function"
        ? bridge
        : null;
}

export class LiveTracker {
    constructor(orm, notification) {
        this.orm = orm;
        this.notification = notification;
        this.recording = false;
        this.watchId = null;
        this.buffer = [];              // fixes the server has not acknowledged yet
        this.last = null;              // the last fix we decided to keep
        this.seen = null;              // the last fix the hardware gave, kept or not
        this.enabled = false;
        this.onDuty = false;
        this.interval = 90;
        this.server = null;            // the last tracking_state answer, read by My Day
        this.flushTimer = null;
        this.pollTimer = null;
        this.heartbeatTimer = null;
        this.retryTimer = null;
        this.flushing = false;
        this.restartedAfterTimeout = false;
        this.denied = false;
        this.wakeLock = null;
        this.battery = null;           // 0-100, or null when the browser will not say
        this.batteryManager = null;
        this.sent = 0;                 // fixes the server took delivery of this session
        this.kept = 0;                 // ...and how many of those it kept
        // Inside the Lab app the native recorder does the watching and this page
        // only says when. Outside it is null, and everything below runs as before.
        this.native = nativeBridge();
        this.nativeVersion = "";
        if (this.native) {
            try {
                this.nativeVersion = String(this.native.version() || "");
            } catch {
                // An older shell without version(): still the shell.
            }
        }
        this.hiddenSince = null;       // when the page went to the background
        this.lastGap = null;           // { seconds, at }: the last silence worth naming
        // Whatever a previous page left behind is ours to deliver, recording or not:
        // the server files each fix against the attendance window it was taken in.
        this.restoreQueue();
    }

    get running() {
        return this.recording;
    }

    /** For the My Day card: what the recorder is doing, in four numbers. */
    getStatus() {
        if (this.native) {
            return this.nativeStatus();
        }
        return {
            recording: this.recording,
            lastFix: this.seen ? { ...this.seen } : null,
            queued: this.buffer.length,
            sent: this.sent,
            kept: this.kept,
            denied: this.denied,
            battery: this.battery,
            gap: this.lastGap,
        };
    }

    /** The native recorder's numbers in the same shape, plus what only it knows. */
    nativeStatus() {
        let s = {};
        try {
            s = JSON.parse(this.native.status() || "{}") || {};
        } catch {
            s = {};
        }
        const fix = s.last_fix;
        return {
            native: true,
            version: this.nativeVersion,
            recording: Boolean(s.running),
            lastFix: fix
                ? {
                      latitude: fix.latitude,
                      longitude: fix.longitude,
                      accuracy_m: fix.accuracy_m || 0,
                      at: fix.at || Date.now(),
                  }
                : null,
            queued: s.queued || 0,
            sent: s.sent || 0,
            kept: s.kept || 0,
            denied: s.permission === "denied",
            permission: s.permission || "unknown",
            batteryOptimised: Boolean(s.battery_optimised),
            battery: Number.isFinite(s.battery) ? s.battery : null,
            gap: null,
        };
    }

    /**
     * Ask the server whether the watch should be on, and act on the answer.
     *
     * Called at start-up, on a slow timer, and by My Day each time it loads. Routing
     * every decision through one server answer is what makes "I ended my day on the
     * office computer" stop the watch on the phone as well. Resolves to the answer
     * (or the previous one when the network is down) so My Day can read the day's
     * kilometres and the nudges from the same call.
     */
    async refresh() {
        let state;
        // The app's page says so, and says which build it is: the server answers
        // with the service's settings instead of a watch for this page.
        const kwargs = this.native ? { client: "app", app_version: this.nativeVersion } : {};
        try {
            state = await this.orm.silent.call("lab.my.day", "tracking_state", [], kwargs);
        } catch {
            // A failed check is not a reason to start recording, nor to throw away a
            // watch that is working: leave things exactly as they are and ask again.
            return this.server;
        }
        this.server = state;
        this.enabled = Boolean(state.enabled);
        this.onDuty = Boolean(state.on_duty);
        const interval = state.interval || 90;
        if (interval !== this.interval) {
            this.interval = interval;
            if (this.recording) {
                // The lab changed the cadence in Settings: a running timer is on the
                // old one until it is rebuilt.
                this.armFlush();
            }
        }
        if (this.native) {
            this.driveNative(state);
        } else if (this.enabled && this.onDuty) {
            this.start();
        } else {
            this.stop();
        }
        if (this.buffer.length && !this.recording) {
            // Left over from a day ended elsewhere, or from before a reload. Sending
            // is always safe: the server drops what falls outside a duty window.
            this.flush();
        }
        return state;
    }

    /**
     * Inside the Lab app the decision is the same - on while the day runs, off
     * otherwise - but the recorder is the app's service. Every refresh re-sends the
     * settings: the service treats a repeated start as "carry on with these", so a
     * changed cadence or a replaced device id reaches the phone within one poll.
     */
    driveNative(state) {
        if (!this.native) {
            return;
        }
        try {
            if (state && state.native && this.onDuty) {
                // The service posts to the server this page came from - not to
                // web.base.url, which may be stale (an http:// address behind the
                // domain, say) and is not what the phone has just proved reachable.
                const config = {
                    ...state.native,
                    server_url: window.location.origin + state.native.path,
                };
                this.native.start(JSON.stringify(config));
            } else {
                this.native.stop();
            }
        } catch {
            // The bridge threw (an old shell, a WebView half torn down): nothing a
            // web page can do about it; the next refresh tries again.
        }
    }

    start() {
        if (this.native) {
            return;                    // the app records; this page never watches itself
        }
        if (this.recording || !navigator.geolocation) {
            return;
        }
        if (this.denied) {
            // Nothing will come until the site setting changes, and asking again
            // would only repeat the warning. Check the permission itself instead:
            // once it is no longer "denied", the next start goes ahead.
            this.recheckPermission();
            return;
        }
        this.recording = true;
        this.restartedAfterTimeout = false;
        this.watch();
        this.armFlush();
        this.heartbeatTimer = setInterval(() => this.heartbeat(), HEARTBEAT_CHECK_MS);
        this.requestWakeLock();
        this.watchBattery();
        // Whatever waited through a reload goes first, before the day adds to it.
        this.flush();
    }

    stop() {
        this.recording = false;
        if (this.flushTimer) {
            clearInterval(this.flushTimer);
            this.flushTimer = null;
        }
        if (this.heartbeatTimer) {
            clearInterval(this.heartbeatTimer);
            this.heartbeatTimer = null;
        }
        if (this.retryTimer) {
            clearTimeout(this.retryTimer);
            this.retryTimer = null;
        }
        if (this.watchId !== null) {
            navigator.geolocation.clearWatch(this.watchId);
            this.watchId = null;
        }
        this.releaseWakeLock();
        // Whatever is still in hand belongs to the day that has just ended, and the
        // server will file it against the attendance window it was taken in.
        this.flush();
        this.last = null;
        this.seen = null;
        this.lastGap = null;
        this.hiddenSince = null;
        this.restartedAfterTimeout = false;
    }

    // ------------------------------------------------------------- the background
    wentBackground() {
        this.hiddenSince = Date.now();
    }

    /**
     * Back in front after a spell in the background: the watch was suspended for
     * that long, so say so - My Day names the gap and, on Android, offers the app
     * that would have closed it - and ask for one fix right now, so the gap ends
     * where the phone is and not where the next fifty metres happen.
     */
    cameToFront() {
        const since = this.hiddenSince;
        this.hiddenSince = null;
        if (!this.recording || this.native || !since) {
            return;
        }
        const seconds = Math.round((Date.now() - since) / 1000);
        if (seconds >= GAP_NOTICE_S) {
            this.lastGap = { seconds, at: Date.now() };
        }
        this.fixNow();
    }

    fixNow() {
        if (!this.recording || !navigator.geolocation) {
            return;
        }
        navigator.geolocation.getCurrentPosition(
            (position) => this.take(position.coords, position.timestamp),
            () => {},
            { enableHighAccuracy: true, maximumAge: 0, timeout: 20000 }
        );
    }

    // ------------------------------------------------------------------ the watch
    watch() {
        if (this.watchId !== null) {
            navigator.geolocation.clearWatch(this.watchId);
        }
        this.watchId = navigator.geolocation.watchPosition(
            (position) => this.take(position.coords, position.timestamp),
            (error) => this.onWatchError(error),
            { enableHighAccuracy: true, maximumAge: 15000, timeout: 30000 }
        );
    }

    /**
     * Read a watch error for what it means, instead of counting it.
     *
     * The three codes are three different situations and only one of them is a reason
     * to stop. An executive is working, not debugging, so none of them gets a dialog
     * except the one they can actually fix.
     */
    onWatchError(error) {
        const code = error && error.code;
        if (code === 1) {
            // PERMISSION_DENIED. Nothing will arrive until the executive changes a
            // site setting, so say which one - the same words every screen uses -
            // and stop for real.
            this.denied = true;
            this.stop();
            if (this.notification) {
                warnNoPosition(this.notification, "denied",
                    "Your route is not being recorded.");
            }
            return;
        }
        if (code === 2) {
            // POSITION_UNAVAILABLE: the phone's location went off, or the provider
            // hiccupped. Chrome reports it once and then goes quiet, so the watch is
            // rebuilt after a pause rather than left waiting for an answer that
            // will not come on its own.
            this.pauseWatch(RETRY_UNAVAILABLE_MS);
            return;
        }
        // TIMEOUT (3), or an error with no code: no fix inside thirty seconds. On a
        // parked phone that is only stillness, and the watch is kept. Once per day
        // it is rebuilt all the same, because a WebView's watch can wedge after a
        // long spell in the background and never fire again. (client, 2026-10-19)
        if (!this.restartedAfterTimeout) {
            this.restartedAfterTimeout = true;
            this.watch();
        }
    }

    pauseWatch(ms) {
        if (this.watchId !== null) {
            navigator.geolocation.clearWatch(this.watchId);
            this.watchId = null;
        }
        clearTimeout(this.retryTimer);
        this.retryTimer = setTimeout(() => {
            this.retryTimer = null;
            if (this.recording && this.watchId === null) {
                this.watch();
            }
        }, ms);
    }

    async recheckPermission() {
        let state = "unknown";
        try {
            if (navigator.permissions && navigator.permissions.query) {
                state = (await navigator.permissions.query({ name: "geolocation" })).state;
            }
        } catch {
            // Older Safari and some WebViews cannot be asked; stay stopped until a
            // reload, which the warning already told them to do.
            return;
        }
        if (state !== "denied" && state !== "unknown") {
            this.denied = false;
            if (this.enabled && this.onDuty) {
                this.start();
            }
        }
    }

    // ------------------------------------------------------------------ the fixes
    /**
     * Keep a fix if it says something new.
     *
     * The server drops repeats too — it has to, because it cannot trust a client — but
     * doing it here as well is what keeps a phone parked at a clinic for two hours from
     * posting eighty identical positions over a mobile connection.
     */
    take(coords, timestamp) {
        if (!coords || (!coords.latitude && !coords.longitude)) {
            return;
        }
        const when = new Date(timestamp || Date.now());
        const fix = {
            latitude: coords.latitude,
            longitude: coords.longitude,
            accuracy_m: coords.accuracy || 0,
            // The hardware says m/s, and says nothing (null or NaN) for a network fix
            // or a phone standing still; the server stores km/h or nothing.
            speed_kmh: Number.isFinite(coords.speed) && coords.speed >= 0
                ? Math.round(coords.speed * 36) / 10 : null,
            heading: Number.isFinite(coords.heading) ? Math.round(coords.heading) : null,
            battery: this.battery,
            at: when.getTime(),
        };
        this.seen = fix;
        if (this.last) {
            const moved = metres(this.last.latitude, this.last.longitude,
                                 fix.latitude, fix.longitude);
            const waited = (when - this.last.when) / 1000;
            if (moved < MIN_MOVE_M && waited < MIN_WAIT_S) {
                return;
            }
        }
        this.keep(fix, when);
    }

    keep(fix, when) {
        this.last = { latitude: fix.latitude, longitude: fix.longitude, when };
        this.buffer.push({
            latitude: fix.latitude,
            longitude: fix.longitude,
            accuracy_m: fix.accuracy_m,
            speed_kmh: fix.speed_kmh,
            heading: fix.heading,
            battery: fix.battery,
            // The server stores UTC; sending anything else files the round under the
            // wrong day for the five and a half hours that matter most here.
            ts: stamp(when),
        });
        if (this.buffer.length > QUEUE_MAX) {
            this.buffer = this.buffer.slice(-QUEUE_MAX);
        }
        this.saveQueue();
        if (this.buffer.length >= 25) {
            this.flush();
        }
    }

    /**
     * A stop is a fact too. When the hardware has gone quiet - a timeout, a phone
     * face-down on a desk - the last position it gave is sent again with a fresh
     * time, once per heartbeat length. The server dedupes on the time, never on the
     * place, so this lands as the "still here" row its own heartbeat rule exists for.
     */
    heartbeat() {
        if (!this.recording || !this.seen || !this.last) {
            return;
        }
        const now = new Date();
        if ((now - this.last.when) / 1000 < HEARTBEAT_S) {
            return;
        }
        this.keep({ ...this.seen, battery: this.battery }, now);
    }

    // ------------------------------------------------------------------ sending
    armFlush() {
        if (this.flushTimer) {
            clearInterval(this.flushTimer);
        }
        // watchPosition fires as fast as the hardware reports, which on a moving phone
        // is several times a second. The buffer is drained on our own clock instead.
        this.flushTimer = setInterval(() => this.flush(), this.interval * 1000);
    }

    async flush() {
        if (this.flushing || !this.buffer.length) {
            return;
        }
        this.flushing = true;
        try {
            // Batches of 200 until the queue is empty or the network says no: after a
            // morning out of coverage there may be ten of them waiting.
            while (this.buffer.length) {
                const batch = this.buffer.slice(0, BATCH_MAX);
                let kept;
                try {
                    kept = await this.orm.silent.call("lab.location.ping", "record", [batch]);
                } catch {
                    // Out of coverage, most likely — half this job happens between
                    // towns. The batch stays where it is, in storage, for the next tick.
                    break;
                }
                // By identity, not by count: the watch kept pushing while the POST
                // was in flight, and the queue may have been trimmed under it.
                const gone = new Set(batch);
                this.buffer = this.buffer.filter((ping) => !gone.has(ping));
                this.sent += batch.length;
                this.kept += Number(kept) || 0;
                this.saveQueue();
            }
        } finally {
            this.flushing = false;
        }
    }

    // ------------------------------------------------------------------ the queue
    // One queue per user, because a shared phone exists: the trail of whoever was
    // logged in yesterday must never be posted under today's login.
    get queueKey() {
        return `lab_fw.trail.${user.userId || 0}`;
    }

    saveQueue() {
        try {
            localStorage.setItem(this.queueKey, JSON.stringify(this.buffer.slice(-QUEUE_MAX)));
        } catch {
            // Private mode or a full store: the fixes live in memory only, as before.
        }
    }

    restoreQueue() {
        try {
            const saved = JSON.parse(localStorage.getItem(this.queueKey) || "[]");
            if (Array.isArray(saved) && saved.length) {
                const sound = saved.filter((p) => p && typeof p === "object"
                    && Number.isFinite(p.latitude) && Number.isFinite(p.longitude)
                    && typeof p.ts === "string");
                this.buffer = sound.concat(this.buffer).slice(-QUEUE_MAX);
            }
        } catch {
            // A broken buffer is not worth more than a shrug.
        }
    }

    // ------------------------------------------------------------------ the screen
    /**
     * Keep the screen on while recording.
     *
     * A web page still cannot track with the screen off: the moment the phone locks,
     * the operating system suspends the tab and the watch with it (platform limit; a
     * native app with a foreground service is the only way round it). What the wake
     * lock does is stop the phone from dimming and locking ITSELF while the app is
     * open in the dashboard holder - which, on the road, was most of the gap.
     */
    async requestWakeLock() {
        if (this.wakeLock || !navigator.wakeLock) {
            return;
        }
        try {
            const lock = await navigator.wakeLock.request("screen");
            if (!this.recording) {
                // Stopped while the request was pending.
                lock.release().catch(() => {});
                return;
            }
            this.wakeLock = lock;
            // The OS lets go of it whenever the page is hidden; the visibility
            // handler below asks again on the way back.
            lock.addEventListener("release", () => {
                if (this.wakeLock === lock) {
                    this.wakeLock = null;
                }
            });
        } catch {
            // Refused - a low battery, or the page is not in front. Not an error.
            this.wakeLock = null;
        }
    }

    releaseWakeLock() {
        const lock = this.wakeLock;
        this.wakeLock = null;
        if (lock) {
            lock.release().catch(() => {});
        }
    }

    // ------------------------------------------------------------------ the battery
    /** A trail that dies at 14:10 with the battery at 3% explains itself. */
    async watchBattery() {
        if (this.batteryManager || !navigator.getBattery) {
            return;
        }
        try {
            const manager = await navigator.getBattery();
            this.batteryManager = manager;
            const read = () => {
                this.battery = Number.isFinite(manager.level)
                    ? Math.round(manager.level * 100) : null;
            };
            read();
            manager.addEventListener("levelchange", read);
        } catch {
            // Safari and Firefox do not say; the server takes null.
            this.battery = null;
        }
    }
}

/** 'YYYY-MM-DD HH:MM:SS' in UTC, which is what the server parses. */
function stamp(when) {
    return when.toISOString().slice(0, 19).replace("T", " ");
}

/** Metres between two fixes. The same equirectangular approximation the server uses;
    over the few hundred metres that decide "did they move", it is exact enough. */
function metres(lat1, lon1, lat2, lon2) {
    const R = 6371000;
    const rad = Math.PI / 180;
    const x = (lon2 - lon1) * rad * Math.cos(((lat1 + lat2) / 2) * rad);
    const y = (lat2 - lat1) * rad;
    return Math.sqrt(x * x + y * y) * R;
}

export const liveTrackService = {
    dependencies: ["orm"],
    start(env, { orm }) {
        // The notification plugin, the way the core's own notification service gets
        // it. Only the permission warning uses it, so a context without the plugin
        // tree (a test, a bare page) simply loses that one dialog.
        let notification = null;
        try {
            notification = usePlugin(NotificationPlugin);
        } catch {
            notification = null;
        }
        const tracker = new LiveTracker(orm, notification);

        // Ask once at load, then on a slow timer. Five minutes is the outside delay
        // between ending a day somewhere else and this phone noticing — short enough
        // to matter, long enough that it costs nothing.
        tracker.refresh();
        tracker.pollTimer = setInterval(() => tracker.refresh(), POLL_MS);

        // Coming back to a backgrounded tab is the moment most likely to follow a
        // suspended watch, so re-check then as well (and take the wake lock back,
        // which the OS dropped on the way out); going away is the last chance to
        // post what is in hand before the tab is discarded.
        document.addEventListener("visibilitychange", () => {
            if (document.visibilityState === "visible") {
                tracker.cameToFront();
                tracker.refresh();
                if (tracker.recording) {
                    tracker.requestWakeLock();
                }
            } else {
                tracker.wentBackground();
                tracker.flush();
            }
        });
        window.addEventListener("pagehide", () => tracker.flush());
        // The Lab app says the location permission was just granted: ask the
        // server again, and start the recorder only if the day is running - the
        // app itself never starts recording. (client, 2026-10-20)
        window.addEventListener("labapp", () => tracker.refresh());

        return tracker;
    },
};

registry.category("services").add("lab_live_track", liveTrackService);
