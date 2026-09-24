/** @odoo-module **/

import { Component } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

import { CARD_COLORS, DEFAULT_CARD_COLOR } from "../core/board_colors";

/* One face per card kind. The keys are the `kind` selection of dashboard.item. */
const KIND_ICONS = {
    kpi: "fa-hashtag",
    gauge: "fa-tachometer",
    status: "fa-circle",
    bar: "fa-bar-chart",
    hbar: "fa-align-left",
    stacked: "fa-th-large",
    combo: "fa-signal",
    waterfall: "fa-bar-chart",
    pareto: "fa-sort-amount-desc",
    bubble: "fa-circle-o",
    treemap: "fa-th-large",
    lollipop: "fa-map-pin",
    calendar: "fa-calendar",
    heatmap: "fa-th",
    pivot: "fa-table",
    line: "fa-line-chart",
    area: "fa-area-chart",
    pie: "fa-pie-chart",
    donut: "fa-circle-o-notch",
    polar: "fa-bullseye",
    radar: "fa-dot-circle-o",
    funnel: "fa-filter",
    progress: "fa-tasks",
    table: "fa-table",
    list: "fa-list-ol",
    text: "fa-sticky-note-o",
};

const KIND_HINTS = {
    kpi: _t("One number, with an optional target ring, comparison and sparkline."),
    gauge: _t("A half dial filling towards a target."),
    status: _t("A light that turns amber or red when a threshold is crossed."),
    bar: _t("Vertical bars, one per value of the split field."),
    hbar: _t("Horizontal bars - better for long labels."),
    stacked: _t("Bars split by one field and divided by another."),
    combo: _t("Bars for one measure, a line over them for a second - each on its own axis if you like."),
    waterfall: _t("Each bar starts where the last one ended: what added up to the total, and what took away."),
    pareto: _t("The bars in order with the running share over them - which few make up most of it."),
    bubble: _t("Two measures place each group, the number of records sizes it."),
    treemap: _t("The shares as nested rectangles - the biggest fills the most room."),
    lollipop: _t("Bars as thin stems with a dot on top: the same reading, lighter on the eye."),
    calendar: _t("One cell per day, laid out by week, darker where more happened."),
    heatmap: _t("Two fields crossed: the darker the cell, the bigger the number."),
    pivot: _t("Two fields crossed as a table with row and column totals."),
    line: _t("The measure along a date field."),
    area: _t("A filled line along a date field."),
    pie: _t("Shares of the whole, as slices."),
    donut: _t("Shares of the whole, with a hole for the eye to rest."),
    polar: _t("Slices whose radius is the value."),
    radar: _t("Values around a wheel - compare a few categories at once."),
    funnel: _t("Stages narrowing from the widest to the smallest."),
    progress: _t("One progress bar per value, with its share."),
    table: _t("A small table: value, count and share per group."),
    list: _t("The top records themselves, ranked."),
    text: _t("A note: a title, an explanation, a reminder on the board."),
};

/*
 * A drawing of each kind, in the kind's own shape and in the kind's own
 * colours.
 *
 * An icon says "chart"; a sketch says *which* chart, which is the question
 * somebody picking one is actually asking - and a pie with three colours
 * reads as a pie long before the caption does. Shapes are declared rather
 * than written as SVG strings so nothing has to be marked up as trusted
 * HTML: `r` is a rect, `c` a circle, and anything else a path. The canvas is
 * 40 x 24, `f` is how solid a shape is drawn, and `k` names its colour in
 * the board palette (see core/board_colors.js) - left out, a shape takes the
 * card colour, indigo.
 */
