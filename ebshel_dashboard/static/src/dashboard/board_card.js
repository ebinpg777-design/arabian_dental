/** @odoo-module **/

import { Component, markup, onWillUnmount, useEffect, useExternalListener, useRef, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";

import { CARD_COLORS, chartInk, resolveCardColor, seriesColors, withAlpha } from "../core/board_colors";
import { formatNumber } from "../core/number_format";

/**
 * A canvas as a white-backed PNG data URL, at most 1400px wide.
 *
 * White first: a transparent chart is unreadable on paper and in a dark
 * viewer. Capped: twenty charts at retina size would make a ten-megabyte
 * request out of a print.
 */
export function canvasToPng(canvas) {
    if (!canvas || !canvas.width || !canvas.height) {
        return null;
    }
    const scale = Math.min(1, 1400 / canvas.width);
    const copy = document.createElement("canvas");
    copy.width = Math.round(canvas.width * scale);
    copy.height = Math.round(canvas.height * scale);
    const ctx = copy.getContext("2d");
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, copy.width, copy.height);
    ctx.drawImage(canvas, 0, 0, copy.width, copy.height);
    return copy.toDataURL("image/png");
}

const CHART_KINDS = ["bar", "hbar", "lollipop", "stacked", "combo", "waterfall", "pareto", "bubble",
                     "line", "area", "pie", "donut", "polar", "radar", "funnel", "scatter"];
// Drawn as SVG by the card itself, not by Chart.js.
const SVG_KINDS = ["treemap", "calendar"];
const MATRIX_KINDS = ["heatmap", "pivot"];
const SPLIT_KINDS = ["bar", "hbar", "pie", "donut", "polar", "radar", "funnel", "progress", "table"];
const NUMBER_KINDS = ["kpi", "gauge", "status", "bullet", "formula"];
const ARC_KINDS = ["pie", "donut", "polar"];
const NEGATIVE = "#e34948";
const COUNT_UP_MS = 520;

/* The sum of the slices, written in the hole of a donut. The size follows
   the hole, so a small card still gets a number it can hold. */
const centerTotal = {
    id: "dbcCenterTotal",
    afterDatasetsDraw(chart, _args, opts) {
        if (!opts || !opts.enabled) {
            return;
        }
        const meta = chart.getDatasetMeta(0);
        const arc = meta && meta.data && meta.data[0];
        if (!arc) {
            return;
        }
        const { ctx } = chart;
        const half = chart.config.options.circumference === 180;
        const room = Math.max(24, (arc.innerRadius || 30) * 2 - 12);
        const size = Math.round(Math.min(26, Math.max(12, (room / Math.max(3, opts.text.length)) * 1.6)));
        // A half donut's centre is its flat bottom edge: the number sits just above it.
        const baseY = half ? arc.y - size * 0.9 : arc.y - 4;
        ctx.save();
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillStyle = opts.color;
        ctx.font = `700 ${size}px system-ui, -apple-system, 'Segoe UI', sans-serif`;
        ctx.fillText(opts.text, arc.x, baseY);
        if (opts.caption) {
            ctx.fillStyle = opts.muted;
            ctx.font = "600 10px system-ui, -apple-system, 'Segoe UI', sans-serif";
            ctx.fillText(opts.caption, arc.x, baseY + size * 0.8);
        }
        ctx.restore();
    },
};

/* Writes each value next to its bar or point. Values stay in ink, never in
   the series colour, so they read on any background. */
const valueLabels = {
    id: "dbcValueLabels",
    afterDatasetsDraw(chart, _args, opts) {
        if (!opts || !opts.enabled) {
            return;
        }
        const { ctx } = chart;
        const horizontal = chart.config.options.indexAxis === "y";
        ctx.save();
        ctx.font = "600 11px system-ui, -apple-system, 'Segoe UI', sans-serif";
        ctx.fillStyle = opts.color;
        chart.data.datasets.forEach((dataset, datasetIndex) => {
            const meta = chart.getDatasetMeta(datasetIndex);
            if (meta.hidden) {
                return;
            }
            meta.data.forEach((element, index) => {
                const raw = dataset.data[index];
                const value = Array.isArray(raw) ? Math.abs(raw[1] - raw[0]) : raw;
                if (value === null || value === undefined) {
                    return;
                }
                const label = opts.format(value);
                const pos = element.tooltipPosition ? element.tooltipPosition() : element;
                if (opts.arc) {
                    ctx.textAlign = "center";
                    ctx.textBaseline = "middle";
                    ctx.fillText(label, pos.x, pos.y);
                } else if (horizontal) {
                    ctx.textAlign = "left";
                    ctx.textBaseline = "middle";
                    ctx.fillText(label, pos.x + 6, pos.y);
                } else {
                    ctx.textAlign = "center";
                    ctx.textBaseline = "bottom";
                    ctx.fillText(label, pos.x, pos.y - 4);
                }
            });
        });
        ctx.restore();
    },
};

/* A reference line at the card's target, on a cartesian chart. */
const targetLine = {
    id: "dbcTargetLine",
    afterDraw(chart, _args, opts) {
        if (!opts || !opts.value) {
            return;
        }
        const horizontal = chart.config.options.indexAxis === "y";
        const scale = horizontal ? chart.scales.x : chart.scales.y;
        if (!scale) {
            return;
        }
        const { ctx, chartArea } = chart;
        const at = scale.getPixelForValue(opts.value);
        ctx.save();
        ctx.strokeStyle = opts.color;
        ctx.setLineDash([5, 4]);
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        if (horizontal) {
            ctx.moveTo(at, chartArea.top);
            ctx.lineTo(at, chartArea.bottom);
        } else {
            ctx.moveTo(chartArea.left, at);
            ctx.lineTo(chartArea.right, at);
        }
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.font = "600 10px system-ui, -apple-system, 'Segoe UI', sans-serif";
        ctx.fillStyle = opts.color;
        ctx.textAlign = horizontal ? "left" : "right";
        ctx.textBaseline = "bottom";
        ctx.fillText(opts.label, horizontal ? at + 4 : chartArea.right, horizontal ? chartArea.top + 12 : at - 3);
        ctx.restore();
    },
};

/** Hand the browser a file to save. */
function download(filename, content, type) {
    const blob = content instanceof Blob ? content : new Blob([content], { type });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 2000);
}

