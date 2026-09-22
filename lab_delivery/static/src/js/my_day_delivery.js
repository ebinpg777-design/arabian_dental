/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { LabMyDay } from "@lab_fieldwork/js/my_day";
import { _t } from "@web/core/l10n/translation";
import { ScanStation } from "@lab_delivery/js/scan_station";

/**
 * Deliveries on the executive's home screen.
 *
 * Same rules as the rest of My Day: one button per card, the state decides what it
 * does, and every action reloads the screen rather than mutating a local copy — an
 * executive re-reads this screen all day and it must never disagree with the server.
 */
patch(LabMyDay.prototype, {
    get deliveries() {
        return (this.state.data && this.state.data.deliveries) || [];
    },

    get deliverySummary() {
        return (this.state.data && this.state.data.delivery_summary) || {};
    },

    /**
     * Raise a delivery from where the executive is standing.
     *
     * Passing the visit is what links the box to the trip; without it the two records
     * only ever met by coincidence of clinic and date.
     */
    async newDelivery(visit) {
        const action = await this.orm.call(
            "lab.my.day",
            "action_new_delivery",
            [visit ? visit.id : false]
        );
        this.action.doAction(action, {
            onClose: () => this.load(),
        });
    },

    /**
     * Open the Scan Station: a full-screen hand-over counter.
     *
     * One press, and the whole doorstep visit happens inside it - live camera on
     * https, photo capture over plain http, typing, or a Bluetooth wedge gun -
     * with every box scanned landing as a card and the confirm wizard opening on
     * top. My Day reloads when the station closes, so the numbers underneath
     * match what just left the bag. (client, 2026-08-28)
     */
    scanDelivery() {
        this.env.services.dialog.add(ScanStation, {}, { onClose: () => this.load() });
    },

    /** Everything waiting to be delivered, whichever clinic it is for. */
    async openDeliveryWorklist() {
        const action = await this.orm.call(
            "sale.order", "action_lab_delivery_worklist", []
        );
        this.action.doAction(action, { onClose: () => this.load() });
    },

    /** The return leg: collect an impression from the doctor for the lab. */
    async newPickup(visit) {
        const action = await this.orm.call(
            "lab.my.day",
            "action_new_delivery",
            [visit ? visit.id : false, "in"]
        );
        this.action.doAction(action, {
            onClose: () => this.load(),
        });
    },

    onDeliveryPrimary(d) {
        if (!d.out) {
            return this.setOff(d);
        }
        // An inbound bag is finished by the LAB receiving it, not by handing it to the
        // doctor it came from. One button, two journeys — so it has to read the
        // direction. (client, 2026-08-27)
        return d.direction === "in" ? this.receiveAtLab(d) : this.markDelivered(d);
    },

    /** The inbound mirror of Mark Delivered: the lab takes the bag in. */
    async receiveAtLab(d) {
        const coords = await this.getPosition();
        const action = await this.orm.call("lab.delivery", "action_receive_at_lab", [
            [d.id],
            coords ? coords.latitude : false,
            coords ? coords.longitude : false,
            coords ? coords.accuracy : false,
        ]);
        this.action.doAction(action, { onClose: () => this.load() });
    },

    async setOff(d) {
        if (this.state.busy) {
            return;
        }
        this.state.busy = "d" + d.id;
        try {
            await this.orm.call("lab.delivery", "action_start", [[d.id]]);
            await this.load();
        } finally {
            this.state.busy = 0;
        }
    },

    /**
     * Handing it over needs an answer that cannot be guessed from a card — clinic or
     * near place, and if near, where. So it opens the same wizard the desk uses.
     */
    async markDelivered(d) {
        // The fix is taken at THIS moment - pressing Delivered - because that is
        // when standing at the clinic means something. (client, 2026-08-28)
        const coords = await this.getPosition();
        const action = await this.orm.call("lab.delivery", "action_mark_delivered", [
            [d.id],
            coords ? coords.latitude : false,
            coords ? coords.longitude : false,
            coords ? coords.accuracy : false,
        ]);
        this.action.doAction(action, {
            onClose: () => this.load(),
        });
    },

    openDelivery(d) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "lab.delivery",
            res_id: d.id,
            views: [[false, "form"]],
        });
    },
});
