/** @odoo-module **/

/**
 * The lab's home screen: which wallpaper is on it, and the warning strip above
 * the apps.
 *
 * Both are decided on the server and travel on the session (see
 * models/ir_http.py), so landing on the home screen costs no extra round trip.
 *
 * The component patched is the THEME's home menu, which is the app grid this
 * database actually shows - neither the stock web one nor the responsive
 * drawer.
 */
import {patch} from "@web/core/utils/patch";
import {registry} from "@web/core/registry";
import { onMounted, onPatched, onWillUnmount, proxy } from "@odoo/owl";
import {useService} from "@web/core/utils/hooks";
import {session} from "@web/session";
import {HomeMenu} from "@ebshel_backend_theme/webclient/home_menu/home_menu";

/**
 * The wallpaper is a class on <body>, set once when the client starts rather
 * than when the home menu mounts: the background belongs to the whole client,
 * and setting it from the component would show the default for as long as it
 * takes that component to appear.
 */
registry.category("services").add("lab_home_wallpaper", {
    start() {
        const wallpaper = session.home_wallpaper;
        if (!wallpaper || !wallpaper.name) {
            return;
        }
        document.body.classList.add(`o_lab_wall_${wallpaper.name}`);
        if (wallpaper.dark) {
            document.body.classList.add("o_lab_wall_dark");
        }
    },
});

patch(HomeMenu.prototype, {
    setup() {
        super.setup();
        this.actionService = useService("action");
        this.homeAlerts = session.home_alerts || [];
        // Dismissal lasts as long as the page does. A backup that is failing is
        // still failing tomorrow, and this warning should come back rather than
        // be turned off once and forgotten.
        this.alertState = proxy({dismissed: false});

        // The apps scroll above the wordmark's band, and while more of them are
        // below, the bottom edge fades out instead of cutting a row in half
        // (home_screen.scss). Scrolled to the end, or with every app on screen,
        // there is nothing to hint at and nothing fades. Scroll and resize move
        // the edge; a search narrowing the apps re-renders, hence onPatched.
        this.labUpdateMoreBelow = () => this.labSetMoreBelow();
        onMounted(() => {
            const root = this.rootRef();
            if (!root) {
                return;
            }
            root.addEventListener("scroll", this.labUpdateMoreBelow, {passive: true});
            this.labResizeObserver = new ResizeObserver(this.labUpdateMoreBelow);
            this.labResizeObserver.observe(root);
            this.labSetMoreBelow();
        });
        onPatched(() => this.labSetMoreBelow());
        onWillUnmount(() => {
            this.rootRef()?.removeEventListener("scroll", this.labUpdateMoreBelow);
            this.labResizeObserver?.disconnect();
        });
    },

    /**
     * A data attribute rather than a class: OWL owns the root's class list
     * (the template sets it), and leaves attributes it never wrote alone.
     */
    labSetMoreBelow() {
        const root = this.rootRef();
        if (!root) {
            return;
        }
        const below = root.scrollHeight - root.clientHeight - root.scrollTop;
        root.toggleAttribute("data-lab-more-below", below > 1);
    },

    get visibleHomeAlerts() {
        return this.alertState.dismissed ? [] : this.homeAlerts;
    },

    async openHomeAlert(alert) {
        if (!alert.action) {
            return;
        }
        await this.actionService.doAction(alert.action);
    },

    dismissHomeAlerts() {
        this.alertState.dismissed = true;
    },
});