function csvCell(value) {
    const text = value === null || value === undefined ? "" : String(value);
    return /[",\n;]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/**
 * One card of a board.
 *
 * The card never queries anything itself: it is handed a payload computed by
 * `dashboard.item.compute_values` and draws it. Clicking any part of it calls
 * back up to the board: a whole card opens its records, a part of a chart
 * focuses the board on that part.
 */
export class BoardCard extends Component {
    static template = "ebshel_dashboard.BoardCard";
    static props = {
        item: Object,
        editable: { type: Boolean, optional: true },
        focus: { type: Object, optional: true },
        compact: { type: Boolean, optional: true },
        pinned: { type: Boolean, optional: true },
        palette: { type: String, optional: true },
        onDrill: Function,
        onFocus: { type: Function, optional: true },
        onEdit: { type: Function, optional: true },
        onRefresh: { type: Function, optional: true },
        onPin: { type: Function, optional: true },
        onPdf: { type: Function, optional: true },
        onXlsx: { type: Function, optional: true },
        onRemove: { type: Function, optional: true },
        canEdit: { type: Boolean, optional: true },
    };

    setup() {
        this.canvasRef = useRef("canvas");
        this.menuRef = useRef("menu");
        this.chart = null;
        this.frame = null;
        this.state = useState({ showTable: false, menuOpen: false, shown: null });
        // Redraw whenever the payload, the focus or the table toggle changes:
        // a period change, a refresh or a new focus hands the card a new
        // object, and Chart.js needs to be told.
        useEffect(
            () => {
                this.renderChart();
                return () => this.destroyChart();
            },
            () => [this.props.item, this.props.focus, this.props.palette, this.state.showTable]
        );
        // The number counts up to its new value instead of jumping to it.
        useEffect(
            () => this.countUp(),
            () => [this.props.item.value]
        );
        useExternalListener(window, "click", (ev) => {
            if (this.state.menuOpen && this.menuRef.el && !this.menuRef.el.contains(ev.target)) {
                this.state.menuOpen = false;
            }
        });
        onWillUnmount(() => {
            this.destroyChart();
            if (this.frame) {
                cancelAnimationFrame(this.frame);
            }
        });
    }

    // ------------------------------------------------------------------
    // Presentation
    // ------------------------------------------------------------------
    get item() {
        return this.props.item;
    }

    get color() {
        return resolveCardColor(this.item);
    }

    get isChart() {
        return CHART_KINDS.includes(this.item.kind);
    }

    get isNumber() {
        return NUMBER_KINDS.includes(this.item.kind);
    }

    get canFocus() {
        return Boolean(this.props.onFocus) && !this.item.error &&
            (SPLIT_KINDS.includes(this.item.kind) ||
             ["line", "area", "calendar", "stacked", "combo", "waterfall", "pareto", "bubble",
              "treemap", "lollipop", "heatmap", "pivot"].includes(this.item.kind));
    }

    get isMatrix() {
        return MATRIX_KINDS.includes(this.item.kind);
    }

    get isSvg() {
        return SVG_KINDS.includes(this.item.kind);
    }

    /**
     * A squarified treemap of the split: every point a rectangle whose area
     * is its share, laid into a 100 x 60 box row by row so that shapes stay
     * close to square. Coordinates are in percent of the box.
     */
    get treemapCells() {
        const points = (this.item.points || []).filter((point) => Number(point.value || 0) > 0);
        const total = points.reduce((sum, point) => sum + Number(point.value || 0), 0);
        if (!total) {
            return [];
        }
        const colours = this.ramp(points.length);
        const cells = [];
        const box = { x: 0, y: 0, w: 100, h: 60 };
        let queue = points.map((point, index) => ({ point, index, area: (Number(point.value) / total) * box.w * box.h }));
        let x = box.x;
        let y = box.y;
        let w = box.w;
        let h = box.h;
        while (queue.length) {
            const vertical = w >= h;   // lay the next row along the shorter side
            const side = vertical ? h : w;
            let row = [];
            let rowArea = 0;
            let bestWorst = Infinity;
            for (const entry of queue) {
                const candidate = [...row, entry];
                const area = rowArea + entry.area;
                const thickness = area / side;
                const worst = Math.max(...candidate.map((c) => {
                    const length = c.area / thickness;
                    return Math.max(thickness / length, length / thickness);
                }));
                if (worst <= bestWorst) {
                    row = candidate;
                    rowArea = area;
                    bestWorst = worst;
                } else {
                    break;
                }
            }
            queue = queue.slice(row.length);
            const thickness = rowArea / side;
            let along = vertical ? y : x;
            for (const entry of row) {
                const length = entry.area / thickness;
                cells.push({
                    point: entry.point,
                    index: entry.index,
                    x: vertical ? x : along,
                    y: vertical ? along : y,
                    w: vertical ? thickness : length,
                    h: vertical ? length : thickness,
                    color: colours[entry.index],
                });
                along += length;
            }
            if (vertical) {
                x += thickness;
                w -= thickness;
            } else {
                y += thickness;
                h -= thickness;
            }
        }
        return cells;
    }

    /**
     * A calendar heat map: the daily points laid out as weeks (columns) and
     * weekdays (rows), Monday at the top, coloured by size.
     */
    get calendarCells() {
        const points = this.item.points || [];
        if (!points.length) {
            return { cells: [], weeks: 0, months: [] };
        }
        const biggest = Math.max(1, ...points.map((point) => Math.abs(Number(point.value || 0))));
        const first = new Date(points[0].key + "T00:00:00");
        const offset = (first.getDay() + 6) % 7;  // Monday = 0
        const cells = [];
        const months = [];
        let lastMonth = null;
        points.forEach((point, index) => {
            const day = new Date(point.key + "T00:00:00");
            const slot = index + offset;
            const week = Math.floor(slot / 7);
            const weekday = slot % 7;
            const value = Number(point.value || 0);
            cells.push({ point, week, weekday, strength: biggest ? Math.abs(value) / biggest : 0 });
            if (day.getMonth() !== lastMonth && weekday <= 6 && day.getDate() <= 7) {
                months.push({ week, label: day.toLocaleString(undefined, { month: "short" }) });
                lastMonth = day.getMonth();
            }
        });
        return { cells, weeks: Math.ceil((points.length + offset) / 7), months };
    }

    /** The colour of a calendar cell: the card colour, as strong as the day. */
    calendarFill(cell) {
        if (!cell.strength) {
            return withAlpha(this.color, 0.08);
        }
        return withAlpha(this.color, 0.18 + cell.strength * 0.82);
    }

    /** The "unusual" badge, worded so the arithmetic can be checked. */
    get anomaly() {
        const found = this.item.anomaly;
        if (!found) {
            return null;
        }
        return {
            above: found.direction === "above",
            z: Math.abs(found.z),
            text: _t("%s standard deviations %s the mean of the last %s days (%s ± %s)",
                     Math.abs(found.z), found.direction === "above" ? _t("above") : _t("below"),
                     found.days, this.formatValue(found.mean), this.formatValue(found.sigma)),
        };
    }

    /**
     * The crossed matrix as rows: one per category, a cell per series, with
     * the row total, the column totals and the biggest cell for the heat.
     */
    get matrix() {
        const item = this.item;
        const categories = item.categories || [];
        const series = item.series || [];
        const rows = categories.map((category, index) => {
            const cells = series.map((entry) => Number(entry.values[index] || 0));
            return { key: category.key, label: category.label, folded: category.folded, cells,
                     total: cells.reduce((a, b) => a + b, 0) };
        });
        const columns = series.map((entry, index) => ({
            label: entry.label,
            total: rows.reduce((a, row) => a + row.cells[index], 0),
        }));
        const max = Math.max(1, ...rows.flatMap((row) => row.cells.map(Math.abs)));
        return { rows, columns, max, total: rows.reduce((a, row) => a + row.total, 0) };
    }

    /** The heat of a cell: the card's colour, as strong as the value. */
    heat(value, max) {
        const share = Math.min(1, Math.abs(value) / max);
        return `background-color: ${withAlpha(this.color, 0.08 + share * 0.82)}; color: ${share > 0.55 ? "#fff" : "inherit"};`;
    }

    /** Goal pacing: how far the number stands against the calendar. */
    get pace() {
        const pace = this.item.pace;
        if (!pace || !this.item.target) {
            return null;
        }
        const value = Number(this.item.value || 0);
        const target = Number(this.item.target || 1);
        const labels = { ahead: _t("ahead"), on_track: _t("on track"), behind: _t("behind") };
        return {
            fill: Math.min(100, (value / target) * 100),
            mark: Math.min(100, pace.elapsed),
            status: pace.status,
            label: labels[pace.status] || pace.status,
            // No "%%" here: Odoo 18's _t leaves it doubled, 19's collapses it.
            text: _t("%s of the period · expected %s · %s left", `${pace.elapsed}%`, this.formatValue(pace.expected),
                     pace.days_left === 1 ? _t("1 day") : _t("%s days", pace.days_left)),
        };
    }

    /**
     * A straight-line forecast over the buckets still to come in the period:
     * least squares on the buckets already gone, drawn dashed from the last
     * real point. Needs three past points and at least one future one.
     */
    get forecast() {
        const item = this.item;
        if (!["line", "area"].includes(item.kind) || !item.period || item.period === "all") {
            return null;
        }
        const points = item.points || [];
        const today = new Date().toISOString().slice(0, 10);
        const past = points.map((point, index) => ({ index, value: Number(point.value || 0) }))
            .filter((entry) => points[entry.index].key <= today);
        if (past.length < 3 || past.length >= points.length) {
            return null;
        }
        const n = past.length;
        const sumX = past.reduce((a, p) => a + p.index, 0);
        const sumY = past.reduce((a, p) => a + p.value, 0);
        const sumXY = past.reduce((a, p) => a + p.index * p.value, 0);
        const sumXX = past.reduce((a, p) => a + p.index * p.index, 0);
        const slope = (n * sumXY - sumX * sumY) / (n * sumXX - sumX * sumX || 1);
        const intercept = (sumY - slope * sumX) / n;
        const last = past[past.length - 1];
        return points.map((point, index) =>
            index < last.index ? null : index === last.index ? last.value : Math.max(0, intercept + slope * index)
        );
    }

    /** The class list the card wears: kind, family, state, background. */
    get cardClass() {
        const item = this.item;
        const classes = ["o_dbc_card", `o_dbc_${item.kind}`, `o_dbc_bg_${item.background || "plain"}`];
        if (this.isNumber) {
            classes.push("o_dbc_number");
        }
        if (item.error) {
            classes.push("o_dbc_error");
        }
        if (this.alert) {
            classes.push(`o_dbc_alert_${this.alert}`);
        }
        if (item.focused) {
            classes.push("o_dbc_focused");
        }
        if (item.focus_source) {
            classes.push("o_dbc_source");
        }
        if (this.props.compact) {
            classes.push("o_dbc_compact");
        }
        if (this.props.pinned) {
            classes.push("o_dbc_pinned");
        }
        return classes.join(" ");
    }

    get hasContent() {
        if (this.item.error || this.item.pending) {
            return false;
        }
        if (this.item.kind === "stacked" || this.isMatrix) {
            return (this.item.categories || []).length > 0 && (this.item.series || []).length > 0;
        }
        if (["combo", "bubble", "waterfall", "pareto"].includes(this.item.kind)) {
            return (this.item.points || []).length > 0;
        }
        if (this.isChart || this.isSvg || ["progress", "table"].includes(this.item.kind)) {
            return (this.item.points || []).length > 0;
        }
        if (this.item.kind === "list") {
            return (this.item.rows || []).length > 0;
        }
        return true;
    }

    get note() {
        return markup(this.item.note || "");
    }

    /** What the info icon says: the plain-words summary, and how long it took. */
    get infoTitle() {
        const parts = [this.item.tooltip || ""];
        if (this.item.ms !== undefined) {
            parts.push(_t("computed in %s ms", this.item.ms));
        }
        return parts.filter(Boolean).join(" · ");
    }

    /** The main number, formatted the way the card was configured. */
    get displayValue() {
        if (this.item.as_ratio && this.item.ratio !== undefined) {
            return `${this.item.ratio}`;
        }
        const value = this.state.shown === null ? this.item.value : this.state.shown;
        return this.formatValue(value);
    }

    get displayUnit() {
        if (this.item.as_ratio && this.item.ratio !== undefined) {
            return "%";
        }
        return this.item.symbol || "";
    }

    formatValue(value) {
        return formatNumber(value, this.item);
    }

    /** A value with its prefix and unit, for tooltips and labels. */
    formatFull(value) {
        const prefix = this.item.prefix ? `${this.item.prefix} ` : "";
        const unit = this.item.symbol ? ` ${this.item.symbol}` : "";
        return `${prefix}${this.formatValue(value)}${unit}`;
    }

    /** Tween the shown number towards the real one. */
    countUp() {
        if (!this.isNumber || this.item.error || typeof this.item.value !== "number") {
            this.state.shown = null;
            return;
        }
        const from = this.state.shown === null ? 0 : this.state.shown;
        const to = this.item.value;
        if (from === to) {
            this.state.shown = to;
            return;
        }
        if (this.frame) {
            cancelAnimationFrame(this.frame);
        }
        const started = performance.now();
        const step = (now) => {
            const t = Math.min(1, (now - started) / COUNT_UP_MS);
            const eased = 1 - Math.pow(1 - t, 3);
            this.state.shown = from + (to - from) * eased;
            if (t < 1) {
                this.frame = requestAnimationFrame(step);
            } else {
                this.state.shown = to;
                this.frame = null;
            }
        };
        this.frame = requestAnimationFrame(step);
    }

    get deltaLabel() {
        const delta = this.item.delta;
        if (delta === undefined || delta === null) {
            return "";
        }
        const sign = delta > 0 ? "+" : "";
        if (this.item.delta_percent !== undefined && this.item.delta_percent !== null) {
            return `${sign}${this.item.delta_percent}%`;
        }
        return `${sign}${this.formatValue(delta)}`;
    }

    get deltaClass() {
        const delta = this.item.delta || 0;
        if (!delta) {
            return "o_dbc_delta_flat";
        }
        return delta > 0 ? "o_dbc_delta_up" : "o_dbc_delta_down";
    }

    get compareLabel() {
        return this.item.compare_mode === "year" ? _t("vs last year") : _t("vs previous period");
    }

    /**
     * How the number moved since the last recorded point, when there is one
     * and it moved at all: "unchanged since yesterday" is not news.
     */
    get snapshotLabel() {
        const since = this.item.since_snapshot;
        if (!since || !since.delta) {
            return "";
        }
        const sign = since.delta > 0 ? "+" : "";
        return _t("%s since %s", `${sign}${this.formatValue(since.delta)}`, since.day);
    }

    get targetPercent() {
        if (!this.item.target) {
            return 0;
        }
        return Math.max(0, Math.min(100, (this.item.value / this.item.target) * 100));
    }

    /** Stroke offset of the progress ring, drawn on a 100-unit circumference. */
    get ringOffset() {
        return 100 - this.targetPercent;
    }

    /** Bullet geometry: value bar, target mark and range, on one scale. */
    get bullet() {
        const value = Math.max(0, Number(this.item.value || 0));
        const target = Math.max(0, Number(this.item.target || 0));
        const scale = Math.max(target * 1.25, value * 1.05, 1);
        return {
            fill: (value / scale) * 100,
            mark: (target / scale) * 100,
            range: Math.min(100, (target / scale) * 100),
            max: this.formatValue(scale),
        };
    }

    /** The alert level of a number card: false, "warning" or "danger". */
    get alert() {
        return this.item.alert || false;
    }

    get statusClass() {
        return this.alert ? `o_dbc_status_${this.alert}` : "o_dbc_status_ok";
    }

    /** A caption for the sparkline: what it spans. */
    get sparkCaption() {
        const points = this.item.spark || [];
        if (points.length < 2) {
            return "";
        }
        return `${points[0].label} – ${points[points.length - 1].label}`;
    }

    get sparkTitle() {
        const points = this.item.spark || [];
        if (!points.length) {
            return "";
        }
        const values = points.map((point) => Number(point.value || 0));
        const peak = points[values.indexOf(Math.max(...values))];
        return _t("%s points, peak %s (%s)", points.length, peak.label, this.formatValue(peak.value));
    }

    /** Sparkline geometry: a 100x28 box, values normalised into it. */
    get spark() {
        const points = this.item.spark || [];
        if (points.length < 2) {
            return null;
        }
        const values = points.map((point) => Number(point.value || 0));
        const min = Math.min(...values);
        const max = Math.max(...values);
        const span = max - min || 1;
        const coords = values.map((value, index) => {
            const x = (index / (values.length - 1)) * 100;
            const y = 26 - ((value - min) / span) * 22;
            return [Number(x.toFixed(2)), Number(y.toFixed(2))];
        });
        const line = coords.map(([x, y]) => `${x},${y}`).join(" ");
        const area = `M0,28 L${line.replace(/ /g, " L")} L100,28 Z`;
        return { line, area, last: coords[coords.length - 1] };
    }

    /**
     * The facts under a chart, as [label, value] pairs: the total the picture
     * adds up to, how many parts it has, and the part that matters most.
     */
    get dataInfo() {
        const item = this.item;
        if (!this.isChart || item.error) {
            return null;
        }
        const facts = [];
        const total = this.formatFull(item.value || 0);
        if (item.kind === "stacked" || this.isMatrix) {
            facts.push([_t("Total"), total]);
            facts.push([item.group_label || _t("Groups"), String((item.categories || []).length)]);
            facts.push([item.stack_label || _t("Stacks"), String((item.series || []).length)]);
            return facts;
        }
        const points = item.points || [];
        if (item.kind === "scatter") {
            facts.push([_t("Records"), String(points.length)]);
            facts.push([item.measure_label || "x", this.formatValue(points.reduce((a, p) => a + p.x, 0) / (points.length || 1))]);
            facts.push([item.measure2_label || "y", this.formatValue(points.reduce((a, p) => a + p.y, 0) / (points.length || 1))]);
            return facts;
        }
        if (item.kind === "waterfall") {
            const rises = points.filter((point) => Number(point.value || 0) >= 0);
            facts.push([_t("Total"), total]);
            facts.push([_t("Up"), String(rises.length)]);
            facts.push([_t("Down"), String(points.length - rises.length)]);
            return facts;
        }
        if (item.kind === "pareto") {
            const sorted = [...points].sort((a, b) => Math.abs(b.value || 0) - Math.abs(a.value || 0));
            const whole = sorted.reduce((sum, point) => sum + Math.abs(point.value || 0), 0);
            let running = 0;
            let vital = 0;
            for (const point of sorted) {
                if (running / (whole || 1) >= 0.8) {
                    break;
                }
                running += Math.abs(point.value || 0);
                vital += 1;
            }
            facts.push([_t("Total"), total]);
            facts.push([_t("80% comes from"), _t("%s of %s", vital, sorted.length)]);
            if (sorted[0]) {
                facts.push([_t("Biggest"), `${this.formatValue(sorted[0].value)} · ${sorted[0].label}`]);
            }
            return facts;
        }
        if (item.kind === "combo" || item.kind === "bubble") {
            facts.push([item.measure_label || _t("Bars"), total]);
            facts.push([item.measure2_label || _t("Line"), this.formatFull(item.value2 || 0)]);
            facts.push([item.group_label || _t("Groups"), String(points.length)]);
            return facts;
        }
        if (["line", "area"].includes(item.kind)) {
            const values = points.map((point) => Number(point.value || 0));
            const peak = points[values.indexOf(Math.max(...values))];
            const average = values.length ? values.reduce((a, b) => a + b, 0) / values.length : 0;
            facts.push([_t("Total"), total]);
            if (peak) {
                facts.push([_t("Peak"), `${this.formatValue(peak.value)} · ${peak.label}`]);
            }
            facts.push([_t("Average"), this.formatValue(average)]);
            const forecast = this.forecast;
            if (forecast) {
                facts.push([_t("Forecast"), `${this.formatValue(forecast[forecast.length - 1])} · ${points[points.length - 1].label}`]);
            }
            return facts;
        }
        const real = points.filter((point) => !point.folded);
        const top = real[0];
        facts.push([_t("Total"), total]);
        facts.push([item.group_label || _t("Groups"), String(real.length)]);
        if (top) {
            const share = top.share !== undefined ? ` · ${top.share}%` : "";
            facts.push([_t("Top"), `${top.label}${share}`]);
        }
        return facts;
    }

    /** The chart's numbers as rows, for the table view and the CSV. */
    get tableRows() {
        const item = this.item;
        if (item.kind === "stacked" || this.isMatrix) {
            const categories = item.categories || [];
            return categories.map((category, index) => ({
                label: category.label,
                cells: (item.series || []).map((entry) => this.formatValue(entry.values[index])),
            }));
        }
        if (item.kind === "scatter") {
            return (item.points || []).map((point) => ({
                label: point.label,
                cells: [this.formatValue(point.x), this.formatValue(point.y)],
            }));
        }
        if (["combo", "bubble"].includes(item.kind)) {
            return (item.points || []).map((point) => ({
                label: point.label,
                cells: [this.formatValue(point.value), this.formatValue(point.value2)],
            }));
        }
        return (item.points || []).map((point) => ({
            label: point.label,
            cells: [this.formatValue(point.value), point.share !== undefined ? `${point.share}%` : ""],
        }));
    }

    get tableHead() {
        const item = this.item;
        if (item.kind === "stacked" || this.isMatrix) {
            return [item.group_label || _t("Group"), ...(item.series || []).map((entry) => entry.label)];
        }
        if (item.kind === "scatter") {
            return [_t("Record"), item.measure_label || "x", item.measure2_label || "y"];
        }
        if (["combo", "bubble"].includes(item.kind)) {
            return [item.group_label || _t("Group"), item.measure_label || _t("Bars"),
                    item.measure2_label || _t("Line")];
        }
        return [item.group_label || item.date_label || _t("Group"), _t("Value"), _t("Share")];
    }

    get maxRowValue() {
        return Math.max(1, ...(this.item.rows || []).map((row) => Math.abs(row.value || 0)));
    }

    rowWidth(row) {
        return `${Math.max(2, (Math.abs(row.value || 0) / this.maxRowValue) * 100)}%`;
    }

    get maxPointValue() {
        return Math.max(1, ...(this.item.points || []).map((point) => Math.abs(point.value || 0)));
    }

    pointWidth(point) {
        return `${Math.max(2, (Math.abs(point.value || 0) / this.maxPointValue) * 100)}%`;
    }

    pointColor(index) {
        return this.item.multicolor ? this.ramp(index + 1)[index] : this.color;
    }

    /** The card's own palette when it has one, the board's otherwise. */
    get palette() {
        const own = this.item.palette;
        return own && own !== "board" ? own : this.props.palette || "default";
    }

    /** The categorical ramp of this card, or shades of its colour. */
    ramp(count) {
        return seriesColors(count, this.palette, this.color);
    }

    /** How round a bar's ends are; `base` is this chart's usual radius. */
    barRadius(base) {
        return { square: 0, pill: 999 }[this.item.bar_shape] ?? base;
    }

    /** The point the card was asked to make stand out, or -1. */
    emphasisIndex(points) {
        const mode = this.item.emphasis;
        if (!mode || mode === "none" || !points.length) {
            return -1;
        }
        if (mode === "last") {
            return points.length - 1;
        }
        let best = 0;
        points.forEach((point, index) => {
            const value = Number(point.value || 0);
            const top = Number(points[best].value || 0);
            if (mode === "max" ? value > top : value < top) {
                best = index;
            }
        });
        return best;
    }

    get emptyLabel() {
        return this.item.error ? _t("This card could not be computed") : _t("Nothing in this period");
    }

    get periodBadge() {
        if (this.item.period_mode === "own" && this.item.own_period) {
            return this.item.own_period.replace(/_/g, " ");
        }
        return "";
    }

    // ------------------------------------------------------------------
    // Clicks
    // ------------------------------------------------------------------
    onCardClick() {
        if (this.item.kind === "text") {
            return;
        }
        this.props.onDrill(this.item, {});
    }

    onOpenClick(ev) {
        ev.stopPropagation();
        this.props.onDrill(this.item, {});
    }

    onRowClick(row) {
        this.props.onDrill(this.item, { resId: row.id });
    }

    onPointClick(point, onDate = false) {
        if (point.folded) {
            return; // "Others" is a leftover, not a filter
        }
        // `onDate` is a calendar cell or a point on a line: the key is a day
        // of the date field, not a value of the split field.
        if (this.props.onFocus) {
            this.props.onFocus(this.item, point, onDate);
        } else {
            this.props.onDrill(this.item, { key: point.key, onDate });
        }
    }

    onEditClick(ev) {
        ev.stopPropagation();
        if (this.props.onEdit) {
            this.props.onEdit(this.item);
        }
    }

    onRefreshClick(ev) {
        ev.stopPropagation();
        if (this.props.onRefresh) {
            this.props.onRefresh(this.item);
        }
    }

    onPinClick(ev) {
        ev.stopPropagation();
        if (this.props.onPin) {
            this.props.onPin(this.item);
        }
    }

    toggleMenu(ev) {
        ev.stopPropagation();
        this.state.menuOpen = !this.state.menuOpen;
    }

    toggleTable() {
        this.state.menuOpen = false;
        this.state.showTable = !this.state.showTable;
    }

    isFocused(point) {
        const focus = this.props.focus;
        return Boolean(focus && focus.item_id === this.item.id && focus.key === point.key);
    }

    // ------------------------------------------------------------------
    // Exports
    // ------------------------------------------------------------------
    get fileStem() {
        return String(this.item.name || "card").trim().replace(/[^\w-]+/g, "_").replace(/^_+|_+$/g, "") || "card";
    }

    /** The chart as it stands, for a download or for the PDF. */
    chartImage() {
        return canvasToPng(this.canvasRef.el);
    }

    /** The colour a sub-value was given, as CSS. */
    subStyle(sub) {
        return `--dbc-sub: ${CARD_COLORS[sub.color] || CARD_COLORS.slate};`;
    }

    subBarStyle(sub) {
        const share = Math.max(0, Math.min(100, Number(sub.share || 0)));
        return `width: ${share}%;`;
    }

    subTitle(sub) {
        const parts = [`${sub.label}: ${this.formatFull(sub.value)}`];
        if (sub.share !== null && sub.share !== undefined) {
            parts.push(_t("%s%% of %s", sub.share, this.formatFull(this.item.value || 0)));
        }
        parts.push(_t("Click to open these records"));
        return parts.join(" · ");
    }

    /** A small number opens exactly the records it counted. */
    onSubClick(sub) {
        if (this.props.onDrill) {
            this.props.onDrill(this.item, { subvalue: sub.id });
        }
    }

    /** Gold, silver, bronze - only when the card asked for them. */
    medal(index) {
        if (!this.item.rank_medals || index > 2) {
            return "";
        }
        return ["gold", "silver", "bronze"][index];
    }

    /** A tint on a row the colour rule matched. */
    rowFlagStyle(row) {
        if (!row.flagged) {
            return "";
        }
        const colour = CARD_COLORS[this.item.cf_color] || CARD_COLORS.rose;
        return `background-color: ${withAlpha(colour, 0.14)}; box-shadow: inset 3px 0 0 ${colour};`;
    }

    /** The bar drawn behind a number in a table cell. */
    cellBarStyle(row) {
        const rows = this.item.rows || [];
        const biggest = Math.max(1, ...rows.map((entry) => Math.abs(entry.value || 0)));
        const share = Math.min(100, (Math.abs(row.value || 0) / biggest) * 100);
        return `width: ${share}%; background-color: ${withAlpha(this.color, 0.18)};`;
    }

    exportPdf() {
        this.state.menuOpen = false;
        if (this.props.onPdf) {
            this.props.onPdf(this.item, this.chartImage());
        }
    }

    exportXlsx() {
        this.state.menuOpen = false;
        if (this.props.onXlsx) {
            this.props.onXlsx(this.item);
        }
    }

    exportImage() {
        this.state.menuOpen = false;
        const canvas = this.canvasRef.el;
        if (!canvas) {
            return;
        }
        // Draw on white first: a transparent PNG is unreadable in a dark viewer.
        const copy = document.createElement("canvas");
        copy.width = canvas.width;
        copy.height = canvas.height;
        const ctx = copy.getContext("2d");
        ctx.fillStyle = "#ffffff";
        ctx.fillRect(0, 0, copy.width, copy.height);
        ctx.drawImage(canvas, 0, 0);
        copy.toBlob((blob) => blob && download(`${this.fileStem}.png`, blob, "image/png"));
    }

    exportCsv() {
        this.state.menuOpen = false;
        const item = this.item;
        const lines = [];
        if (item.kind === "list") {
            const head = [_t("Record"), ...(item.measure ? [item.measure_label || item.measure] : []),
                          ...(item.columns || []).map((column) => column.label)];
            lines.push(head.map(csvCell).join(","));
            for (const row of item.rows || []) {
                const cells = [row.label, ...(item.measure ? [row.value] : []), ...(row.cells || [])];
                lines.push(cells.map(csvCell).join(","));
            }
        } else if (this.isNumber) {
            lines.push([_t("Card"), _t("Value")].map(csvCell).join(","));
            lines.push([item.name, item.value].map(csvCell).join(","));
            for (const variable of item.variables || []) {
                lines.push([variable.label || variable.name, variable.value].map(csvCell).join(","));
            }
        } else {
            lines.push(this.tableHead.map(csvCell).join(","));
            for (const row of this.tableRows) {
                lines.push([row.label, ...row.cells].map(csvCell).join(","));
            }
        }
        download(`${this.fileStem}.csv`, "﻿" + lines.join("\n"), "text/csv;charset=utf-8");
    }

    exportJson() {
        this.state.menuOpen = false;
        download(`${this.fileStem}.json`, JSON.stringify(this.item, null, 2), "application/json");
    }

    // ------------------------------------------------------------------
    // Charts
    // ------------------------------------------------------------------
    destroyChart() {
        if (this.chart) {
            this.chart.destroy();
            this.chart = null;
        }
    }

    renderChart() {
        if (this.item.pending) {
            return;
        }
        this.destroyChart();
        if (!this.isChart || !this.hasContent || this.state.showTable || !this.canvasRef.el || !window.Chart) {
            return;
        }
        const ink = chartInk();
        const kind = this.item.kind;
        const builder = {
            bar: () => this.barConfig(ink, false),
            lollipop: () => this.lollipopConfig(ink),
            combo: () => this.comboConfig(ink),
            waterfall: () => this.waterfallConfig(ink),
            pareto: () => this.paretoConfig(ink),
            bubble: () => this.bubbleConfig(ink),
            hbar: () => this.barConfig(ink, true),
            funnel: () => this.funnelConfig(ink),
            stacked: () => this.stackedConfig(ink),
            line: () => this.lineConfig(ink, false),
            area: () => this.lineConfig(ink, true),
            pie: () => this.arcConfig("doughnut", "0%"),
            donut: () => this.arcConfig("doughnut", "58%"),
            polar: () => this.arcConfig("polarArea", null),
            radar: () => this.radarConfig(ink),
            scatter: () => this.scatterConfig(ink),
        }[kind];
        const config = builder();
        const arc = ARC_KINDS.includes(kind);
        const horizontal = ["hbar", "funnel"].includes(kind);
        const legend = this.item.legend || "right";
        const showLegend = ["pie", "donut", "polar", "stacked"].includes(kind) && legend !== "none";
        config.plugins = [valueLabels, targetLine, centerTotal];
        config.options = Object.assign(
            {
                responsive: true,
                maintainAspectRatio: false,
                animation: { duration: 240 },
                onClick: (event, elements) => this.onChartClick(elements),
                // Room for the value labels, which are drawn just outside the
                // bar or point and would otherwise sit on the axis.
                layout: {
                    padding: {
                        top: this.item.show_values && !arc && !horizontal ? 14 : 4,
                        right: this.item.show_values && horizontal ? 32 : 6,
                    },
                },
                plugins: {
                    legend: {
                        display: showLegend,
                        position: ["bottom", "top", "left", "right"].includes(legend) ? legend : "right",
                        labels: { color: ink.ink, boxWidth: 10, font: { size: 11 } },
                    },
                    tooltip: { callbacks: { label: (context) => this.tooltipLabel(context) } },
                    dbcValueLabels: {
                        enabled: Boolean(this.item.show_values) && kind !== "scatter",
                        color: ink.ink,
                        arc,
                        format: (value) => this.formatValue(value),
                    },
                    dbcTargetLine: {
                        value: !arc && !["radar", "funnel", "scatter"].includes(kind) ? this.item.target : 0,
                        color: ink.ink,
                        label: _t("Target"),
                    },
                    dbcCenterTotal: {
                        enabled: kind === "donut" && Boolean(this.item.center_total),
                        text: this.formatValue((this.item.points || []).reduce(
                            (sum, point) => sum + Math.abs(Number(point.value || 0)), 0)),
                        caption: _t("Total"),
                        color: ink.ink,
                        muted: ink.muted,
                    },
                },
            },
            config.options || {}
        );
        this.chart = new window.Chart(this.canvasRef.el, config);
    }

    tooltipLabel(context) {
        const kind = this.item.kind;
        if (kind === "stacked") {
            const series = (this.item.series || [])[context.datasetIndex] || {};
            return `${series.label}: ${this.formatFull(context.raw)}`;
        }
        if (context.datasetIndex === 1 && ["line", "area"].includes(kind)) {
            return `${_t("Forecast")}: ${this.formatFull(context.raw)}`;
        }
        const point = (this.item.points || [])[context.dataIndex] || {};
        if (kind === "scatter") {
            return `${point.label}: ${this.formatValue(point.x)} / ${this.formatValue(point.y)}`;
        }
        if (kind === "waterfall") {
            const raw = Array.isArray(context.raw) ? context.raw[1] - context.raw[0] : context.raw;
            return `${context.label}: ${this.formatFull(raw)}`;
        }
        if (kind === "pareto") {
            return context.datasetIndex === 1
                ? `${_t("Cumulative")}: ${context.raw}%`
                : `${context.label}: ${this.formatFull(context.raw)}`;
        }
        if (kind === "bubble") {
            const bubble = (this.item.points || [])[context.dataIndex] || {};
            return `${bubble.label}: ${this.formatValue(bubble.value)} / ${this.formatValue(bubble.value2)} · ${bubble.count} ${_t("records")}`;
        }
        if (kind === "combo") {
            const isLine = context.datasetIndex === 1;
            const label = isLine ? (this.item.measure2_label || _t("Line"))
                : (this.item.measure_label || _t("Bars"));
            return `${label}: ${this.formatFull(isLine ? point.value2 : point.value)}`;
        }
        const value = Array.isArray(context.raw) ? Math.abs(context.raw[1] - context.raw[0]) : point.value;
        const share = point.share !== undefined && ["pie", "donut", "polar", "funnel"].includes(kind)
            ? ` (${point.share}%)` : "";
        return `${point.label}: ${this.formatFull(value)}${share}`;
    }

    /** Colours for one bar per point, dimmed when another point is the focus. */
    pointColors(points) {
        const focus = this.props.focus;
        const focused = focus && focus.item_id === this.item.id;
        const ramp = this.ramp(points.length);
        const stress = this.emphasisIndex(points);
        return points.map((point, index) => {
            let color = this.item.multicolor ? ramp[index] : this.color;
            if ((point.value || 0) < 0) {
                color = NEGATIVE;
            }
            if ((focused && focus.key !== point.key) || (stress >= 0 && index !== stress)) {
                return withAlpha(color, 0.28);
            }
            return color;
        });
    }

    /** Axes shared by the cartesian charts. */
    cartesianScales(ink, horizontal, free = false) {
        const valueAxis = {
            beginAtZero: !free,
            type: this.item.log_scale ? "logarithmic" : "linear",
            ticks: { color: ink.muted, font: { size: 11 }, callback: (value) => this.formatValue(value) },
            grid: { color: ink.grid, drawTicks: false, display: this.item.show_grid !== false },
            border: { display: false },
            title: this.axisTitle(
                this.item.measure_label || (this.item.aggregate === "count" ? _t("Records") : ""), ink),
        };
        const labelAxis = {
            ticks: { color: ink.muted, font: { size: 11 }, maxRotation: 0, autoSkip: true },
            grid: { display: false },
            border: { color: ink.grid },
            title: this.axisTitle(this.item.group_label || this.item.date_label || "", ink),
        };
        return horizontal ? { x: valueAxis, y: labelAxis } : { x: labelAxis, y: valueAxis };
    }

    /** An axis title, shown only when the card asked for titles and has one. */
    axisTitle(text, ink) {
        return { display: Boolean(this.item.axis_titles && text), text, color: ink.muted, font: { size: 11 } };
    }

    barConfig(ink, horizontal) {
        const points = this.item.points || [];
        return {
            type: "bar",
            data: {
                labels: points.map((point) => point.label),
                datasets: [{
                    data: points.map((point) => point.value),
                    backgroundColor: this.pointColors(points),
                    borderRadius: this.barRadius(3),
                    maxBarThickness: 42,
                }],
            },
            options: { indexAxis: horizontal ? "y" : "x", scales: this.cartesianScales(ink, horizontal) },
        };
    }

    funnelConfig(ink) {
        // Floating bars centred on zero: each stage is [-v/2, v/2], widest first.
        const points = this.item.points || [];
        return {
            type: "bar",
            data: {
                labels: points.map((point) => point.label),
                datasets: [{
                    data: points.map((point) => [-Math.abs(point.value) / 2, Math.abs(point.value) / 2]),
                    backgroundColor: this.pointColors(points),
                    borderSkipped: false,
                    borderRadius: this.barRadius(4),
                    barPercentage: 0.96,
                    categoryPercentage: 0.96,
                }],
            },
            options: {
                indexAxis: "y",
                scales: {
                    x: { display: false },
                    y: { ticks: { color: ink.ink, font: { size: 11 } }, grid: { display: false }, border: { display: false } },
                },
            },
        };
    }

    stackedConfig(ink) {
        const categories = this.item.categories || [];
        let series = this.item.series || [];
        const colors = this.ramp(series.length);
        if (this.item.stack_percent) {
            // Recomputed here rather than on the server: the same numbers are
            // still what the tooltip, the table view and the CSV carry.
            const totals = categories.map((_category, index) =>
                series.reduce((sum, entry) => sum + Math.abs(Number(entry.values[index] || 0)), 0));
            series = series.map((entry) => ({
                ...entry,
                values: entry.values.map((value, index) =>
                    totals[index] ? Math.round((Number(value || 0) / totals[index]) * 1000) / 10 : 0),
            }));
        }
        return {
            type: "bar",
            data: {
                labels: categories.map((category) => category.label),
                datasets: series.map((entry, index) => ({
                    label: entry.label,
                    data: entry.values,
                    backgroundColor: colors[index],
                    borderRadius: this.barRadius(2),
                    maxBarThickness: 42,
                })),
            },
            options: {
                indexAxis: this.item.stack_horizontal ? "y" : "x",
                scales: (() => {
                    const scales = this.cartesianScales(ink, false);
                    scales.x.stacked = true;
                    scales.y.stacked = true;
                    if (this.item.stack_horizontal) {
                        // Sideways: the categories run down the left.
                        [scales.x, scales.y] = [scales.y, scales.x];
                    }
                    if (this.item.stack_percent) {
                        // Every bar the same height: the pieces read as shares.
                        scales.y.max = 100;
                        scales.y.ticks = { ...(scales.y.ticks || {}),
                                           callback: (value) => `${value}%` };
                    }
                    return scales;
                })(),
            },
        };
    }

    /**
     * Bars with a line over them: two measures, one split.
     *
     * With a second axis the line keeps its own scale on the right, which is
     * the whole point of the card - a count and a percentage on one chart
     * without the smaller of the two flattening into the floor.
     */
    comboConfig(ink) {
        const points = this.item.points || [];
        const scales = this.cartesianScales(ink, false);
        if (this.item.dual_axis) {
            scales.y2 = {
                position: "right",
                grid: { drawOnChartArea: false },
                ticks: { color: ink.muted, font: { size: 10 } },
                border: { color: ink.grid },
            };
        }
        return {
            type: "bar",
            data: {
                labels: points.map((point) => point.label),
                datasets: [
                    {
                        type: "bar",
                        label: this.item.measure_label || _t("Bars"),
                        data: points.map((point) => point.value),
                        backgroundColor: withAlpha(this.color, 0.85),
                        borderRadius: this.barRadius(4),
                        order: 2,
                    },
                    {
                        type: "line",
                        label: this.item.measure2_label || _t("Line"),
                        data: points.map((point) => point.value2),
                        // The line takes the next colour of the board's palette,
                        // so it never disappears into the bars beneath it.
                        borderColor: seriesColors(2, this.palette, this.color)[1],
                        backgroundColor: "transparent",
                        borderWidth: 2,
                        pointRadius: points.length > 24 ? 0 : 3,
                        tension: 0.25,
                        yAxisID: this.item.dual_axis ? "y2" : "y",
                        order: 1,
                    },
                ],
            },
            options: { scales },
        };
    }

    /** A lollipop: the bars as thin stems, a dot on each. Same reading, lighter. */
    lollipopConfig(ink) {
        const points = this.item.points || [];
        const colours = this.pointColors(points);
        return {
            type: "bar",
            data: {
                labels: points.map((point) => point.label),
                datasets: [
                    { type: "bar", data: points.map((point) => point.value),
                      backgroundColor: colours.map((colour) => withAlpha(colour, 0.55)),
                      barThickness: 3, order: 2 },
                    { type: "line", data: points.map((point) => point.value), showLine: false,
                      pointRadius: 6, pointHoverRadius: 8, pointBackgroundColor: colours,
                      pointBorderColor: "#ffffff", pointBorderWidth: 1.5, order: 1 },
                ],
            },
            options: { scales: this.cartesianScales(ink, false) },
        };
    }

    /**
     * A waterfall: each bar starts where the last one ended.
     *
     * Chart.js draws a floating bar from a two-number datum, so the running
     * total goes in as `[from, to]` and the last bar is the total itself,
     * drawn from zero. Rises and falls take their own colours because that
     * is the whole point of the picture.
     */
    waterfallConfig(ink) {
        const points = this.item.points || [];
        const palette = seriesColors(3, this.palette, this.color);
        const bars = [];
        const colours = [];
        let running = 0;
        for (const point of points) {
            const value = Number(point.value || 0);
            bars.push([running, running + value]);
            colours.push(value < 0 ? palette[2] : this.color);
            running += value;
        }
        bars.push([0, running]);
        colours.push(withAlpha(palette[1], 0.9));
        return {
            type: "bar",
            data: {
                labels: [...points.map((point) => point.label), _t("Total")],
                datasets: [{ data: bars, backgroundColor: colours, borderRadius: this.barRadius(3) }],
            },
            options: { scales: this.cartesianScales(ink, false) },
        };
    }

    /**
     * A pareto: the bars in order, and the running share drawn over them.
     *
     * The line is a percentage, so it gets its own axis pinned to 0-100 -
     * the point of the chart is reading "these three make up 80%" off it.
     */
    paretoConfig(ink) {
        const points = [...(this.item.points || [])].sort(
            (a, b) => Math.abs(b.value || 0) - Math.abs(a.value || 0));
        const total = points.reduce((sum, point) => sum + Math.abs(point.value || 0), 0);
        let running = 0;
        const cumulative = points.map((point) => {
            running += Math.abs(point.value || 0);
            return total ? Math.round((running / total) * 1000) / 10 : 0;
        });
        const scales = this.cartesianScales(ink, false);
        scales.y2 = {
            position: "right",
            min: 0,
            max: 100,
            grid: { drawOnChartArea: false },
            ticks: { color: ink.muted, font: { size: 10 }, callback: (value) => `${value}%` },
            border: { color: ink.grid },
        };
        return {
            type: "bar",
            data: {
                labels: points.map((point) => point.label),
                datasets: [
                    { type: "bar", label: this.item.measure_label || _t("Value"),
                      data: points.map((point) => point.value),
                      backgroundColor: withAlpha(this.color, 0.85), borderRadius: this.barRadius(3), order: 2 },
                    { type: "line", label: _t("Cumulative"), data: cumulative,
                      borderColor: seriesColors(2, this.palette, this.color)[1],
                      backgroundColor: "transparent", borderWidth: 2,
                      pointRadius: points.length > 20 ? 0 : 3, tension: 0.2,
                      yAxisID: "y2", order: 1 },
                ],
            },
            options: { scales },
        };
    }

    /**
     * A bubble: two measures place the circle, the record count sizes it.
     *
     * Radius is scaled from the square root of the count, so a group with
     * four times the records draws twice as wide - area, not radius, is what
     * the eye reads as "how many".
     */
    bubbleConfig(ink) {
        const points = this.item.points || [];
        const biggest = Math.max(1, ...points.map((point) => point.count || 0));
        return {
            type: "bubble",
            data: {
                datasets: [{
                    data: points.map((point) => ({
                        x: Number(point.value || 0),
                        y: Number(point.value2 || 0),
                        r: 5 + 18 * Math.sqrt((point.count || 0) / biggest),
                    })),
                    backgroundColor: withAlpha(this.color, 0.45),
                    borderColor: this.color,
                    borderWidth: 1.5,
                }],
            },
            options: { scales: this.cartesianScales(ink, false) },
        };
    }

    lineConfig(ink, fill) {
        const points = this.item.points || [];
        const stress = this.emphasisIndex(points);
        const dot = points.length > 24 ? 0 : 3;
        const each = (value, other) => (stress >= 0 ? points.map((_point, index) => (index === stress ? value : other)) : other);
        const datasets = [{
            data: points.map((point) => point.value),
            borderColor: this.color,
            backgroundColor: withAlpha(this.color, 0.16),
            borderWidth: 2,
            pointRadius: each(6, dot),
            pointHoverRadius: each(8, dot + 2),
            pointBackgroundColor: this.color,
            pointBorderColor: each("#ffffff", this.color),
            pointBorderWidth: each(2, 1),
            fill,
            stepped: this.item.stepped ? "before" : false,
            tension: this.item.stepped ? 0 : 0.25,
        }];
        const forecast = this.forecast;
        if (forecast) {
            datasets.push({
                data: forecast,
                borderColor: ink.muted || ink.grid || "#888",
                borderDash: [5, 4],
                borderWidth: 1.5,
                pointRadius: 0,
                fill: false,
                tension: 0,
                spanGaps: false,
            });
        }
        return {
            type: "line",
            data: { labels: points.map((point) => point.label), datasets },
            options: { scales: this.cartesianScales(ink, false, Boolean(this.item.free_axis)) },
        };
    }

    arcConfig(type, cutout) {
        const points = this.item.points || [];
        const focus = this.props.focus;
        const focused = focus && focus.item_id === this.item.id;
        const colors = this.ramp(points.length).map((color, index) =>
            focused && focus.key !== points[index].key ? withAlpha(color, 0.28) : color
        );
        const options = {};
        if (cutout !== null) {
            options.cutout = cutout;
        }
        if (this.item.semicircle) {
            options.rotation = -90;
            options.circumference = 180;
        }
        return {
            type,
            data: {
                labels: points.map((point) => point.label),
                datasets: [{ data: points.map((point) => Math.abs(point.value)), backgroundColor: colors, borderWidth: 0 }],
            },
            options,
        };
    }

    radarConfig(ink) {
        const points = this.item.points || [];
        return {
            type: "radar",
            data: {
                labels: points.map((point) => point.label),
                datasets: [{
                    data: points.map((point) => point.value),
                    borderColor: this.color,
                    backgroundColor: withAlpha(this.color, 0.2),
                    pointBackgroundColor: this.color,
                    borderWidth: 2,
                }],
            },
            options: {
                scales: {
                    r: {
                        beginAtZero: true,
                        ticks: { color: ink.muted, backdropColor: "transparent", font: { size: 10 } },
                        grid: { color: ink.grid },
                        angleLines: { color: ink.grid },
                        pointLabels: { color: ink.ink, font: { size: 11 } },
                    },
                },
            },
        };
    }

    scatterConfig(ink) {
        const points = this.item.points || [];
        const colors = this.item.multicolor ? this.ramp(points.length) : points.map(() => this.color);
        const scales = this.cartesianScales(ink, false);
        scales.x = Object.assign({}, scales.y, { title: { display: true, text: this.item.measure_label || "", color: ink.muted } });
        scales.y = Object.assign({}, scales.y, { title: { display: true, text: this.item.measure2_label || "", color: ink.muted } });
        return {
            type: "scatter",
            data: {
                datasets: [{
                    data: points.map((point) => ({ x: point.x, y: point.y })),
                    backgroundColor: colors,
                    pointRadius: 5,
                    pointHoverRadius: 7,
                }],
            },
            options: { scales },
        };
    }

    onChartClick(elements) {
        if (!elements || !elements.length) {
            return;
        }
        const index = elements[0].index;
        const kind = this.item.kind;
        if (kind === "scatter") {
            const point = (this.item.points || [])[index];
            if (point) {
                this.props.onDrill(this.item, { resId: point.id });
            }
            return;
        }
        const point = kind === "stacked"
            ? (this.item.categories || [])[index]
            : (this.item.points || [])[index];
        if (!point || point.folded) {
            return;
        }
        const onDate = ["line", "area"].includes(kind);
        if (this.props.onFocus) {
            this.props.onFocus(this.item, point, onDate);
        } else {
            this.props.onDrill(this.item, { key: point.key, onDate });
        }
    }
}
