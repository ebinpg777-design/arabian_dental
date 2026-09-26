/** @odoo-module **/

/**
 * The Cost Centre Cockpit.
 *
 * One call fills the whole screen (see models/cost_centre_board.py), because a
 * board that asks the server once per department is a board that takes four
 * seconds to open and is therefore never opened.
 */
import {Component, onWillStart, useState} from "@odoo/owl";
import {registry} from "@web/core/registry";
import {useService} from "@web/core/utils/hooks";
import {formatMonetary} from "@web/views/fields/formatters";

const PERIODS = [
    {key: "month", label: "This month"},
    {key: "last_month", label: "Last month"},
    {key: "quarter", label: "This quarter"},
    {key: "year", label: "This year"},
];

const KIND_LABEL = {
    production: "Production",
    quality: "Quality",
    commercial: "Commercial",
    supply: "Supply chain",
    admin: "Administration",
};

export class CostCentreBoard extends Component {
    static template = "lab_cost_centre.CostCentreBoard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            loading: true,
            period: "month",
            kind: "",          // "" = every kind
            data: {rows: [], totals: {}},
        });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call(
                "lab.cost.centre.board", "get_board", [this.state.period]);
        } catch (error) {
            console.warn("Cost centre cockpit: could not load", error);
        }
        this.state.loading = false;
    }

    async setPeriod(period) {
        this.state.period = period;
        await this.load();
    }

    setKind(kind) {
        this.state.kind = this.state.kind === kind ? "" : kind;
    }

    get periods() {
        return PERIODS;
    }

    /** The cards to draw: every department, or one kind of them. */
    get rows() {
        const rows = this.state.data.rows || [];
        return this.state.kind
            ? rows.filter((r) => r.kind === this.state.kind)
            : rows;
    }

    /** The kinds present, so the filter bar only offers what exists. */
    get kinds() {
        const seen = new Set((this.state.data.rows || []).map((r) => r.kind));
        return [...seen].filter(Boolean).map((k) => ({
            key: k, label: KIND_LABEL[k] || k,
        }));
    }

    money(amount) {
        return formatMonetary(amount || 0, {digits: [69, 0]});
    }

    /**
     * A department that costs money and bills none is not losing money - the
     * benches earn through the cases they make, which are billed by Sales. Only
     * the departments that do both get a margin.
     */
    margin(row) {
        if (!row.earned) {
            return null;
        }
        return Math.round(100 * ((row.earned - row.spend) / row.earned));
    }

    async openItems(row) {
        if (!row.centre) {
            return;
        }
        const action = await this.orm.call(
            "lab.cost.centre.board", "open_items", [row.id, this.state.period]);
        this.action.doAction(action);
    }

    openDepartment(row) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "hr.department",
            res_id: row.id,
            views: [[false, "form"]],
        });
    }
}

registry.category("actions").add("lab_cost_centre_board", CostCentreBoard);