const SKETCHES = {
    kpi: [{ t: "r", x: 4, y: 5, w: 20, h: 9, f: 1, k: "indigo" },
             { t: "r", x: 4, y: 17, w: 12, h: 3, f: 0.6, k: "emerald" }],
    gauge: [{ t: "o", d: "M6 18 A12 12 0 0 1 34 18", w: 3.4, f: 0.25, k: "slate" },
            { t: "o", d: "M6 18 A12 12 0 0 1 20 6", w: 3.4, f: 1, k: "emerald" }],
    status: [{ t: "c", x: 20, y: 12, r: 7, f: 1, k: "emerald" }, { t: "c", x: 20, y: 12, r: 10.5, f: 0.18, k: "emerald" }],
    bullet: [{ t: "r", x: 3, y: 9, w: 34, h: 7, f: 0.18, k: "slate" },
             { t: "r", x: 3, y: 9, w: 21, h: 7, f: 1, k: "teal" },
             { t: "r", x: 29, y: 6, w: 2, h: 13, f: 0.8, k: "rose" }],
    formula: [{ t: "r", x: 4, y: 6, w: 11, h: 4, f: 1, k: "sky" },
              { t: "r", x: 4, y: 14, w: 11, h: 4, f: 0.45, k: "amber" },
              { t: "o", d: "M20 20 L28 4", w: 2.4, f: 0.7, k: "slate" },
              { t: "r", x: 31, y: 10, w: 6, h: 4, f: 1, k: "indigo" }],
    bar: [{ t: "r", x: 4, y: 12, w: 6, h: 9, f: 1, k: "indigo" },
             { t: "r", x: 13, y: 6, w: 6, h: 15, f: 1, k: "sky" },
             { t: "r", x: 22, y: 15, w: 6, h: 6, f: 1, k: "teal" },
             { t: "r", x: 31, y: 9, w: 6, h: 12, f: 1, k: "amber" }],
    hbar: [{ t: "r", x: 4, y: 4, w: 30, h: 4, f: 1, k: "indigo" },
              { t: "r", x: 4, y: 10, w: 20, h: 4, f: 1, k: "sky" },
              { t: "r", x: 4, y: 16, w: 12, h: 4, f: 1, k: "teal" }],
    funnel: [{ t: "o", d: "M4 4 L36 4 L27 13 L13 13 Z", w: 0, f: 0.95, k: "violet" },
                { t: "o", d: "M13 13 L27 13 L27 21 L13 21 Z", w: 0, f: 0.8, k: "sky" }],
    stacked: [{ t: "r", x: 6, y: 12, w: 8, h: 9, f: 1, k: "indigo" },
                 { t: "r", x: 6, y: 6, w: 8, h: 6, f: 0.9, k: "amber" },
                 { t: "r", x: 20, y: 15, w: 8, h: 6, f: 1, k: "sky" },
                 { t: "r", x: 20, y: 8, w: 8, h: 7, f: 0.9, k: "rose" }],
    combo: [{ t: "r", x: 5, y: 13, w: 6, h: 8, f: 0.8, k: "indigo" }, { t: "r", x: 14, y: 9, w: 6, h: 12, f: 0.8, k: "indigo" },
            { t: "r", x: 23, y: 15, w: 6, h: 6, f: 0.8, k: "indigo" },
            { t: "o", d: "M8 8 L17 4 L26 10 L35 6", w: 2.2, f: 1, k: "rose" }],
    waterfall: [{ t: "r", x: 4, y: 13, w: 6, h: 8, f: 1, k: "emerald" }, { t: "r", x: 12, y: 8, w: 6, h: 6, f: 0.75, k: "emerald" },
                { t: "r", x: 20, y: 11, w: 6, h: 5, f: 0.4, k: "rose" }, { t: "r", x: 28, y: 5, w: 8, h: 16, f: 0.9, k: "indigo" }],
    pareto: [{ t: "r", x: 4, y: 9, w: 6, h: 12, f: 1, k: "indigo" }, { t: "r", x: 12, y: 13, w: 6, h: 8, f: 0.8, k: "sky" },
             { t: "r", x: 20, y: 16, w: 6, h: 5, f: 0.55, k: "teal" }, { t: "r", x: 28, y: 18, w: 6, h: 3, f: 0.35, k: "slate" },
             { t: "o", d: "M7 14 L15 8 L23 5 L31 4", w: 2.2, f: 1, k: "rose" }],
    bubble: [{ t: "c", x: 11, y: 15, r: 5, f: 0.55, k: "sky" }, { t: "c", x: 22, y: 9, r: 3, f: 0.85, k: "rose" },
             { t: "c", x: 31, y: 16, r: 4, f: 0.4, k: "amber" }],
    treemap: [{ t: "r", x: 4, y: 4, w: 18, h: 17, f: 0.95, k: "indigo" },
              { t: "r", x: 23, y: 4, w: 13, h: 9, f: 0.9, k: "sky" },
              { t: "r", x: 23, y: 14, w: 7, h: 7, f: 0.85, k: "teal" },
              { t: "r", x: 31, y: 14, w: 5, h: 7, f: 0.8, k: "amber" }],
    lollipop: [{ t: "r", x: 7, y: 9, w: 1.6, h: 12, f: 0.7, k: "indigo" }, { t: "c", x: 7.8, y: 8, r: 2.6, f: 1, k: "indigo" },
               { t: "r", x: 16, y: 5, w: 1.6, h: 16, f: 0.7, k: "sky" }, { t: "c", x: 16.8, y: 4, r: 2.6, f: 1, k: "sky" },
               { t: "r", x: 25, y: 13, w: 1.6, h: 8, f: 0.7, k: "teal" }, { t: "c", x: 25.8, y: 12, r: 2.6, f: 1, k: "teal" },
               { t: "r", x: 34, y: 10, w: 1.6, h: 11, f: 0.7, k: "amber" }, { t: "c", x: 34.8, y: 9, r: 2.6, f: 1, k: "amber" }],
    calendar: [{ t: "r", x: 4, y: 4, w: 4, h: 4, f: 0.25, k: "emerald" }, { t: "r", x: 9, y: 4, w: 4, h: 4, f: 0.6, k: "emerald" },
               { t: "r", x: 14, y: 4, w: 4, h: 4, f: 1, k: "emerald" }, { t: "r", x: 19, y: 4, w: 4, h: 4, f: 0.4, k: "emerald" },
               { t: "r", x: 24, y: 4, w: 4, h: 4, f: 0.8, k: "emerald" }, { t: "r", x: 29, y: 4, w: 4, h: 4, f: 0.2, k: "emerald" },
               { t: "r", x: 4, y: 10, w: 4, h: 4, f: 0.7, k: "emerald" }, { t: "r", x: 9, y: 10, w: 4, h: 4, f: 0.2, k: "emerald" },
               { t: "r", x: 14, y: 10, w: 4, h: 4, f: 0.5, k: "emerald" }, { t: "r", x: 19, y: 10, w: 4, h: 4, f: 1, k: "emerald" },
               { t: "r", x: 24, y: 10, w: 4, h: 4, f: 0.3, k: "emerald" }, { t: "r", x: 29, y: 10, w: 4, h: 4, f: 0.9, k: "emerald" },
               { t: "r", x: 4, y: 16, w: 4, h: 4, f: 0.4, k: "emerald" }, { t: "r", x: 9, y: 16, w: 4, h: 4, f: 0.95, k: "emerald" },
               { t: "r", x: 14, y: 16, w: 4, h: 4, f: 0.3, k: "emerald" }, { t: "r", x: 19, y: 16, w: 4, h: 4, f: 0.6, k: "emerald" },
               { t: "r", x: 24, y: 16, w: 4, h: 4, f: 0.15, k: "emerald" }, { t: "r", x: 29, y: 16, w: 4, h: 4, f: 0.7, k: "emerald" }],
    heatmap: [{ t: "r", x: 5, y: 5, w: 8, h: 6, f: 0.9, k: "rose" },
                 { t: "r", x: 16, y: 5, w: 8, h: 6, f: 0.5, k: "amber" },
                 { t: "r", x: 27, y: 5, w: 8, h: 6, f: 0.75, k: "orange" },
                 { t: "r", x: 5, y: 13, w: 8, h: 6, f: 0.5, k: "teal" },
                 { t: "r", x: 16, y: 13, w: 8, h: 6, f: 0.9, k: "emerald" },
                 { t: "r", x: 27, y: 13, w: 8, h: 6, f: 0.45, k: "sky" }],
    pivot: [{ t: "r", x: 4, y: 4, w: 32, h: 4, f: 0.9, k: "indigo" },
               { t: "r", x: 4, y: 10, w: 14, h: 3, f: 0.7, k: "sky" },
               { t: "r", x: 21, y: 10, w: 15, h: 3, f: 0.7, k: "teal" },
               { t: "r", x: 4, y: 15, w: 14, h: 3, f: 0.45, k: "sky" },
               { t: "r", x: 21, y: 15, w: 15, h: 3, f: 0.45, k: "teal" }],
    line: [{ t: "o", d: "M4 18 L13 10 L21 14 L29 5 L36 9", w: 2.6, f: 1, k: "sky" },
              { t: "c", x: 29, y: 5, r: 2.4, f: 1, k: "rose" }],
    area: [{ t: "o", d: "M4 18 L13 10 L21 14 L29 5 L36 9 L36 21 L4 21 Z", w: 0, f: 0.5, k: "teal" },
              { t: "o", d: "M4 18 L13 10 L21 14 L29 5 L36 9", w: 2.4, f: 1, k: "emerald" }],
    pie: [{ t: "c", x: 20, y: 12, r: 9, f: 0.85, k: "sky" },
             { t: "o", d: "M20 12 L20 3 A9 9 0 0 1 28 16 Z", w: 0, f: 1, k: "rose" }],
    donut: [{ t: "c", x: 20, y: 12, r: 9, f: 0.85, k: "teal" },
               { t: "o", d: "M20 12 L20 3 A9 9 0 0 1 29 12 Z", w: 0, f: 1, k: "amber" },
               { t: "c", x: 20, y: 12, r: 4, f: 1, k: "hole" }],
    polar: [{ t: "o", d: "M20 12 L20 4 A8 8 0 0 1 26 8 Z", w: 0, f: 1, k: "violet" },
            { t: "o", d: "M20 12 L26 8 A8 8 0 0 1 25 18 Z", w: 0, f: 0.6, k: "sky" },
            { t: "o", d: "M20 12 L25 18 A8 8 0 0 1 13 16 Z", w: 0, f: 0.35, k: "teal" }],
    radar: [{ t: "o", d: "M20 3 L33 11 L28 21 L12 21 L7 11 Z", w: 1.6, f: 0.2, k: "slate" },
            { t: "o", d: "M20 7 L28 12 L25 18 L15 18 L13 12 Z", w: 0, f: 0.8, k: "violet" }],
    scatter: [{ t: "c", x: 8, y: 17, r: 2.4, f: 1, k: "sky" }, { t: "c", x: 15, y: 9, r: 2.4, f: 0.7, k: "teal" },
              { t: "c", x: 23, y: 14, r: 2.4, f: 1, k: "indigo" }, { t: "c", x: 31, y: 6, r: 2.4, f: 0.55, k: "amber" },
              { t: "c", x: 33, y: 17, r: 2.4, f: 0.35, k: "rose" }],
    progress: [{ t: "r", x: 4, y: 5, w: 32, h: 4, f: 0.15, k: "slate" },
                  { t: "r", x: 4, y: 5, w: 24, h: 4, f: 1, k: "emerald" },
                  { t: "r", x: 4, y: 12, w: 32, h: 4, f: 0.15, k: "slate" },
                  { t: "r", x: 4, y: 12, w: 13, h: 4, f: 1, k: "amber" },
                  { t: "r", x: 4, y: 19, w: 32, h: 3, f: 0.15, k: "slate" },
                  { t: "r", x: 4, y: 19, w: 8, h: 3, f: 1, k: "rose" }],
    table: [{ t: "r", x: 4, y: 4, w: 32, h: 3.5, f: 0.9, k: "indigo" },
               { t: "r", x: 4, y: 10, w: 32, h: 2.5, f: 0.6, k: "sky" },
               { t: "r", x: 4, y: 15, w: 32, h: 2.5, f: 0.45, k: "teal" },
               { t: "r", x: 4, y: 20, w: 32, h: 2.5, f: 0.35, k: "emerald" }],
    list: [{ t: "c", x: 6, y: 6, r: 2, f: 1, k: "amber" },
              { t: "r", x: 11, y: 4.5, w: 25, h: 3, f: 0.7, k: "indigo" },
              { t: "c", x: 6, y: 13, r: 2, f: 1, k: "slate" },
              { t: "r", x: 11, y: 11.5, w: 21, h: 3, f: 0.6, k: "sky" },
              { t: "c", x: 6, y: 20, r: 2, f: 1, k: "orange" },
              { t: "r", x: 11, y: 18.5, w: 17, h: 3, f: 0.5, k: "teal" }],
    text: [{ t: "r", x: 4, y: 5, w: 24, h: 3, f: 0.9, k: "violet" },
              { t: "r", x: 4, y: 11, w: 32, h: 2.5, f: 0.45, k: "violet" },
              { t: "r", x: 4, y: 16, w: 28, h: 2.5, f: 0.35, k: "violet" },
              { t: "r", x: 4, y: 21, w: 18, h: 2.5, f: 0.25, k: "violet" }],
};

