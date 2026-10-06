import { DYNAMIC_FIELD_PLUGINS } from "@html_editor/backend/dynamic_field/dynamic_field_plugin";
import { Plugin } from "@html_editor/plugin";
import { withSequence } from "@html_editor/utils/resource";
import { _t } from "@web/core/l10n/translation";

const isPromptBlock = (node) => node?.nodeType === Node.ELEMENT_NODE && node.classList.contains("o_cai_prompt");

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
                icon: "wand_stars",
                run: () => this.insertBlock(),
            },
        ],
        // 20.0 has no "marketing_tools" category any more: an unknown category id breaks the
        // whole powerbox of the editor, so the block brings its own
        powerbox_categories: withSequence(2, { id: "cai_tools", name: _t("AI") }),
        powerbox_items: {
            categoryId: "cai_tools",
            commandId: "insertCommunityAiPromptBlock",
        },
        // A plain div is a paragraph candidate: dom.insert would unwrap it into the
        // current paragraph and the o_cai_prompt class (what the server looks for) was lost.
        is_valid_for_base_container_predicates: (node) => {
            if (isPromptBlock(node)) {
                return false;
            }
        },
        is_node_splittable_predicates: (node) => {
            if (isPromptBlock(node)) {
                return false;
            }
        },
    };

    insertBlock() {
        const block = this.document.createElement("div");
        block.className = "o_cai_prompt";
        block.textContent = _t("Describe what the AI should write, e.g. a friendly reminder about the due date.");
        this.dependencies.dom.insert(block);
        this.dependencies.history.commit();
    }
}

// Enabled wherever dynamic fields are (templates), not in the chatter composer.
DYNAMIC_FIELD_PLUGINS.push(CommunityAIPromptBlockPlugin);
