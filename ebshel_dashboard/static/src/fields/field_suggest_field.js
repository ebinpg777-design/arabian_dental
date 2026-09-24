/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { useRecordObserver } from "@web/model/relational_model/utils";


/**
 * The fields worth building this card on, as one click each.
 *
 * A model has two hundred stored fields and four of them make a card. The
 * server ranks the ones people actually group and measure by (see
 * `dashboard.item.suggest_fields`); this offers them as chips above the
 * ordinary dropdown, which is still there for everything else.
 *
 * `kind` says which list to show - "split", "measure" or "date" - and
 * `target` the many2one to fill in when a chip is clicked.
 */
export class FieldSuggestField extends Component {
    static template = "ebshel_dashboard.FieldSuggestField";
    // A view widget, not a field widget: the model is already on the form
    // once, and a form may not carry the same field twice.
    static props = {
        record: Object,
        readonly: { type: Boolean, optional: true },
        kind: { type: String, optional: true },
        target: { type: String, optional: true },
    };

    setup() {
        this.orm = useService("orm");
        this.state = useState({ suggestions: [], modelId: null, loading: false });
        // The record is one stable object, so a prop change never fires: the
        // observer is what tells a widget that a field it cares about moved.
        useRecordObserver((record) => this.load(record));
    }

    get kind() {
        return this.props.kind || "split";
    }

    get target() {
        return this.props.target || "group_by_field_id";
    }

    /** The model the card is on, whatever shape the record hands it in. */
    modelId(record) {
        const value = record.data.model_id;
        if (!value) {
            return null;
        }
        return Array.isArray(value) ? value[0] : value.id || value;
    }

    async load(record) {
        const modelId = this.modelId(record);
        if (modelId === this.state.modelId) {
            return;
        }
        this.state.modelId = modelId;
        this.state.suggestions = [];
        if (!modelId) {
            return;
        }
        this.state.loading = true;
        try {
            const found = await this.orm.silent.call("dashboard.item", "suggest_fields", [modelId]);
            this.state.suggestions = found || {};
        } catch {
            this.state.suggestions = {};
        } finally {
            this.state.loading = false;
        }
    }

    get chips() {
        const found = this.state.suggestions || {};
        return found[this.kind] || [];
    }

    get currentId() {
        const value = this.props.record.data[this.target];
        if (!value) {
            return null;
        }
        return Array.isArray(value) ? value[0] : value.id || value;
    }

    get label() {
        return {
            split: _t("Often split by"),
            measure: _t("Often measured"),
            date: _t("Dates on this model"),
        }[this.kind];
    }

    /** A word for the field type, so a chip says what it will do. */
    typeLabel(type) {
        return {
            many2one: _t("link"), selection: _t("choice"), boolean: _t("yes/no"),
            char: _t("text"), integer: _t("number"), float: _t("number"),
            monetary: _t("money"), date: _t("date"), datetime: _t("date"),
        }[type] || type;
    }

    pick(chip) {
        if (this.props.readonly) {
            return;
        }
        // Same shape the many2one itself writes, so the form reacts as if the
        // reader had chosen it in the dropdown - onchanges included.
        this.props.record.update({ [this.target]: { id: chip.id, display_name: chip.label } });
    }
}

export const fieldSuggestWidget = {
    component: FieldSuggestField,
    extractProps: ({ attrs }) => ({ kind: attrs.kind, target: attrs.target }),
    fieldDependencies: [{ name: "model_id", type: "many2one" }],
};

registry.category("view_widgets").add("dashboard_field_suggest", fieldSuggestWidget);