/* The families the picker groups the kinds into, and what each family needs. */
const KIND_GROUPS = [
    { key: "numbers", label: _t("Numbers"), needs: _t("a model"), kinds: ["kpi", "gauge", "status"] },
    { key: "bars", label: _t("Bars"), needs: _t("a field to split by"), kinds: ["bar", "hbar", "lollipop", "funnel"] },
    { key: "crossed", label: _t("Two fields"), needs: _t("a field to split by and one to cross with"), kinds: ["stacked", "heatmap", "pivot"] },
    { key: "mixed", label: _t("Two measures"), needs: _t("a field to split by and a second measure"), kinds: ["combo", "bubble", "scatter"] },
    { key: "flow", label: _t("What adds up"), needs: _t("a field to split by"), kinds: ["waterfall", "pareto"] },
    { key: "time", label: _t("Over time"), needs: _t("a date field"), kinds: ["line", "area", "calendar"] },
    { key: "shares", label: _t("Shares"), needs: _t("a field to split by"), kinds: ["pie", "donut", "polar", "radar", "treemap"] },
    { key: "lists", label: _t("Lists"), needs: _t("a field to split by, or none for a top list"), kinds: ["progress", "table", "list"] },
    { key: "other", label: _t("Other"), needs: "", kinds: ["text"] },
];

