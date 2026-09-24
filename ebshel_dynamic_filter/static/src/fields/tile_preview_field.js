/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { KeepLast } from "@web/core/utils/concurrency";
import { useService } from "@web/core/utils/hooks";
import { useDebounced } from "@web/core/utils/timing";
import { useRecordObserver } from "@web/model/relational_model/utils";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { FilterTile } from "../components/filter_tile/filter_tile";

/**
 * Live preview of the tile being edited.
 *
 * Renders the very same component the view bar renders, fed by the very same
 * server method - so what the editor shows is what the bar will show, counted
 * against real records while you type the domain.
 */
export class FilterTilePreviewField extends Component {
    static template = "ebshel_dynamic_filter.FilterTilePreviewField";
    static components = { FilterTile };
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.keepLast = new KeepLast();
        // data: undefined, never null - FilterTile's `data` is an optional Object
        // prop, and OWL's validation (debug mode) rejects null while accepting an
        // omitted prop. Same trap the bar's dataFor() hit. (client, 2026-08-28)
        this.state = useState({ data: undefined, total: 0, loading: true });

        const refresh = useDebounced(() => this.compute(), 350);
        // useRecordObserver re-fires on the record values *read inside* the
        // callback - so read every field the preview depends on.
        useRecordObserver((record) => {
            const data = record.data;
            void [
                data.name,
                data.model_name,
                data.domain,
                data.measure_name,
                data.aggregate,
                data.symbol,
                data.color,
                data.custom_color,
                data.icon,
                data.tooltip,
                data.width,
                data.show_trend,
                data.trend_source,
                data.trend_field_name,
                data.target_value,
                data.alert_operator,
                data.alert_value,
                data.alert_message,
            ];
            refresh();
        });
    }

    get record() {
        return this.props.record.data;
    }

    get modelName() {
        return this.record.model_name || "";
    }

    /** Build a tile definition out of the (possibly unsaved) record. */
    get tileDef() {
        const record = this.record;
        return {
            id: this.props.record.resId || 0,
            key: "preview",
            name: record.name || _t("Untitled tile"),
            model: this.modelName,
            domain: record.domain || "[]",
            mine_field: record.only_mine ? record.user_field_name || "" : "",
            measure: record.measure_name || "",
            aggregate: record.aggregate || "sum",
            symbol: record.symbol || "",
            color: record.color || "indigo",
            custom_color: record.custom_color || "",
            icon: record.icon || "fa-bolt",
            tooltip: record.tooltip || "",
            width: record.width || 0,
            show_trend: Boolean(record.show_trend),
            trend_source: record.trend_source || "field",
            trend_field: record.trend_field_name || "",
            target: record.target_value || 0,
            // Carried so the preview lights up exactly like the real tile will
            // when the threshold you are typing is already crossed.
            alert_operator: record.alert_operator || "none",
            alert_value: record.alert_value || 0,
            alert_message: record.alert_message || "",
        };
    }

    async compute() {
        if (!this.modelName) {
            this.state.data = undefined;
            this.state.loading = false;
            return;
        }
        this.state.loading = true;
        try {
            const result = await this.keepLast.add(
                this.orm.silent.call("filter.tile", "compute_tiles", [
                    this.modelName,
                    [{ ...this.tileDef, key: "preview" }],
                    [],
                ])
            );
            this.state.data = (result.tiles && result.tiles.preview) || undefined;
            this.state.total = result.total || 0;
        } catch {
            // A half-typed domain is expected to fail; the tile just shows a dash.
            this.state.data = { value: 0, count: 0, trend: [], delta: null, error: true };
        }
        this.state.loading = false;
    }
}

export const filterTilePreviewField = {
    component: FilterTilePreviewField,
    displayName: _t("Filter Tile Preview"),
    supportedTypes: ["char"],
};

registry.category("fields").add("filter_tile_preview", filterTilePreviewField);
