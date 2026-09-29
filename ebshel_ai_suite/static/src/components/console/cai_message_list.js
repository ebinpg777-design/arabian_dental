import { Component, onPatched, useRef } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { renderCaiMarkdown } from "../../core/cai_markdown";
import { CaiActionDialogCard } from "./cai_action_card";

/** Scrollable list of the visible messages of a conversation. */
export class CaiMessageList extends Component {
    static template = "ebshel_ai_suite.CaiMessageList";
    static components = { CaiActionDialogCard };
    static props = {
        exchanges: Array,
        busy: Boolean,
        statusText: { type: [String, { value: null }], optional: true },
        onDecide: Function,
        onRetry: Function,
        onNavigate: Function,
        onSuggestion: Function,
        onPost: { type: Function, optional: true },
        suggestions: { type: Array, optional: true },
    };

    setup() {
        this.notification = useService("notification");
        this.scrollRef = useRef("scroll");
        onPatched(() => {
            const el = this.scrollRef.el;
            if (el) {
                el.scrollTop = el.scrollHeight;
            }
        });
    }

    get visibleExchanges() {
        return this.props.exchanges.filter((ex) => {
            if (ex.speaker === "event") {
                return false;
            }
            if (ex.speaker === "assistant" && ex.intermediate && !ex.body) {
                return false;
            }
            return true;
        });
    }

    get lastFailedId() {
        const failed = this.props.exchanges.filter((ex) => ex.state === "failed");
        const last = this.props.exchanges.at(-1);
        return failed.length && last && last.state === "failed" ? last.id : null;
    }

    async copy(ex) {
        try {
            await navigator.clipboard.writeText(ex.body);
            this.notification.add(_t("Copied to the clipboard."), { type: "success" });
        } catch {
            this.notification.add(_t("The browser refused access to the clipboard."), { type: "warning" });
        }
    }

    render(body) {
        return renderCaiMarkdown(body);
    }

    toolIcon(ex) {
        return (
            {
                executed: "fa-check-circle text-success",
                awaiting_confirmation: "fa-hand-paper-o text-warning",
                rejected: "fa-ban text-danger",
                failed: "fa-exclamation-triangle text-danger",
            }[ex.tool_status] || "fa-cog"
        );
    }
}
