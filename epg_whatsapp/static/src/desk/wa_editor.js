/** @odoo-module **/

import { Component, onWillStart, onWillUpdateProps, useRef, useState } from "@odoo/owl";
import { useEmojiPicker } from "@web/core/emoji_picker/emoji_picker";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

/**
 * A WhatsApp message editor: the text, with what WhatsApp lets you do to it.
 *
 * Bold, italic and strike-through in WhatsApp's own marks, an emoji picker, and
 * the record's fields dropped in as {{placeholders}} - so a campaign is written
 * the way a WhatsApp message is, not the way a form field is. (client, 2026-09-17)
 */
export class WaEditorField extends Component {
    static template = "epg_whatsapp.WaEditor";
    static props = {
        ...standardFieldProps,
        model: { type: String, optional: true },
        modelField: { type: String, optional: true },
        placeholder: { type: String, optional: true },
    };

    setup() {
        this.orm = useService("orm");
        this.textarea = useRef("textarea");
        this.state = useState({ fields: [], showFields: false, query: "" });
        useEmojiPicker(
            useRef("emoji"),
            { onSelect: (codepoints) => this.insert(codepoints) },
            { position: "bottom" }
        );
        onWillStart(() => this.loadFields());
        // A template's model can change while the form is open: the fields follow.
        onWillUpdateProps((next) => {
            if (this.modelOf(next) !== this.modelOf(this.props)) {
                this.loadFields(next);
            }
        });
    }

    modelOf(props) {
        return props.modelField ? props.record.data[props.modelField] : props.model;
    }

    async loadFields(props = this.props) {
        const model = this.modelOf(props);
        this.state.fields = model
            ? await this.orm.call("epg.whatsapp.template", "available_paths", [model])
            : [];
    }

    get value() {
        return this.props.record.data[this.props.name] || "";
    }

    get shownFields() {
        const q = this.state.query.trim().toLowerCase();
        const rows = q
            ? this.state.fields.filter(
                  (f) => f.label.toLowerCase().includes(q) || f.path.toLowerCase().includes(q)
              )
            : this.state.fields;
        return rows.slice(0, 30);
    }

    onInput(ev) {
        this.props.record.update({ [this.props.name]: ev.target.value });
    }

    /** Put `text` where the cursor is, and leave the cursor after it. */
    insert(text) {
        const el = this.textarea.el;
        if (!el || this.props.readonly) {
            return;
        }
        const start = el.selectionStart ?? el.value.length;
        const end = el.selectionEnd ?? start;
        const next = el.value.slice(0, start) + text + el.value.slice(end);
        this.commit(el, next, start + text.length, start + text.length);
    }

    /** Wrap the selection in a WhatsApp mark: *bold*, _italic_, ~strike~. */
    wrap(mark) {
        const el = this.textarea.el;
        if (!el || this.props.readonly) {
            return;
        }
        const start = el.selectionStart ?? el.value.length;
        const end = el.selectionEnd ?? start;
        const selected = el.value.slice(start, end) || "text";
        const next = el.value.slice(0, start) + mark + selected + mark + el.value.slice(end);
        this.commit(el, next, start + mark.length, start + mark.length + selected.length);
    }

    commit(el, next, selStart, selEnd) {
        this.props.record.update({ [this.props.name]: next });
        el.value = next;
        el.focus();
        el.setSelectionRange(selStart, selEnd);
    }

    insertField(path) {
        this.insert(`{{${path}}}`);
        this.state.showFields = false;
        this.state.query = "";
    }
}

export const waEditorField = {
    component: WaEditorField,
    supportedTypes: ["text"],
    extractProps: ({ attrs, options }) => ({
        model: options.model,
        modelField: options.model_field,
        placeholder: attrs.placeholder,
    }),
};

registry.category("fields").add("wa_editor", waEditorField);
