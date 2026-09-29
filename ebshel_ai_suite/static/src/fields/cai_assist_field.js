import { Component, markup, useState } from "@odoo/owl";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { CommunityAIVoiceDialog } from "../components/voice/cai_voice_dialog";

const fieldRegistry = registry.category("fields");

/**
 * ``widget="cai_assist"``: wraps the default widget of a char, text or html
 * field and adds an AI menu (improve, shorten, translate, ...). The proposal
 * replaces the value in the form only; the user still decides to save.
 */
export class CommunityAIAssistField extends Component {
    static template = "ebshel_ai_suite.CommunityAIAssistField";
    static components = { Dropdown, DropdownItem };
    static props = {
        ...standardFieldProps,
        caiInnerType: String,
        caiInnerProps: { type: Object, optional: true },
    };

    setup() {
        this.bridge = useService("communityAiBridge");
        this.notification = useService("notification");
        this.dialog = useService("dialog");
        this.state = useState({
            busy: false, operations: [], languages: [], shortcuts: [], previous: null, enabled: false,
        });
        this.bridge.bootstrap().then((config) => {
            this.state.enabled = Boolean(config?.enabled);
        });
    }

    get innerComponent() {
        return fieldRegistry.get(this.props.caiInnerType).component;
    }

    get innerProps() {
        const { caiInnerType, caiInnerProps, ...standard } = this.props;
        return { ...standard, ...(caiInnerProps || {}) };
    }

    get isHtml() {
        return this.props.caiInnerType === "html";
    }

    async loadOptions() {
        if (!this.state.operations.length) {
            const options = await this.bridge.writingOptions(this.props.record.resModel);
            this.state.operations = options.operations.filter(
                (op) => !["translate", "tone", "custom", "meeting_summary"].includes(op.key)
            );
            this.state.languages = options.languages;
            this.state.shortcuts = options.shortcuts || [];
        }
    }

    async run(operation, extra = {}) {
        const record = this.props.record;
        const current = record.data[this.props.name];
        const text = current ? current.toString() : "";
        this.state.busy = true;
        try {
            const result = await this.bridge.transformText(operation, text, {
                html: this.isHtml,
                res_model: record.resModel,
                res_id: record.resId || false,
                ...extra,
            });
            this.state.previous = text;
            await record.update({ [this.props.name]: this.isHtml ? markup(result.text) : result.text });
        } catch (error) {
            this.notification.add(error?.data?.message || _t("The AI request failed."), { type: "danger" });
        } finally {
            this.state.busy = false;
        }
    }

    openVoice() {
        this.dialog.add(CommunityAIVoiceDialog, {
            onInsert: ({ transcript, summary }) => this.insertTranscript(transcript, summary),
        });
    }

    async insertTranscript(transcript, summary) {
        const record = this.props.record;
        const current = record.data[this.props.name] ? record.data[this.props.name].toString() : "";
        this.state.previous = current;
        let value;
        if (this.isHtml) {
            const escape = (text) => text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
            const block = (title, text) =>
                text ? `<h4>${escape(title)}</h4><p>${escape(text).replace(/\n/g, "<br/>")}</p>` : "";
            value = markup(current + block(_t("Summary"), summary) + block(_t("Transcript"), transcript));
        } else {
            value = [current, summary, transcript].filter(Boolean).join("\n\n");
        }
        await record.update({ [this.props.name]: value });
    }

    async undo() {
        if (this.state.previous !== null) {
            const value = this.state.previous;
            this.state.previous = null;
            await this.props.record.update({ [this.props.name]: this.isHtml ? markup(value) : value });
        }
    }
}

export const communityAIAssistField = {
    component: CommunityAIAssistField,
    displayName: _t("Text with AI assistance"),
    supportedTypes: ["char", "text", "html"],
    extractProps(fieldInfo, dynamicInfo) {
        const inner = fieldRegistry.get(fieldInfo.type);
        return {
            caiInnerType: fieldInfo.type,
            caiInnerProps: inner.extractProps ? inner.extractProps(fieldInfo, dynamicInfo) : {},
        };
    },
};

fieldRegistry.add("cai_assist", communityAIAssistField);
