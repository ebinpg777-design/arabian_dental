/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { browser } from "@web/core/browser/browser";
import { NavBar } from "@web/webclient/navbar/navbar";

/**
 * A way back, on a phone.
 *
 * On a small screen Odoo's navigation bar drops the app name and the section
 * menus and leaves only the systray: an executive who opened Collections from
 * My Day is looking at a screen with no visible way home, and the burger that
 * would take them there is the one icon they do not recognise. Two controls at
 * the left of the bar, mobile only: one step back, and the app's own front
 * door. (client, 2026-08-28)
 */
patch(NavBar.prototype, {
    /** The previous screen, if there is one in this session; the app home otherwise. */
    fwGoBack() {
        if (browser.history.length > 1) {
            browser.history.back();
        } else {
            this.fwGoHome();
        }
    },

    /** The app's first screen: what "the main menu" means to whoever is holding the phone. */
    fwGoHome() {
        const app = this.currentApp;
        if (app) {
            this.menuService.selectMenu(app);
        }
    },
});
