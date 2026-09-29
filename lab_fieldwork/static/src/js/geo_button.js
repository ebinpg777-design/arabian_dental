/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { session } from "@web/session";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";
import { clearPositionWarning, locate, warnNoPosition } from "@lab_fieldwork/js/position";

/**
 * A header button that captures the browser's location before calling its method.
 *
 * The location must be taken in the browser and passed to the server — a server-side
 * check-in has no idea where anyone is. A plain <button> calling the same method would
 * open the visit with an empty geo-stamp, which is worse than no geo-fence at all
 * because the record then claims to be verified.
 *
 * A refused or unavailable fix now BLOCKS the call. The module used to record the
 * visit anyway and show it as locationless, on the reasoning that a lost visit is
 * worse than an unexplained one; on the live data that produced day sheets of thirty
 * visits stamped within the same minute with zero kilometres travelled, which is a day
 * typed at a desk. The server refuses these too — this is the polite half of the same
 * rule, so the executive is told what to switch on instead of meeting a traceback.
 * (client, 2026-09-05)
 */
export class FwGeoButton extends Component {
    static template = "lab_fieldwork.GeoButton";
    static props = {
        ...standardWidgetProps,
        method: { type: String },
        label: { type: String, optional: true },
        icon: { type: String, optional: true },
        btn_class: { type: String, optional: true },
    };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        // While the browser looks for a position - which includes the time Chrome's
        // "Allow location?" question is on screen - the button is off. Every extra tap
        // used to queue another request, and the moment Allow was pressed they all
        // reached the server together: one opened the visit, the next was told "This
        // visit is not waiting to be started". (client, 2026-09-29)
        this.state = useState({ busy: false });
    }

    get label() {
        return this.props.label || "Check In";
    }

    async onClick() {
        if (this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            const record = this.props.record;
            if (record.isDirty) {
                await record.save();
            }
            // An excused phone is not asked, and not warned. (client, 2026-09-28)
            const exempt = Boolean(session.fw_location_exception);
            let coords = null;
            if (!exempt) {
                const found = await locate();
                if (!found.coords) {
                    warnNoPosition(this.notification, found.error,
                        "A visit cannot be recorded without it.");
                    return;
                }
                clearPositionWarning();
                coords = found.coords;
            }
            await this.orm.call(record.resModel, this.props.method, [[record.resId]], {
                latitude: coords ? coords.latitude : false,
                longitude: coords ? coords.longitude : false,
            });
            await record.model.root.load();
        } finally {
            this.state.busy = false;
        }
    }
}

export const fwGeoButton = {
    component: FwGeoButton,
    extractProps: ({ attrs }) => ({
        method: attrs.method,
        label: attrs.label,
        icon: attrs.icon,
        btn_class: attrs.btn_class,
    }),
};

registry.category("view_widgets").add("fw_geo_button", fwGeoButton);