/** The card kinds as families of faces - what a chart looks like beats its name. */
export class KindPickerField extends Component {
    static template = "ebshel_dashboard.KindPickerField";
    static props = { ...standardFieldProps };

    get labels() {
        const field = this.props.record.fields[this.props.name];
        return Object.fromEntries((field.selection || []).map(([key, label]) => [key, label]));
    }

    get groups() {
        const labels = this.labels;
        return KIND_GROUPS.map((group) => ({
            ...group,
            options: group.kinds
                .filter((key) => key in labels)
                .map((key) => ({
                    key,
                    label: labels[key],
                    icon: KIND_ICONS[key] || "fa-square-o",
                    hint: KIND_HINTS[key] || "",
                })),
        })).filter((group) => group.options.length);
    }

    get current() {
        return this.props.record.data[this.props.name];
    }

    /** The shapes that draw one kind, or nothing if it has no sketch yet. */
    sketch(key) {
        return SKETCHES[key] || [];
    }

    /** A shape's colour, from the same palette the boards draw with. */
    colour(shape) {
        // "hole" is the doughnut's middle: the page showing through, whatever
        // colour the page happens to be.
        if (shape.k === "hole") {
            return "var(--body-bg, #ffffff)";
        }
        return CARD_COLORS[shape.k] || CARD_COLORS[DEFAULT_CARD_COLOR];
    }

