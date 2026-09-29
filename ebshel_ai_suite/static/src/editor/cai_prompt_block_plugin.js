import { DYNAMIC_PLACEHOLDER_PLUGINS } from "@html_editor/backend/plugin_sets";
import { Plugin } from "@html_editor/plugin";
import { _t } from "@web/core/l10n/translation";

/**
 * Powerbox command "/AI Prompt" for templated HTML (email templates): inserts a
 * block whose text is an instruction. The server replaces the block with
 * generated text for each record when the template is rendered.
 */
export class CommunityAIPromptBlockPlugin extends Plugin {
    static id = "communityAiPromptBlock";
    static dependencies = ["dom", "history", "selection"];
    resources = {
        user_commands: [
            {
                id: "insertCommunityAiPromptBlock",
                title: _t("AI Prompt"),
                description: _t("Text written by AI for each recipient"),
                icon: "fa-magic",
                run: () => this.insertBlock(),
            },
        ],
        powerbox_items: {
            categoryId: "marketing_tools",
            commandId: "insertCommunityAiPromptBlock",
        },
    };

    insertBlock() {
        const block = this.document.createElement("div");
        block.className = "o_cai_prompt";
        block.textContent = _t("Describe what the AI should write, e.g. a friendly reminder about the due date.");
        this.dependencies.dom.insert(block);
        this.dependencies.history.addStep();
    }
}

// Enabled wherever dynamic placeholders are (templates), not in the chatter composer.
DYNAMIC_PLACEHOLDER_PLUGINS.push(CommunityAIPromptBlockPlugin);
