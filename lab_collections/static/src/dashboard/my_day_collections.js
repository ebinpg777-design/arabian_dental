/** @odoo-module **/
import { patch } from "@web/core/utils/patch";
import { LabMyDay } from "@lab_fieldwork/js/my_day";

/**
 * Statement and open receivable from a Doctors / Clinics card - the money
 * questions a doctor asks at the counter, answered where the executive already
 * is rather than two apps away in Collections. (client, 2026-09-17)
 */
patch(LabMyDay.prototype, {
    async openClinicStatement(clinic, ev) {
        ev?.stopPropagation();
        const action = await this.orm.call(
            "lab.collection.performance", "action_statement_for_partner", [clinic.id]);
        if (action) {
            await this.action.doAction(action);
        }
    },

    async openClinicReceivable(clinic, ev) {
        ev?.stopPropagation();
        const action = await this.orm.call(
            "lab.collection.performance", "action_open_items_for_partner", [clinic.id]);
        if (action) {
            await this.action.doAction(action);
        }
    },
});
