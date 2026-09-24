/** @odoo-module **/

import { Component, useRef, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useRecordObserver } from "@web/model/relational_model/utils";

/* The same families the form's own visibility rules use, so a chip is only
   ever offered for a setting the card can actually use. */
const NUMBER_KINDS = ["kpi", "gauge", "status", "bullet", "formula"];
const SPLIT_KINDS = ["bar", "hbar", "stacked", "combo", "heatmap", "pivot", "pie", "donut", "polar",
                     "radar", "funnel", "progress", "table", "waterfall", "pareto", "treemap",
                     "lollipop", "bubble"];
const STACKED_KINDS = ["stacked", "heatmap", "pivot"];
const SERIES_KINDS = ["line", "area", "calendar"];
const NO_COMPARE = ["list", "scatter", "formula", "text"];

/** A many2one value as {id, name}, whatever shape the record hands it in. */
function m2o(value) {
    if (!value) {
        return null;
    }
    if (Array.isArray(value)) {
        return { id: value[0], name: value[1] || "" };
    }
    return { id: value.id, name: value.display_name || "" };
}

/** How many records an x2many holds, saved or not. */
function count(list) {
    if (!list) {
        return 0;
    }
    if (typeof list.count === "number") {
        return list.count;
    }
    return (list.currentIds || list.records || []).length;
}

/**
 * What has been set, in one line - so the editor can read the card's setup
 * without opening seven tabs, and jump straight to the one a chip names.
 *
 * Every chip is a sentence built from the record ("Split by Country, every
 * month", "Above 10: danger"), and a click activates the tab that holds it.
 */
export class SetupChipsWidget extends Component {
    static template = "ebshel_dashboard.SetupChips";
    static props = {
        record: Object,
        readonly: { type: Boolean, optional: true },
    };

    setup() {
        this.root = useRef("root");
        this.state = useState({ chips: [] });
        // Built inside the observer, so any field a chip reads re-fires it.
        useRecordObserver((record) => {
            this.state.chips = this.build(record);
        });
    }

    /** The label of a selection value, from the field's own definition. */
    label(record, field, value) {
        const description = record.fields[field];
        const found = description && description.selection
            && description.selection.find((entry) => entry[0] === value);
        return found ? found[1] : String(value || "");
    }

