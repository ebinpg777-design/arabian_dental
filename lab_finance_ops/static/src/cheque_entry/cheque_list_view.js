/** @odoo-module **/

import { Component, onWillStart, onWillUnmount, proxy, t, useProps } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { useService } from "@web/core/utils/hooks";
import { ListController } from "@web/views/list/list_controller";
import { ListRenderer } from "@web/views/list/list_renderer";
import { listView } from "@web/views/list/list_view";

import { ChequeEntryDialog } from "./cheque_entry_dialog";

const CHANGED = "LAB_CHEQUES:CHANGED";
const GROUP = "lab_finance_ops.group_lab_cheque_user";

/** Customer payments, or the cheque register itself - not vendor payments. */
function forCustomerCheques(resModel, context) {
    return resModel === "lab.cheque" || (context || {}).default_partner_type === "customer"
        || (context || {}).default_payment_type === "inbound";
}

/**
 * The cheques at a glance, above Customer Payments and the Cheque Register: what is in
 * hand, due for deposit today, falling due this week, in the bank and bounced. Each one
 * opens those cheques.
 */
export class ChequeStrip extends Component {
    static template = "lab_finance_ops.ChequeStrip";
    props = useProps({ onAdd: t.function().optional() });

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = proxy({ allowed: false, summary: null });
        onWillStart(async () => {
            this.state.allowed = await user.hasGroup(GROUP);
            if (this.state.allowed) {
                await this.load();
            }
        });
        const reload = () => this.state.allowed && this.load();
        this.env.bus.addEventListener(CHANGED, reload);
        onWillUnmount(() => this.env.bus.removeEventListener(CHANGED, reload));
    }

    async load() {
        this.state.summary = await this.orm.call("lab.cheque", "lab_cheque_summary", []);
    }

    get tiles() {
        const s = this.state.summary;
        if (!s) {
            return [];
        }
        return [
            { key: "in_hand", label: _t("In hand"), ...s.in_hand, kind: "" },
            { key: "due", label: _t("Due for deposit"), ...s.due, kind: s.due.count ? "warn" : "" },
            { key: "week", label: _t("Due in 7 days"), ...s.week, kind: "" },
            { key: "deposited", label: _t("In the bank"), ...s.deposited, kind: "" },
            { key: "bounced", label: _t("Bounced"), ...s.bounced, kind: s.bounced.count ? "bad" : "" },
        ];
    }

    money(value) {
        const currency = (this.state.summary && this.state.summary.currency) || {};
        const text = Number(value || 0).toLocaleString("en-IN", { maximumFractionDigits: 0 });
        return currency.position === "after" ? `${text} ${currency.symbol || ""}` : `${currency.symbol || ""} ${text}`;
    }

    open(tile) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: _t("Cheques · %s", tile.label),
            res_model: "lab.cheque",
            views: [[false, "list"], [false, "form"]],
            domain: tile.domain,
            target: "current",
        });
    }
}

export class LabChequeListController extends ListController {
    setup() {
        super.setup();
        this.chequeDialog = useService("dialog");
        this.chequeAccess = proxy({ allowed: false });
        onWillStart(async () => {
            this.chequeAccess.allowed = await user.hasGroup(GROUP);
        });
    }

    get showChequeEntry() {
        return this.chequeAccess.allowed && forCustomerCheques(this.props.resModel, this.props.context);
    }

    openChequeEntry() {
        this.chequeDialog.add(ChequeEntryDialog, {
            onSaved: async () => {
                await this.model.load();
                this.env.bus.trigger(CHANGED);
            },
        });
    }
}

export class LabChequeListRenderer extends ListRenderer {
    static template = "lab_finance_ops.ChequeListRenderer";
    static components = { ...ListRenderer.components, ChequeStrip };

    get showChequeStrip() {
        const list = this.props.list;
        return forCustomerCheques(list.resModel, list.context);
    }
}

export const labChequeListView = {
    ...listView,
    Controller: LabChequeListController,
    Renderer: LabChequeListRenderer,
    buttonTemplate: "lab_finance_ops.ChequeListView.Buttons",
};

registry.category("views").add("lab_cheque_list", labChequeListView);