    /** What the current kind still lacks, said before the user saves. */
    get missing() {
        const data = this.props.record.data;
        const kind = this.current;
        const has = (name) => Boolean(data[name]);
        if (kind === "text") {
            return "";
        }
        if (!has("model_id")) {
            return _t("Pick a model in step 2.");
        }
        if (["bar", "hbar", "lollipop", "stacked", "combo", "bubble", "waterfall", "pareto", "treemap", "heatmap", "pivot", "pie", "donut", "polar", "radar", "funnel", "progress", "table"].includes(kind) && !has("group_by_field_id")) {
            return _t("This card needs a field to split by (step 3).");
        }
        if (["stacked", "heatmap", "pivot"].includes(kind) && !has("stack_field_id")) {
            return _t("This card also needs a second field to cross with (step 3).");
        }
        if (["combo", "bubble"].includes(kind) && !has("measure2_field_id")) {
            return _t("This card reads two measures: pick the second one (step 3).");
        }
        if (["line", "area", "calendar"].includes(kind) && !has("date_field_id")) {
            return _t("This card needs a date field to run along (step 3).");
        }
        if (kind === "gauge" && !data.target_value) {
            return _t("A gauge needs a target to fill towards (step 3, Numbers).");
        }
        return "";
    }

    select(key) {
        if (this.props.readonly) {
            return;
        }
        this.props.record.update({ [this.props.name]: key });
    }
}

export const kindPickerField = {
    component: KindPickerField,
    displayName: _t("Card Kind Picker"),
    supportedTypes: ["selection"],
};

registry.category("fields").add("dashboard_kind_picker", kindPickerField);
