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

/** Where is this browser? Resolves {coords} or {error}; never rejects, never hangs. */
export async function locate() {
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
    const asking = state !== "granted";
    let result = await attempt(GPS, asking ? WHILE_ASKING_MS : GPS.timeout + 5000);
    if (result.error === "timeout" || result.error === "unavailable") {
        // No GPS lock yet - indoors, or a computer with no GPS at all. The network
        // position still says which street, and a recent one will do.
        const second = await attempt(NETWORK, NETWORK.timeout + 5000);
        if (second.coords) {
            result = second;
        }
    }
    return result;
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
