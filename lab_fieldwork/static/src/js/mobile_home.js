/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { browser } from "@web/core/browser/browser";
import { user } from "@web/core/user";
import { NavBar } from "@web/webclient/navbar/navbar";

/**
 * A way home, on a phone.
 *
 * On a small screen Odoo's navigation bar drops the app name and the section
 * menus and leaves only the systray: an executive who opened Collections from
 * My Day is looking at a screen with no visible way home, and the burger that
 * would take them there is the one icon they do not recognise. (client, 2026-08-28)
 *
 * Home is the first thing on the bar and never gives up its room: it used to
 * carry the app's name and shrank to nothing on a form, where the record's own
 * back arrow and actions crowd the bar, so on a delivery it was not there at
 * all. It goes where the person's day starts - My Day for an executive (the
 * home action the server hands them) - not to whichever app they happen to be
 * in. (client, 2026-11-24)
 */
patch(NavBar.prototype, {
    /** Whether there is anywhere to go home to. */
    get fwHasHome() {
        return Boolean(user.homeActionId || this.currentApp);
    },

    /** The previous screen, if there is one in this session; home otherwise. */
    fwGoBack() {
        if (browser.history.length > 1) {
            browser.history.back();
        } else {
            this.fwGoHome();
        }
    },

    /** Where the person's day starts: their home action, else the app's first screen. */
    fwGoHome() {
        if (user.homeActionId) {
            return this.actionService.doAction(user.homeActionId, { clearBreadcrumbs: true });
        }
        const app = this.currentApp;
        if (app) {
            return this.menuService.selectMenu(app);
        }
    },
});
