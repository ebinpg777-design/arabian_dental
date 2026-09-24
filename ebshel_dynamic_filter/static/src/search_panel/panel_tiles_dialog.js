/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { AUTO_COLOR_CYCLE, TILE_COLORS } from "../core/tile_colors";

/**
 * "Turn this section into filter tiles": the values of one search panel
 * section, offered as chips - the busiest ones ticked - and one tile per
 * ticked value when confirmed. The server does the creating; this dialog only
 * lets the user pick, and says who will see the result.
 */
export class PanelTilesDialog extends Component {
    static template = "ebshel_dynamic_filter.PanelTilesDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        sectionName: String,
        values: Array, // [{id, label, count}] - count is null without counters
        maxTiles: Number,
        canManage: { type: Boolean, optional: true },
        actionName: { type: String, optional: true },
        onConfirm: Function, // (picked, personal, thisActionOnly) => Promise
    };

    setup() {
        const initial = this.props.values.slice(0, Math.min(6, this.props.maxTiles));
        this.state = useState({
            picked: initial.map((value) => value.id),
            // A manager publishes by default, like the tile set generator;
            // everybody else only ever builds private tiles.
            personal: !this.props.canManage,
            thisActionOnly: Boolean(this.props.actionName),
            saving: false,
        });
    }

    isPicked(value) {
        return this.state.picked.includes(value.id);
    }

    get isFull() {
        return this.state.picked.length >= this.props.maxTiles;
    }

    toggle(value) {
        if (this.isPicked(value)) {
            this.state.picked = this.state.picked.filter((id) => id !== value.id);
        } else if (!this.isFull) {
            this.state.picked = [...this.state.picked, value.id];
        }
    }

    pickTop() {
        this.state.picked = this.props.values.slice(0, this.props.maxTiles).map((v) => v.id);
    }

    pickNone() {
        this.state.picked = [];
    }

    /** Colour a chip the way its tile will be coloured. */
    chipStyle(value) {
        const index = this.state.picked.indexOf(value.id);
        if (index < 0) {
            return "";
        }
        const color = TILE_COLORS[AUTO_COLOR_CYCLE[index % AUTO_COLOR_CYCLE.length]];
        return `--dft-color: ${color};`;
    }

    async confirm() {
        if (!this.state.picked.length || this.state.saving) {
            return;
        }
        this.state.saving = true;
        // In the order they were picked: that is the order the tiles get.
        const byId = new Map(this.props.values.map((value) => [value.id, value]));
        const picked = this.state.picked.map((id) => byId.get(id)).filter(Boolean);
        try {
            await this.props.onConfirm(picked, this.state.personal, this.state.thisActionOnly);
            this.props.close();
        } finally {
            this.state.saving = false;
        }
    }
}
