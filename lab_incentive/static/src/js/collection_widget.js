/** @odoo-module **/

import { Component, onWillStart, proxy, useProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

const CHEQUE_GROUP = "lab_finance_ops.group_lab_cheque_user";

/**
 * The month's money on an incentive sheet (Collected basis, client 2026-11-23).
 *
 * Tiles: what counted (cash & bank, cheques cleared - also those received in an earlier
 * month), what did not (cheques not cleared, bounced). Below them every cheque's way
 * through the month on one bar: received, deposited, cleared or bounced, so whether a
 * cheque counted - and in which month - is read off the bar, not worked out from dates.
 * Everything comes from the JSON the server computed with the sheet.
 */
export class LabCollectionWidget extends Component {
    static template = "lab_incentive.CollectionWidget";
    props = useProps(standardFieldProps);

    setup() {
        this.action = useService("action");
        this.access = proxy({ cheques: false });
        onWillStart(async () => {
            this.access.cheques = await user.hasGroup(CHEQUE_GROUP);
        });
    }

    get data() {
        return this.props.record.data[this.props.name] || {};
    }

    get tiles() {
        return this.data.tiles || [];
    }

    get cheques() {
        return this.data.cheques || [];
    }

    /** The coloured stretch of a cheque's bar: from its first day in view to the day
     *  it cleared or bounced - or on to the month's end while it waits. */
    trackStyle(cheque) {
        const events = cheque.events || [];
        if (!events.length) {
            return "display: none";
        }
        const start = events[0].pos;
        const last = events[events.length - 1];
        const settled = ["cleared", "bounced"].includes(last.kind);
        const end = settled ? last.pos : 100;
        return `left: ${start}%; width: ${Math.max(end - start, 0.8)}%`;
    }

    markerStyle(event) {
        return `left: ${event.pos}%`;
    }

    eventTitle(event) {
        const words = {
            received: "Received",
            deposited: "Deposited",
            cleared: "Cleared",
            bounced: "Bounced",
        };
        return `${words[event.kind] || event.kind} ${event.label}`;
    }

    openCheque(cheque) {
        if (!this.access.cheques) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "lab.cheque",
            res_id: cheque.id,
            views: [[false, "form"]],
        });
    }
}

registry.category("fields").add("lab_collection_widget", {
    component: LabCollectionWidget,
    supportedTypes: ["json"],
});
