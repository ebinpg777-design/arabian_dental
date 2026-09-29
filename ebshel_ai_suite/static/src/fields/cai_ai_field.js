import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

const fieldRegistry = registry.category("fields");

/**
 * ``widget="cai_ai_field"``: the field's usual widget plus an AI button that
 * regenerates the value with the field's AI rule (the record is saved first).
 */
export class CommunityAIFieldRefresh extends Component {
    static template = "ebshel_ai_suite.CommunityAIFieldRefresh";
    static props = {
        ...standardFieldProps,
        caiInnerType: String,
        caiInnerProps: { type: Object, optional: true },
    };

    setup() {
        this.bridge = useService("communityAiBridge");
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ busy: false, enabled: false });
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

    async regenerate() {
        const record = this.props.record;
        this.state.busy = true;
        try {
            if (record.isNew || record.dirty) {
                const saved = await record.save();
                if (!saved) {
                    return;
                }
            }
            const result = await this.orm.call("community.ai.field.rule", "cai_generate_for_field", [
                record.resModel,
                record.resId,
                this.props.name,
            ]);
            await record.load();
            if (!result.written) {
                this.notification.add(result.message || _t("The AI did not propose a value."), {
                    type: "warning",
                });
            }
        } catch (error) {
            this.notification.add(error?.data?.message || _t("The AI request failed."), { type: "danger" });
        } finally {
            this.state.busy = false;
        }
    }
}

export const communityAIFieldRefresh = {
    component: CommunityAIFieldRefresh,
    displayName: _t("AI-generated value"),
    supportedTypes: ["char", "text", "html", "integer", "float", "monetary", "date", "datetime", "boolean",
        "selection", "many2one"],
    extractProps(fieldInfo, dynamicInfo) {
        const inner = fieldRegistry.get(fieldInfo.type);
        return {
            caiInnerType: fieldInfo.type,
            caiInnerProps: inner.extractProps ? inner.extractProps(fieldInfo, dynamicInfo) : {},
        };
    },
    isEmpty(record, fieldName) {
        const inner = fieldRegistry.get(record.fields[fieldName].type);
        return inner.isEmpty ? inner.isEmpty(record, fieldName) : !record.data[fieldName];
    },
};

fieldRegistry.add("cai_ai_field", communityAIFieldRefresh);
