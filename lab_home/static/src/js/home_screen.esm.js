/** @odoo-module **/

/**
 * The warning strip on the app grid.
 *
 * The warnings themselves are worked out on the server and travel on the
 * session (see models/ir_http.py), so landing on the home screen costs nothing
 * extra. This only decides what to do when one is clicked.
 *
 * It patches the THEME's home menu, which is the grid this database actually
 * shows - neither the stock web one nor the responsive drawer.
 */
import {patch} from "@web/core/utils/patch";
import {useService} from "@web/core/utils/hooks";
import {useState} from "@odoo/owl";
import {session} from "@web/session";
import {HomeMenu} from "@zxs_entp_theme/webclient/home_menu/home_menu";

patch(HomeMenu.prototype, {
    setup() {
        super.setup();
        this.actionService = useService("action");
        this.homeAlerts = session.home_alerts || [];
        // Dismissal lasts as long as the page does. A backup that is failing is
        // still failing tomorrow, and this warning should come back rather than
        // be turned off once and forgotten.
        this.alertState = useState({dismissed: false});
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
