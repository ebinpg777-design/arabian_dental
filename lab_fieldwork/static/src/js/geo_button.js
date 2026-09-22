/** @odoo-module **/

import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";

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
    }

    get label() {
        return this.props.label || "Check In";
    }

    async onClick() {
        const record = this.props.record;
        if (record.isDirty) {
            await record.save();
        }
        const coords = await this.getPosition();
        if (!coords) {
            this.notification.add(
                "Turn on Location for this browser, wait for the arrow to appear, " +
                "then try again. A visit cannot be recorded without it.",
                { type: "danger", title: "Location is off", sticky: true }
            );
            return;
        }
        await this.orm.call(record.resModel, this.props.method, [[record.resId]], {
            latitude: coords.latitude,
            longitude: coords.longitude,
        });
        await record.model.root.load();
    }

    getPosition() {
        return new Promise((resolve) => {
            if (!navigator.geolocation) {
                return resolve(null);
            }
            navigator.geolocation.getCurrentPosition(
                (pos) => resolve(pos.coords),
                () => resolve(null),
                { enableHighAccuracy: true, timeout: 8000, maximumAge: 0 }
            );
        });
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
