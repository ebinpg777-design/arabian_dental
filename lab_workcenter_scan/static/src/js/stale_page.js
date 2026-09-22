/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";

/**
 * Tell a bench that its screen is running yesterday's code.
 *
 * Odoo already broadcasts `bundle_changed` when it rebuilds an asset bundle, and
 * `bus.assets_watchdog` already knows how to offer a Refresh — but it offers it only
 * when the payload's `server_version` differs from the session's, and that value is
 * Odoo's own release ("19.0"). A module deploy never changes it, so the notification
 * the watchdog exists to show can never appear for ours: core compares a constant to
 * itself (bus/static/src/services/assets_watchdog_service.js:13, and the sender at
 * base/models/assetsbundle.py:305).
 *
 * On a shop floor that matters more than anywhere else. The board is opened once in
 * the morning and left open on a phone in an apron all day, so a deploy leaves every
 * bench silently running the old client: a new question is not asked, a new button is
 * not there, and the floor reports a broken feature that works perfectly on the
 * server. That is exactly how "the finisher popup is not coming" arrived on
 * 2026-09-09 — the server was answering `needs_finisher` and the morning's client,
 * which had never heard of it, showed the message as a toast instead.
 *
 * The version is checked AGAINST THE SERVER rather than assumed from the broadcast,
 * so a screen that is already current is never nagged. A prompt people learn to
 * dismiss is worse than no prompt at all. (client, 2026-09-09)
 */

/** The bundle hash this page actually loaded, read off its own script tag. */
function loadedAssetVersion() {
    for (const tag of document.querySelectorAll('script[src*="/web/assets/"]')) {
        const match = /\/web\/assets\/([^/]+)\/[^/]*assets_backend/.exec(tag.src || "");
        if (match) {
            return match[1];
        }
    }
    return null;
}

export const stalePageService = {
    dependencies: ["bus_service", "notification"],

    start(env, { bus_service, notification }) {
        const loaded = loadedAssetVersion();
        if (!loaded) {
            // Nothing to compare against (a debug build, a test harness). Core's own
            // watchdog is still in place, and guessing is worse than staying quiet.
            return;
        }
        let asked = false;

        bus_service.subscribe("bundle_changed", async () => {
            if (asked) {
                return;
            }
            let current;
            try {
                current = await rpc("/lab_workcenter_scan/assets_version", {});
            } catch {
                return; // Offline, or no session: not the moment to nag anybody.
            }
            if (!current?.versions?.length || current.versions.includes(loaded)) {
                return; // Already running what the server would serve.
            }
            asked = true;
            notification.add(
                _t("This screen is running an older version. Reload to pick up the "
                   + "latest — anything half-finished on it will be lost."),
                {
                    title: _t("A newer version is ready"),
                    type: "warning",
                    sticky: true,
                    buttons: [{
                        name: _t("Reload now"),
                        primary: true,
                        onClick: () => browser.location.reload(),
                    }],
                    // Dismissed on purpose. Ask again if it changes AGAIN, but never
                    // re-open the same prompt behind somebody's hands.
                    onClose: () => {
                        asked = false;
                    },
                }
            );
        });
        bus_service.start();
    },
};

registry.category("services").add("lab_workcenter_scan.stale_page", stalePageService);