    build(record) {
        const data = record.data;
        const kind = data.kind;
        if (kind === "text") {
            return [];
        }
        const label = (field, value) => this.label(record, field, value);
        const lower = (text) => String(text).toLowerCase();
        const chips = [];
        const number = NUMBER_KINDS.includes(kind);

        const subs = count(data.subvalue_ids);
        if (number && subs) {
            chips.push({ page: "subvalues", icon: "fa-list-ol",
                         text: subs === 1 ? _t("One number underneath") : _t("%s numbers underneath", subs) });
        }

        const split = m2o(data.group_by_field_id);
        if (split && SPLIT_KINDS.includes(kind)) {
            let text = _t("Split by %s", split.name);
            const stack = m2o(data.stack_field_id);
            if (stack && STACKED_KINDS.includes(kind)) {
                text = _t("%s by %s", split.name, stack.name);
            } else if (["date", "datetime"].includes(data.group_by_field_type) && data.group_by_interval) {
                text = _t("Split by %s, every %s", split.name, lower(label("group_by_interval", data.group_by_interval)));
            }
            chips.push({ page: "shape", icon: "fa-th-large", text });
        }
        if (kind === "list") {
            const columns = count(data.list_field_ids);
            chips.push({ page: "shape", icon: "fa-columns",
                         text: columns ? _t("%s columns, %s rows", columns, data.limit || 0)
                                       : _t("%s rows", data.limit || 0) });
        }

        const date = m2o(data.date_field_id);
        if (date) {
            const text = data.period_mode === "own"
                ? _t("Always %s, by %s", lower(label("own_period", data.own_period)), date.name)
                : _t("The board's period, by %s", date.name);
            chips.push({ page: "shape", icon: "fa-calendar", text });
            if (data.compare && !NO_COMPARE.includes(kind)) {
                chips.push({ page: "shape", icon: "fa-exchange",
                             text: _t("Compared with %s", lower(label("compare_mode", data.compare_mode))) });
            }
        } else {
            chips.push({ page: "shape", icon: "fa-calendar-o", quiet: true,
                         text: _t("No period field: shows everything, always") });
        }
        if (SERIES_KINDS.includes(kind) && data.transform && data.transform !== "none") {
            chips.push({ page: "shape", icon: "fa-line-chart", text: label("transform", data.transform) });
        }
        const rule = m2o(data.cf_field_id);
        if (rule && ["list", "table"].includes(kind)) {
            const test = lower(label("cf_operator", data.cf_operator));
            chips.push({ page: "shape", icon: "fa-tint",
                         text: _t("Rows coloured when %s is %s", rule.name,
                                  data.cf_value ? `${test} ${data.cf_value}` : test) });
        }

        const written = [];
        if (data.prefix) {
            written.push(data.prefix);
        }
        if (data.symbol) {
            written.push(data.symbol);
        }
        if (data.number_system && data.number_system !== "auto") {
            written.push(label("number_system", data.number_system));
        }
        if (data.multiplier && ![0, 1].includes(data.multiplier)) {
            written.push(_t("times %s", data.multiplier));
        }
        if (written.length) {
            chips.push({ page: "numbers", icon: "fa-hashtag", text: _t("Written as %s", written.join(" · ")) });
        }
        if (data.target_value) {
            chips.push({ page: "numbers", icon: "fa-bullseye", text: _t("Target %s", data.target_value) });
        }

        const look = [];
        if (data.palette && data.palette !== "board") {
            look.push(_t("%s colours", label("palette", data.palette)));
        }
        if (data.multicolor) {
            look.push(_t("one colour per bar"));
        }
        if (data.stack_percent) {
            look.push(_t("as shares"));
        }
        if (data.stack_horizontal) {
            look.push(_t("sideways"));
        }
        if (data.stepped) {
            look.push(_t("steps"));
        }
        if (data.log_scale) {
            look.push(_t("log axis"));
        }
        if (data.dual_axis) {
            look.push(_t("second axis"));
        }
        if (data.semicircle) {
            look.push(_t("half circle"));
        }
        if (data.center_total && kind === "donut") {
            look.push(_t("total in the middle"));
        }
        if (data.emphasis && data.emphasis !== "none") {
            look.push(_t("%s stands out", lower(label("emphasis", data.emphasis))));
        }
        if (data.bar_shape && data.bar_shape !== "rounded") {
            look.push(_t("%s bar ends", lower(label("bar_shape", data.bar_shape))));
        }
        if (data.show_grid === false) {
            look.push(_t("no grid"));
        }
        if (data.axis_titles) {
            look.push(_t("axis titles"));
        }
        if (data.free_axis) {
            look.push(_t("axis follows the data"));
        }
        if (look.length && !number && !["list", "table", "text"].includes(kind)) {
            chips.push({ page: "chart", icon: "fa-paint-brush", text: look.join(" · ") });
        }

        if (number && data.alert_operator && data.alert_operator !== "none") {
            chips.push({ page: "threshold", icon: "fa-bell-o",
                         tone: data.alert_level === "danger" ? "danger" : "warning",
                         text: _t("%s %s: %s", label("alert_operator", data.alert_operator), data.alert_value || 0,
                                  lower(label("alert_level", data.alert_level))) });
        }

        const groups = count(data.group_ids);
        const companies = count(data.company_ids);
        if (groups) {
            chips.push({ page: "visibility", icon: "fa-users",
                         text: groups === 1 ? _t("One group only") : _t("%s groups only", groups) });
        }
        if (companies) {
            chips.push({ page: "visibility", icon: "fa-building-o",
                         text: companies === 1 ? _t("One company") : _t("%s companies", companies) });
        }
        if (number && data.hide_operator && data.hide_operator !== "none") {
            chips.push({ page: "visibility", icon: "fa-eye-slash",
                         text: _t("Hidden while %s %s", lower(label("hide_operator", data.hide_operator)), data.hide_value || 0) });
        }

        const action = m2o(data.action_id);
        if (action || (data.drill_view && data.drill_view !== "list")) {
            const view = lower(label("drill_view", data.drill_view));
            chips.push({ page: "drill", icon: "fa-hand-pointer-o",
                         text: action ? _t("Opens %s, as a %s", action.name, view) : _t("Opens as a %s", view) });
        }
        return chips;
    }

    get chips() {
        return this.state.chips;
    }

    /** Activate the tab that holds the setting a chip names. */
    jump(chip) {
        const section = this.root.el && this.root.el.closest(".o_dbf_section");
        const tab = section && section.querySelector(`.o_dbf_tabs .nav-link[name="${chip.page}"]`);
        if (tab) {
            tab.click();
        }
    }
}

export const setupChipsWidget = {
    component: SetupChipsWidget,
};

registry.category("view_widgets").add("dashboard_setup_chips", setupChipsWidget);
