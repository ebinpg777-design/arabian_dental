import { Component, onPatched, signal, t, usePlugin, useProps } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { NotificationPlugin } from "@web/core/notifications/notification_plugin";
import { renderCaiMarkdown } from "../../core/cai_markdown";
import { CaiActionDialogCard } from "./cai_action_card";

/** Scrollable list of the visible messages of a conversation. */
export class CaiMessageList extends Component {
    static template = "ebshel_ai_suite.CaiMessageList";
    static components = { CaiActionDialogCard };
    props = useProps({
        exchanges: t.array(),
        busy: t.boolean(),
        statusText: t.or([t.string(), t.literal(null)]).optional(),
        onDecide: t.function(),
        onRetry: t.function(),
        onNavigate: t.function(),
        onSuggestion: t.function(),
        onPost: t.function().optional(),
        suggestions: t.array().optional(),
    });
    scrollRef = signal.ref();

    setup() {
        this.notification = usePlugin(NotificationPlugin);
        onPatched(() => {
            const el = this.scrollRef();
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
                executed: "check_circle",
                awaiting_confirmation: "back_hand",
                rejected: "block",
                failed: "warning",
            }[ex.tool_status] || "settings"
        );
    }

    toolIconClass(ex) {
        return (
            {
                executed: "text-success",
                awaiting_confirmation: "text-warning",
                rejected: "text-danger",
                failed: "text-danger",
            }[ex.tool_status] || ""
        );
    }
}
