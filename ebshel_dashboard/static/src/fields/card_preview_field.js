/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { KeepLast } from "@web/core/utils/concurrency";
import { useService } from "@web/core/utils/hooks";
import { useDebounced } from "@web/core/utils/timing";
import { useRecordObserver } from "@web/model/relational_model/utils";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

import { BoardCard } from "../dashboard/board_card";

/* Every field the preview depends on. Read inside the observer so a change
   to any of them re-fires it; sent to the server so the preview is the real
   card and not a guess at it. */
const WATCHED = [
    "name", "kind", "model_id", "domain", "aggregate", "measure_field_id", "measure2_field_id",
    "symbol", "prefix", "digits", "number_system", "multiplier", "show_count", "as_ratio",
    "group_by_field_id", "group_by_interval", "stack_field_id", "limit", "sort", "list_field_ids",
    "date_field_id", "period_mode", "own_period", "compare", "compare_mode", "transform",
    "color", "icon", "tooltip", "width", "height", "background", "target_value", "show_values",
    "legend", "multicolor", "log_scale", "dual_axis", "stack_percent", "stack_horizontal",
    "stepped", "semicircle", "palette", "bar_shape", "show_grid", "axis_titles", "free_axis",
    "center_total", "emphasis", "show_bars", "rank_medals", "cf_field_id", "cf_operator",
    "cf_value", "cf_color", "formula", "hide_operator", "hide_value", "note", "only_mine",
    "user_field_id", "alert_operator", "alert_value", "alert_level", "alert_message",
    "spark_source", "drill_view",
];

/** A many2one value as an id, whatever shape the record model hands it in. */
function m2oId(value) {
    if (!value) {
        return false;
    }
    if (Array.isArray(value)) {
        return value[0] || false;
    }
    if (typeof value === "object") {
        return value.id || false;
    }
    return value;
}

/**
 * Live preview of the card being edited.
 *
 * Renders the very same component the board renders, fed by the very same
 * server engine - so what the form shows is what the board will show,
 * computed against real records while you type.
 */
export class CardPreviewField extends Component {
    static template = "ebshel_dashboard.CardPreviewField";
    static components = { BoardCard };
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.keepLast = new KeepLast();
        this.state = useState({ item: null, loading: true, period: "all", ready: false });
        onWillStart(async () => {
            await loadBundle("web.chartjs_lib");
            this.state.ready = true;
        });
        const refresh = useDebounced(() => this.compute(), 400);
        useRecordObserver((record) => {
            const data = record.data;
            for (const name of WATCHED) {
                void data[name];
            }
            refresh();
        });
    }

    get periods() {
        return [
            { key: "all", label: _t("All Time") },
            { key: "today", label: _t("Today") },
            { key: "this_week", label: _t("This Week") },
            { key: "this_month", label: _t("This Month") },
            { key: "last_month", label: _t("Last Month") },
            { key: "this_quarter", label: _t("This Quarter") },
            { key: "this_year", label: _t("This Year") },
        ];
    }

    /** The record's current values, in the shape the server accepts. */
    get values() {
        const data = this.props.record.data;
        const values = {};
        for (const name of WATCHED) {
            let value = data[name];
            if (name.endsWith("_id")) {
                value = m2oId(value);
            } else if (name.endsWith("_ids")) {
                // A static list: sent as the one command new() understands.
                const ids = value ? value.currentIds || (value.records || []).map((record) => record.resId) : [];
                value = [[6, 0, ids.filter((id) => typeof id === "number")]];
            } else if (value && typeof value === "object" && !Array.isArray(value)) {
                value = String(value); // an Html markup object
            }
            values[name] = value === undefined || value === null ? false : value;
        }
        return values;
    }

    get widthStyle() {
        const width = Math.max(1, Math.min(this.props.record.data.width || 3, 12));
        return `width: ${Math.max(25, (width / 12) * 100)}%;`;
    }

    async compute() {
        this.state.loading = true;
        try {
            const result = await this.keepLast.add(
                this.orm.silent.call("dashboard.item", "preview_values", [this.values, this.state.period])
            );
            this.state.item = result && !result.empty ? result : null;
        } catch {
            // A half-typed card is expected to fail; the preview just waits.
            this.state.item = null;
        }
        this.state.loading = false;
    }

    onPeriod(ev) {
        this.state.period = ev.target.value;
        this.compute();
    }

    noop() {}
}

export const cardPreviewField = {
    component: CardPreviewField,
    displayName: _t("Dashboard Card Preview"),
    supportedTypes: ["char"],
};

registry.category("fields").add("dashboard_card_preview", cardPreviewField);
