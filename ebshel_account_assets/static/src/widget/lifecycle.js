/** @odoo-module **/

import { Component, useProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { finDate } from "@ebshel_account_reports/fin_date";

/**
 * The life of an asset on one line, and its value on one curve.
 *
 * Reads the JSON the server computes: the events (created, confirmed, paused,
 * modified, disposed…) and the book-value curve, one point per depreciation
 * line, posted ones solid and the future dotted.
 */
export class AssetLifecycle extends Component {
    static template = "ebshel_account_assets.Lifecycle";
    props = useProps(standardFieldProps);

    /** A date the company's way (Settings > Accounting > Dynamic Reports > Date format). */
    fmtDate(value) {
        return finDate(value);
    }

    get data() {
        return this.props.record.data[this.props.name] || { events: [], curve: [] };
    }

    get events() {
        return this.data.events || [];
    }

    get chart() {
        const curve = this.data.curve || [];
        const W = 640, H = 160, PL = 8, PR = 8, PT = 12, PB = 24;
        if (curve.length < 2) {
            return null;
        }
        const top = Math.max(this.data.purchase_value || 0, ...curve.map((p) => p.book), 1);
        const xs = curve.map((_p, i) => PL + (i * (W - PL - PR)) / (curve.length - 1));
        const y = (v) => PT + (H - PT - PB) * (1 - v / top);
        const points = curve.map((p, i) => ({ x: xs[i], y: y(p.book), posted: p.posted, label: p.date, book: p.book }));
        const lastPosted = points.map((p) => p.posted).lastIndexOf(true);
        const solid = points.slice(0, Math.max(lastPosted + 1, 1)).map((p) => `${p.x},${p.y}`).join(" ");
        const dotted = points.slice(Math.max(lastPosted, 0)).map((p) => `${p.x},${p.y}`).join(" ");
        const salvageY = this.data.salvage ? y(this.data.salvage) : null;
        const ticks = [0, Math.floor(curve.length / 2), curve.length - 1].map((i) => ({ x: xs[i], label: curve[i].date }));
        return { W, H, solid, dotted, points, salvageY, ticks, topLabel: this.money(top) };
    }

    money(v) {
        return `${this.data.currency || ""} ${Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
    }

    icon(kind) {
        return {
            created: "add", running: "play_arrow", paused: "pause", resumed: "play_arrow", modified: "edit",
            revalued: "north_east", impaired: "south_east", posted: "check", disposed: "logout",
            closed: "sports_score", reset: "undo", cancelled: "block", skipped: "fast_forward", note: "mode_comment",
        }[kind] || "circle";
    }
}

registry.category("fields").add("ebshel_asset_lifecycle", { component: AssetLifecycle, supportedTypes: ["json"] });
