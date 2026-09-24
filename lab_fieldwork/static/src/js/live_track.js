/** @odoo-module **/

import { registry } from "@web/core/registry";

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
 */
export class LiveTracker {
    constructor(orm) {
        this.orm = orm;
        this.watchId = null;
        this.buffer = [];
        this.last = null;              // the last fix we decided to send
        this.enabled = false;
        this.onDuty = false;
        this.interval = 90;
        this.flushTimer = null;
        this.pollTimer = null;
        this.failures = 0;
    }

    get running() {
        return this.watchId !== null;
    }

    /**
     * Ask the server whether the watch should be on, and act on the answer.
     *
     * Called at start-up, on a slow timer, and by My Day the moment the attendance
     * button is pressed. Routing every decision through one server answer is what makes
     * "I ended my day on the office computer" stop the watch on the phone as well.
     */
    async refresh() {
        let state;
        try {
            state = await this.orm.call("lab.my.day", "tracking_state", []);
        } catch {
            // A failed check is not a reason to start recording, nor to throw away a
            // watch that is working: leave things exactly as they are and ask again.
            return;
        }
        this.enabled = Boolean(state.enabled);
        this.onDuty = Boolean(state.on_duty);
        this.interval = state.interval || 90;
        if (this.enabled && this.onDuty) {
            this.start();
        } else {
            this.stop();
        }
    }

    start() {
        if (this.running || !navigator.geolocation) {
            return;
        }
        this.watchId = navigator.geolocation.watchPosition(
            (position) => this.take(position.coords, position.timestamp),
            () => {
                // Permission refused, or no fix. Neither is an error worth a dialog:
                // the executive is working, not debugging, and the day sheet already
                // shows an unlocated round for what it is.
                this.failures += 1;
                if (this.failures >= 5) {
                    this.stop();
                }
            },
            { enableHighAccuracy: true, maximumAge: 15000, timeout: 30000 }
        );
        // watchPosition fires as fast as the hardware reports, which on a moving phone
        // is several times a second. The buffer is drained on our own clock instead.
        this.flushTimer = setInterval(() => this.flush(), this.interval * 1000);
    }

    stop() {
        if (this.flushTimer) {
            clearInterval(this.flushTimer);
            this.flushTimer = null;
        }
        if (this.watchId !== null) {
            navigator.geolocation.clearWatch(this.watchId);
            this.watchId = null;
        }
        // Whatever is still in hand belongs to the day that has just ended, and the
        // server will file it against the attendance window it was taken in.
        this.flush();
        this.last = null;
        this.failures = 0;
    }

    /**
     * Keep a fix if it says something new.
     *
     * The server drops repeats too — it has to, because it cannot trust a client — but
     * doing it here as well is what keeps a phone parked at a clinic for two hours from
     * posting eighty identical positions over a mobile connection.
     */
    take(coords, timestamp) {
        this.failures = 0;
        if (!coords || (!coords.latitude && !coords.longitude)) {
            return;
        }
        const when = new Date(timestamp || Date.now());
        if (this.last) {
            const moved = metres(this.last.latitude, this.last.longitude,
                                 coords.latitude, coords.longitude);
            const waited = (when - this.last.when) / 1000;
            if (moved < 50 && waited < 300) {
                return;
            }
        }
        this.last = { latitude: coords.latitude, longitude: coords.longitude, when };
        this.buffer.push({
            latitude: coords.latitude,
            longitude: coords.longitude,
            accuracy_m: coords.accuracy || 0,
            // The server stores UTC; sending anything else files the round under the
            // wrong day for the five and a half hours that matter most here.
            ts: when.toISOString().slice(0, 19).replace("T", " "),
        });
        if (this.buffer.length >= 25) {
            this.flush();
        }
    }

    async flush() {
        if (!this.buffer.length) {
            return;
        }
        const batch = this.buffer;
        this.buffer = [];
        try {
            await this.orm.silent.call("lab.location.ping", "record", [batch]);
        } catch {
            // Out of coverage, most likely — half this job happens between towns. Put
            // the batch back at the front and try again on the next tick, but do not
            // let an all-day outage grow without limit.
            this.buffer = batch.concat(this.buffer).slice(-200);
        }
    }
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
        const tracker = new LiveTracker(orm);

        // Ask once at load, then on a slow timer. Five minutes is the outside delay
        // between ending a day somewhere else and this phone noticing — short enough
        // to matter, long enough that it costs nothing.
        tracker.refresh();
        tracker.pollTimer = setInterval(() => tracker.refresh(), 300000);

        // Coming back to a backgrounded tab is the moment most likely to follow a
        // suspended watch, so re-check then as well; and going away is the last chance
        // to post what is in hand before the tab is discarded.
        document.addEventListener("visibilitychange", () => {
            if (document.visibilityState === "visible") {
                tracker.refresh();
            } else {
                tracker.flush();
            }
        });
        window.addEventListener("pagehide", () => tracker.flush());

        return tracker;
    },
};

registry.category("services").add("lab_live_track", liveTrackService);
