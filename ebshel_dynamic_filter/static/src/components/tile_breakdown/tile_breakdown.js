/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { humanNumber } from "@web/core/utils/numbers";
import { useService } from "@web/core/utils/hooks";
import { formatFloat, formatInteger } from "@web/views/fields/formatters";

/**
 * "What is actually inside this tile?"
 *
 * A tile answers *how many*; this popover answers *of what*, splitting the
 * tile's own slice on a field of the model - and every row is itself a filter,
 * so the answer is one click away from being the view you are looking at.
 *
 * The split is computed server-side in a single grouped read, with the reader's
 * own rights, against the domain currently on screen.
 */
export class TileBreakdown extends Component {
    static template = "ebshel_dynamic_filter.TileBreakdown";
    static props = {
        close: { type: Function, optional: true },
        def: Object,
        baseDomain: { type: [Array, String], optional: true },
        onPick: { type: Function, optional: true },
    };

    setup() {
        this.orm = useService("orm");
        this.state = useState({ loading: true, rows: [], label: "", others: null });

        onWillStart(async () => {
            try {
                const result = await this.orm.silent.call("filter.tile", "tile_breakdown", [
                    this.props.def.model,
                    {
                        domain: this.props.def.domain,
                        mine_field: this.props.def.mine_field || "",
                        measure: this.props.def.measure,
                        aggregate: this.props.def.aggregate,
                        breakdown_field: this.props.def.breakdown_field,
                    },
                    this.props.baseDomain || [],
                ]);
                Object.assign(this.state, {
                    rows: result.rows || [],
                    label: result.label || "",
                    others: result.others || null,
                });
            } catch {
                this.state.rows = [];
            }
            this.state.loading = false;
        });
    }

    get title() {
        return this.state.label ? _t("By %s", this.state.label) : _t("Breakdown");
    }

    /** Widest row = full bar; the rest are read against it. */
    get scale() {
        return Math.max(...this.state.rows.map((row) => Math.abs(row.value)), 0) || 1;
    }

    barWidth(row) {
        return `${Math.min(100, (Math.abs(row.value) / this.scale) * 100)}%`;
    }

    format(row) {
        const value = this.props.def.measure ? row.value : row.count;
        if (Math.abs(value) >= 10000) {
            return humanNumber(value, { decimals: 1 });
        }
        return this.props.def.measure ? formatFloat(value) : formatInteger(value);
    }

    pick(row) {
        if (this.props.onPick) {
            this.props.onPick(row);
        }
        this.props.close?.();
    }
}
