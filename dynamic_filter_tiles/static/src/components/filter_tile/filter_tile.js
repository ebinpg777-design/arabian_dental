/** @odoo-module **/

import { Component, onMounted, onWillUnmount, onWillUpdateProps, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { humanNumber } from "@web/core/utils/numbers";
import { formatFloat, formatInteger } from "@web/views/fields/formatters";
import { resolveTileColor } from "../../core/tile_colors";

const ANIMATION_MS = 420;
const SPARK_W = 100;
const SPARK_H = 30;

// Keep in sync with models/filter_tile.py. The server clamps too; these only
// stop the drag itself at the edge.
const MIN_TILE_WIDTH = 120;
const MAX_TILE_WIDTH = 520;

/** Ease-out cubic: fast start, gentle landing - reads as "counting up". */
function easeOut(t) {
    return 1 - Math.pow(1 - t, 3);
}

/**
 * One filter tile.
 *
 * Purely presentational: the bar owns the data and the selection, the tile owns
 * the drawing (count-up, sparkline, share bar, goal ring) and forwards clicks.
 */
export class FilterTile extends Component {
    static template = "dynamic_filter_tiles.FilterTile";
    static props = {
        def: Object, // tile definition, as served by filter.tile.get_tile_registry
        data: { type: Object, optional: true }, // {value, count, trend, delta, alert, error}
        total: { type: Number, optional: true },
        active: { type: Boolean, optional: true },
        compact: { type: Boolean, optional: true },
        canManage: { type: Boolean, optional: true },
        selectable: { type: Boolean, optional: true }, // multi-select mode is on
        loading: { type: Boolean, optional: true },
        shortcut: { type: Number, optional: true }, // 1-9: the Alt+n that toggles it
        onSelect: { type: Function, optional: true },
        // Its filter is something the browser cannot evaluate: shown, greyed,
        // and never applied - a bad tile used to take the view down with it.
        // (client, 2026-09-10)
        broken: { type: Boolean, optional: true },
        onEdit: { type: Function, optional: true },
        onBreakdown: { type: Function, optional: true },
        onResize: { type: Function, optional: true }, // (width) => persisted
        onDelete: { type: Function, optional: true },
    };
    static defaultProps = {
        data: null,
        total: 0,
        active: false,
        compact: false,
        canManage: false,
        selectable: false,
        loading: false,
        shortcut: 0,
    };

    setup() {
        this.state = useState({ shown: this.rawValue(this.props) });
        // Width while a drag is in progress; 0 the rest of the time, when the
        // tile's own `width` (or the stylesheet default) rules.
        this.resize = useState({ width: 0, dragging: false });
        this.stopResize = null;
        this.frame = null;
        this.reduceMotion =
            typeof window !== "undefined" &&
            window.matchMedia &&
            window.matchMedia("(prefers-reduced-motion: reduce)").matches;

        onMounted(() => this.animateTo(this.rawValue(this.props), 0));
        onWillUpdateProps((nextProps) => {
            const next = this.rawValue(nextProps);
            if (next !== this.state.shown) {
                this.animateTo(next, this.state.shown);
            }
        });
        onWillUnmount(() => {
            this.stopAnimation();
            this.stopResize?.();
        });
    }

    rawValue(props) {
        return (props.data && Number(props.data.value)) || 0;
    }

    stopAnimation() {
        if (this.frame) {
            window.cancelAnimationFrame(this.frame);
            this.frame = null;
        }
    }

    animateTo(target, from) {
        this.stopAnimation();
        if (this.reduceMotion || target === from) {
            this.state.shown = target;
            return;
        }
        const start = performance.now();
        const step = (now) => {
            const progress = Math.min((now - start) / ANIMATION_MS, 1);
            this.state.shown = from + (target - from) * easeOut(progress);
            if (progress < 1) {
                this.frame = window.requestAnimationFrame(step);
            } else {
                this.frame = null;
                this.state.shown = target;
            }
        };
        this.frame = window.requestAnimationFrame(step);
    }

    get isMeasure() {
        return Boolean(this.props.def.measure);
    }

    /** Big number: compact for measures, exact for counts (until they get silly). */
    get formattedValue() {
        if (this.props.loading && !this.props.data) {
            return "—"; // never flash a zero we have not counted yet
        }
        const value = this.state.shown;
        let text;
        if (this.isMeasure) {
            text = Math.abs(value) >= 10000 ? humanNumber(value, { decimals: 1 }) : formatFloat(value);
        } else {
            const rounded = Math.round(value);
            text = rounded >= 100000 ? humanNumber(rounded) : formatInteger(rounded);
        }
        return this.props.def.symbol ? `${this.props.def.symbol} ${text}` : text;
    }

    /** Under the value: the record count backing an aggregated measure. */
    get subLabel() {
        if (!this.props.data || this.props.loading) {
            return "";
        }
        if (this.isMeasure) {
            const count = this.props.data.count || 0;
            return count === 1 ? _t("1 record") : _t("%s records", formatInteger(count));
        }
        return "";
    }

    get color() {
        return resolveTileColor(this.props.def);
    }

    get style() {
        // An alerting tile borrows the danger colour outright. It has to happen
        // here rather than in CSS: this inline custom property is what the whole
        // tile is painted from, so a class could never override it.
        const parts = [`--dft-color: ${this.isAlerting ? "var(--danger)" : this.color}`];
        const width = this.resize.dragging ? this.resize.width : this.props.def.width || 0;
        if (width) {
            parts.push(`--dft-width: ${width}px`);
        }
        return `${parts.join("; ")};`;
    }

    /**
     * How much room this tile has sideways.
     *
     * Narrow: the label gets one line and an ellipsis, and the value steps down
     * a size rather than being clipped. Wide: the label is allowed to wrap onto
     * a second line instead of being cut, because there is room for it.
     */
    get widthClass() {
        const width = this.resize.dragging ? this.resize.width : this.props.def.width || 0;
        if (!width) {
            return "";
        }
        if (width < 156) {
            return "o_dft_tile_narrow";
        }
        if (width >= 260) {
            return "o_dft_tile_wide";
        }
        return "";
    }

    get iconClass() {
        const icon = (this.props.def.icon || "").trim();
        if (!icon) {
            return "fa fa-circle-o";
        }
        return icon.startsWith("fa-") ? `fa ${icon}` : icon;
    }

    /** "goal" swaps the sparkline for a progress ring towards `target`. */
    get mode() {
        return this.props.def.target > 0 ? "goal" : "trend";
    }

    get share() {
        const total = this.props.total || 0;
        const count = (this.props.data && this.props.data.count) || 0;
        if (!total) {
            return 0;
        }
        return Math.max(0, Math.min(100, (count / total) * 100));
    }

    get goal() {
        const target = this.props.def.target || 0;
        const value = (this.props.data && this.props.data.value) || 0;
        const ratio = target ? value / target : 0;
        const percent = Math.max(0, Math.min(1, ratio));
        const radius = 15;
        const circumference = 2 * Math.PI * radius;
        return {
            percent: Math.round(ratio * 100),
            radius,
            circumference,
            offset: circumference * (1 - percent),
            reached: ratio >= 1,
        };
    }

    get delta() {
        const delta = this.props.data && this.props.data.delta;
        if (delta === null || delta === undefined || !isFinite(delta) || Math.round(delta) === 0) {
            return null;
        }
        const rounded = Math.abs(delta) >= 100 ? Math.round(delta) : Math.round(delta * 10) / 10;
        return {
            up: delta > 0,
            // Deliberately colour-neutral: "more late orders" is not good news,
            // and only the reader knows which way is up for their tile.
            text: `${delta > 0 ? "+" : "−"}${Math.abs(rounded)}%`,
        };
    }

    /**
     * Sparkline of the last periods, smoothed through the midpoints so a short
     * series still reads as a curve rather than a zig-zag.
     *
     * The SVG is stretched with preserveAspectRatio="none"; strokes keep their
     * width via vector-effect, and the "latest point" dot is plain HTML so it
     * stays round.
     */
    get spark() {
        const points = (this.props.data && this.props.data.trend) || [];
        if (this.props.compact || this.mode === "goal" || points.length < 2) {
            return null;
        }
        const max = Math.max(...points);
        const min = Math.min(...points);
        const span = max - min || Math.abs(max) || 1;
        const stepX = SPARK_W / (points.length - 1);
        const coords = points.map((value, index) => [
            index * stepX,
            SPARK_H - 3 - ((value - min) / span) * (SPARK_H - 6),
        ]);

        let line = `M${coords[0][0]},${coords[0][1].toFixed(2)}`;
        for (let i = 1; i < coords.length; i++) {
            const [prevX, prevY] = coords[i - 1];
            const [x, y] = coords[i];
            const midX = (prevX + x) / 2;
            line += ` Q${prevX.toFixed(2)},${prevY.toFixed(2)} ${midX.toFixed(2)},${(
                (prevY + y) /
                2
            ).toFixed(2)}`;
            if (i === coords.length - 1) {
                line += ` T${x.toFixed(2)},${y.toFixed(2)}`;
            }
        }
        const last = coords[coords.length - 1];
        return {
            line,
            area: `${line} L${SPARK_W},${SPARK_H} L0,${SPARK_H} Z`,
            lastX: (last[0] / SPARK_W) * 100,
            lastY: ((SPARK_H - last[1]) / SPARK_H) * 100,
        };
    }

    /** A tile past its threshold stops being decorative and starts warning. */
    get isAlerting() {
        return Boolean(this.props.data && this.props.data.alert);
    }

    get alertText() {
        if (!this.isAlerting) {
            return "";
        }
        const def = this.props.def;
        if (def.alert_message) {
            return def.alert_message;
        }
        const direction = def.alert_operator === "lt" ? _t("below") : _t("above");
        return _t("%(name)s is %(direction)s %(threshold)s", {
            name: def.name,
            direction,
            threshold: formatFloat(def.alert_value || 0),
        });
    }

    get tooltip() {
        const parts = [this.props.def.tooltip || this.props.def.name];
        if (this.isAlerting) {
            parts.push(`⚠ ${this.alertText}`);
        }
        if (this.props.def.personal) {
            parts.push(_t("Private tile - only you see it"));
        }
        if (this.props.shortcut) {
            parts.push(_t("Alt + %s", this.props.shortcut));
        }
        return parts.join("\n");
    }

    // ------------------------------------------------------------------
    // Interaction
    // ------------------------------------------------------------------
    onBreakdownClick(ev) {
        ev.stopPropagation();
        if (this.props.onBreakdown) {
            this.props.onBreakdown(ev.currentTarget);
        }
    }

    // ------------------------------------------------------------------
    // Resizing - drag the right edge
    // ------------------------------------------------------------------
    /**
     * Grab the edge and the tile follows the pointer; let go and the width is
     * saved on the tile itself, so everybody who sees that tile gets it.
     *
     * Pointer capture is deliberately *not* used: the listeners live on the
     * document so the drag survives the pointer leaving the (narrow) handle,
     * which is exactly what happens when you drag fast.
     */
    onResizeStart(ev) {
        if (!this.props.onResize || ev.button !== 0) {
            return;
        }
        ev.preventDefault();
        ev.stopPropagation();
        const tile = ev.currentTarget.closest(".o_dft_tile");
        const startX = ev.clientX;
        const startWidth = tile ? tile.getBoundingClientRect().width : MIN_TILE_WIDTH;

        this.resize.dragging = true;
        this.resize.width = Math.round(startWidth);

        const onMove = (moveEv) => {
            const width = Math.round(startWidth + moveEv.clientX - startX);
            this.resize.width = Math.max(MIN_TILE_WIDTH, Math.min(MAX_TILE_WIDTH, width));
        };
        const onUp = () => {
            this.stopResize?.();
            const width = this.resize.width;
            this.resize.dragging = false;
            // The tile keeps the dragged width until the definition comes back
            // updated, so it never snaps back for a frame.
            this.props.onResize(width);
        };
        this.stopResize = () => {
            document.removeEventListener("pointermove", onMove);
            document.removeEventListener("pointerup", onUp);
            document.body.classList.remove("o_dft_resizing");
            this.stopResize = null;
        };
        document.addEventListener("pointermove", onMove);
        document.addEventListener("pointerup", onUp);
        document.body.classList.add("o_dft_resizing");
    }

    /** Double-click the edge: back to the standard width. */
    onResizeReset(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        if (this.props.onResize) {
            this.props.onResize(0);
        }
    }

    onClick(ev) {
        if (this.props.onSelect) {
            this.props.onSelect(ev);
        }
    }

    onKeydown(ev) {
        if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault();
            this.onClick(ev);
        }
    }

    onEditClick(ev) {
        ev.stopPropagation();
        if (this.props.onEdit) {
            this.props.onEdit();
        }
    }

    onDeleteClick(ev) {
        ev.stopPropagation();
        if (this.props.onDelete) {
            this.props.onDelete();
        }
    }
}
