/** @odoo-module **/

/**
 * One way to ask the browser where the executive is, used by every screen that needs it.
 *
 * Three screens each had their own copy of "getCurrentPosition, high accuracy, eight
 * seconds, nothing cached", and all three misread the same situations (client,
 * 2026-09-29):
 *
 * - The first time a browser is asked, Chrome shows "Allow location?". My Day's own
 *   ten-second guard kept running while the executive read that question, gave up
 *   under them, started the day with no position and said "Location is off" - a
 *   sticky warning that stayed on screen after they had pressed Allow.
 * - Every failure was reported as "turn on Location", whether Chrome had blocked the
 *   site, the phone's own location was off, the GPS simply had not found itself yet
 *   indoors, or the page was opened over plain http - where no permission can help,
 *   because browsers share a position only with secure pages.
 * - High accuracy and no cached fix meant a desktop, or a phone inside a building,
 *   often timed out although a perfectly good position was available.
 *
 * `locate()` settles on {coords} or {error}. It waits as long as the permission
 * question is open, tries GPS first and the network position second, accepts a fix
 * that is a few seconds old, and names what actually went wrong.
 */

// How long we wait for an answer once the browser may look. The platform's own
// timeout is not enough on its own: a WebView that was never granted the permission
// can drop the request and call neither callback, so each attempt has a guard of ours.
const GPS = { enableHighAccuracy: true, timeout: 15000, maximumAge: 30000 };
const NETWORK = { enableHighAccuracy: false, timeout: 10000, maximumAge: 120000 };
// While Chrome's question is on screen the executive is reading it; a guard that runs
// out underneath them is the bug this file exists to fix.
const WHILE_ASKING_MS = 90000;

async function permissionState() {
    try {
        if (navigator.permissions && navigator.permissions.query) {
            const status = await navigator.permissions.query({ name: "geolocation" });
            return status.state; // "granted" | "prompt" | "denied"
        }
    } catch {
        // Older Safari and some WebViews cannot be asked; carry on and find out.
    }
    return "unknown";
}

function attempt(options, guardMs) {
    return new Promise((resolve) => {
        let settled = false;
        const finish = (result) => {
            if (!settled) {
                settled = true;
                clearTimeout(guard);
                resolve(result);
            }
        };
        const guard = setTimeout(() => finish({ error: "timeout" }), guardMs);
        try {
            navigator.geolocation.getCurrentPosition(
                (position) => finish({ coords: position.coords }),
                (error) => finish({
                    error: error && error.code === 1 ? "denied"
                        : error && error.code === 2 ? "unavailable" : "timeout",
                }),
                options
            );
        } catch {
            finish({ error: "unavailable" });
        }
    });
}

/**
 * Both readings at once, the better one kept (client, 2026-11-25: "Start my day
 * takes a long time"). Asked one after the other, a phone indoors without a GPS
 * lock waited out the GPS timeout before the network was even asked: up to 35
 * seconds behind the button. Now GPS and the network position are asked together:
 *
 * - `purpose: "day"` (Start my day / End the day) takes the first position that
 *   arrives, a few minutes old or not - the day's start needs the town, not the
 *   doorstep, and the recorder takes over from there;
 * - `purpose: "visit"` (I'm Here) waits a few seconds for GPS, because the server
 *   measures the fix against the clinic, then takes a network position that is
 *   precise enough rather than none.
 */
const FAST = {
    day: { gpsAge: 300000, netAge: 300000, preferGpsMs: 0, netEnoughM: Infinity },
    visit: { gpsAge: 30000, netAge: 60000, preferGpsMs: 8000, netEnoughM: 150 },
};
const HARD_MS = 15000;

async function race(purpose, guardMs) {
    const rule = FAST[purpose] || FAST.visit;
    const started = Date.now();
    return new Promise((resolve) => {
        let done = false;
        let net = null;
        let errors = [];
        let pending = 2;
        const finish = (result) => {
            if (!done) {
                done = true;
                clearTimeout(hard);
                clearTimeout(soft);
                resolve(result);
            }
        };
        const settleNet = () => {
            if (!net) {
                return;
            }
            const waited = Date.now() - started;
            const precise = (net.coords.accuracy || Infinity) <= rule.netEnoughM;
            if (rule.preferGpsMs === 0 || (precise && waited >= rule.preferGpsMs)) {
                finish(net);
            }
        };
        const failed = (error) => {
            errors.push(error);
            pending -= 1;
            if (pending === 0) {
                finish(net || { error: errors.includes("denied") ? "denied" : errors[0] });
            }
        };
        const hard = setTimeout(() => finish(net || { error: "timeout" }), guardMs);
        const soft = rule.preferGpsMs ? setTimeout(settleNet, rule.preferGpsMs) : null;
        attempt({ ...GPS, maximumAge: rule.gpsAge }, guardMs).then((result) => {
            if (result.coords) {
                finish(result);
            } else {
                failed(result.error);
            }
        });
        attempt({ ...NETWORK, maximumAge: rule.netAge }, guardMs).then((result) => {
            if (result.coords) {
                net = result;
                pending -= 1;
                settleNet();
                if (pending === 0) {
                    finish(net);
                }
            } else {
                failed(result.error);
            }
        });
    });
}

/** Where is this browser? Resolves {coords} or {error}; never rejects, never hangs. */
export async function locate({ purpose = "visit" } = {}) {
    if (!navigator.geolocation) {
        return { error: "unsupported" };
    }
    if (window.isSecureContext === false) {
        // Chrome refuses at once on http://, whatever the site setting says.
        return { error: "insecure" };
    }
    const state = await permissionState();
    if (state === "denied") {
        return { error: "denied" };
    }
    // While Chrome's question is on screen the executive is reading it: the guard
    // waits for them, as before.
    return race(purpose, state !== "granted" ? WHILE_ASKING_MS : HARD_MS);
}

const REASONS = {
    unsupported: [
        "This browser cannot share a location",
        "Open the app in Google Chrome.",
    ],
    insecure: [
        "Location needs the secure address",
        "This page was opened over http://, and browsers only share a location " +
        "with https:// pages - allowing location in Chrome cannot change that. " +
        "Open the https:// address of the app instead.",
    ],
    denied: [
        "Location is blocked for this site",
        "Chrome has location switched off for this site. Tap the icon to the left " +
        "of the address, open Permissions (Site settings), set Location to Allow, " +
        "then reload the page.",
    ],
    unavailable: [
        "Your device did not give a position",
        "Chrome is allowed, but the device's own location is off. On a phone, " +
        "switch on Location in the quick settings. On a computer, turn on Location " +
        "services (Windows: Settings > Privacy & security > Location; Mac: System " +
        "Settings > Privacy & Security > Location Services > Google Chrome).",
    ],
    timeout: [
        "No position yet",
        "The phone is still finding its position. Step outside or near a window " +
        "for a moment, then try again.",
    ],
};

let closeLast = null;

/** Say why there is no position. `consequence` is what could not happen because of it. */
export function warnNoPosition(notification, error, consequence, { sticky = true } = {}) {
    clearPositionWarning();
    const [title, text] = REASONS[error] || REASONS.timeout;
    closeLast = notification.add(consequence ? `${text} ${consequence}` : text, {
        type: sticky ? "danger" : "warning",
        title,
        sticky,
    });
}

/** A position has arrived: the warning about not having one is no longer true. */
export function clearPositionWarning() {
    if (closeLast) {
        try {
            closeLast();
        } catch {
            // already closed by hand
        }
        closeLast = null;
    }
}
