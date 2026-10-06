import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const MIN_LENGTH = 3;

registry.category("command_categories").add("ebshel_ai", { name: _t("AI") }, { sequence: 90 });

/**
 * Adds two entries to the command palette (Ctrl+K) when the user typed a
 * sentence: ask the assistant, or turn the sentence into a search.
 */
registry.category("command_provider").add("ebshel_ai_suite", {
    // called inside the palette's scope: hooks must run before the first await
    async provide(options) {
        const bridge = useService("communityAiBridge");
        const text = (options.searchValue || "").trim();
        if (text.length < MIN_LENGTH) {
            return [];
        }
        const config = await bridge.bootstrap();
        if (!config?.enabled) {
            return [];
        }
        return [
            {
                name: _t("Ask Assistant: %s", text),
                category: "ebshel_ai",
                action: () => bridge.open({ prompt: text }),
            },
            {
                name: _t("Find with AI: %s", text),
                category: "ebshel_ai",
                action: () => bridge.findWithAi(text),
            },
        ];
    },
});
