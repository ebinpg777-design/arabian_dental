/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { AUTO_COLOR_CYCLE, TILE_COLORS } from "../../core/tile_colors";

/**
 * "Generate a tile set…": pick one of the model's status-like fields and get a
 * complete, colour-coded, icon-matched ribbon in one click.
 *
 * The field candidates (and the real creation) come from the server; this
 * dialog only previews what each choice would produce.
 */
export class TileGeneratorDialog extends Component {
    static template = "ebshel_dynamic_filter.TileGeneratorDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        resModel: String,
        fields: Array, // [{name, string, values: [{value, label}]}]
        hasTiles: { type: Boolean, optional: true },
        actionName: { type: String, optional: true }, // the menu we were opened from
        rowCount: { type: Number, optional: true },  // rows the ribbon already has
        maxRows: { type: Number, optional: true },
        onGenerate: Function,
    };
    static defaultProps = { rowCount: 1, maxRows: 6 };

    setup() {
        this.state = useState({
            selected: this.props.fields.length ? this.props.fields[0].name : "",
            replace: false,
            generating: false,
            // One model is often reached through several menus; a set generated
            // from one of them usually belongs to that one only.
            thisActionOnly: Boolean(this.props.actionName),
            // A generated set is a group of its own, so it defaults to a row of
            // its own - the next one - rather than crowding what is already
            // there. Only when the ribbon is still empty does it start at 1.
            row: this.props.rowCount > 1 || this.props.hasTiles
                ? Math.min(this.props.rowCount + 1, this.props.maxRows)
                : 1,
        });
    }

    get selectedField() {
        return this.props.fields.find((field) => field.name === this.state.selected);
    }

    /** Same rotation the server uses, so the preview matches the result. */
    chipStyle(index) {
        const color = TILE_COLORS[AUTO_COLOR_CYCLE[index % AUTO_COLOR_CYCLE.length]];
        return `--dft-color: ${color};`;
    }

    select(fieldName) {
        this.state.selected = fieldName;
    }

    /** Rows to choose from: the ones that exist, plus one to start a new one. */
    get rowChoices() {
        const highest = Math.min(this.props.rowCount + 1, this.props.maxRows);
        return Array.from({ length: highest }, (_, index) => index + 1);
    }

    rowLabel(row) {
        return row > this.props.rowCount ? _t("New row %s", row) : _t("Row %s", row);
    }

    pickRow(row) {
        this.state.row = row;
    }

    async confirm() {
        if (!this.state.selected || this.state.generating) {
            return;
        }
        this.state.generating = true;
        try {
            await this.props.onGenerate(
                this.state.selected, this.state.replace, this.state.thisActionOnly,
                this.state.row);
            this.props.close();
        } finally {
            this.state.generating = false;
        }
    }
}
