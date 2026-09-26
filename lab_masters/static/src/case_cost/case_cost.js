/** @odoo-module **/

/**
 * The Case Cost Explorer.
 *
 * Fifteen routes down the left, one of them open on the right: what it consumes,
 * where it is worked, what it costs and what is left of the price. One server
 * call fills all of it (see models/case_cost.py).
 *
 * The bar across each route is the price, divided: material, labour, the
 * overhead the lab's own sheet absorbs, and what remains. It is the one picture
 * that answers "are we making money on this" without arithmetic.
 */
import {Component, onWillStart, useState} from "@odoo/owl";
import {registry} from "@web/core/registry";
import {useService} from "@web/core/utils/hooks";
import {formatMonetary} from "@web/views/fields/formatters";

const SORTS = [
    {key: "volume", label: "By volume"},
    {key: "margin", label: "By margin"},
    {key: "cost", label: "By cost"},
    {key: "name", label: "By name"},
];

export class CaseCostExplorer extends Component {
    static template = "lab_masters.CaseCostExplorer";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            loading: true,
            sort: "volume",
            selected: null,
            data: {rows: [], totals: {}, currency: {}},
        });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call("lab.case.cost", "get_routes", []);
            const first = this.rows[0];
            this.state.selected = first ? first.bom_id : null;
        } catch (error) {
            console.warn("Case cost explorer: could not load", error);
        }
        this.state.loading = false;
    }

    /** The routes in the order the chosen sort puts them. */
    get rows() {
        const rows = [...(this.state.data.rows || [])];
        const by = {
            volume: (a, b) => b.sold_qty - a.sold_qty,
            margin: (a, b) => a.margin_pct - b.margin_pct,
            cost: (a, b) => b.works - a.works,
            name: (a, b) => a.name.localeCompare(b.name),
        }[this.state.sort];
        return rows.sort(by);
    }

    get current() {
        return (this.state.data.rows || []).find(
            (row) => row.bom_id === this.state.selected) || null;
    }

    money(value) {
        if (value === null || value === undefined) {
            return "—";
        }
        return formatMonetary(value, {currencyId: this.state.data.currency.id});
    }

    /**
     * The four bands of the price bar, as percentages of the price.
     *
     * When a route costs more than it sells for the bands would run past 100%,
     * so they are scaled to the cost instead and the loss is shown in its own
     * colour. A bar that silently overflows its container is a bar that lies.
     */
    bands(row) {
        const overhead = Math.max(row.overhead || 0, 0);
        const cost = row.works + overhead;
        const scale = Math.max(row.price, cost) || 1;
        const pct = (value) => Math.max(0, (value / scale) * 100);
        return {
            material: pct(row.material),
            labour: pct(row.labour),
            overhead: pct(overhead),
            margin: pct(Math.max(0, row.price - cost)),
            loss: pct(Math.max(0, cost - row.price)),
            covered: row.price >= cost,
        };
    }

    /** Hours, because a case that takes 214 minutes is easier read as 3.6 h. */
    hours(minutes) {
        return (minutes / 60).toFixed(1);
    }

    select(bomId) {
        this.state.selected = bomId;
    }

    setSort(key) {
        this.state.sort = key;
    }

    async openBom(bomId) {
        const action = await this.orm.call("lab.case.cost", "open_bom", [bomId]);
        this.action.doAction(action);
    }

    async openProduct(productId) {
        const action = await this.orm.call("lab.case.cost", "open_product", [productId]);
        this.action.doAction(action);
    }
}

registry.category("actions").add("lab_case_cost_explorer", CaseCostExplorer);
