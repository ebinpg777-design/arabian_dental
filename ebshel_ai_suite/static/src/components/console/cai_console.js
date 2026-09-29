import { Component, onMounted, onWillStart, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { CaiPromptInput } from "./cai_prompt_input";
import { CaiContextBadge } from "./cai_context_badge";
import { CaiMessageList } from "./cai_message_list";

/**
 * The AI console: assistant picker, conversation history, messages,
 * confirmations and prompt input. Used as a side panel ("overlay") and as a
 * full page ("page", from the Ebshel AI menu).
 */
export class CommunityAIConsole extends Component {
    static template = "ebshel_ai_suite.CommunityAIConsole";
    static components = { CaiPromptInput, CaiContextBadge, CaiMessageList };
    static props = {
        mode: { type: String, optional: true },
        onClose: { type: Function, optional: true },
        initialAssistantId: { type: [Number, Boolean], optional: true },
    };
    static defaultProps = { mode: "overlay" };

    setup() {
        this.bridge = useService("communityAiBridge");
        this.state = useState({
            ready: false,
            enabled: true,
            assistants: [],
            assistantId: false,
            session: null,
            exchanges: [],
            busy: false,
            error: null,
            toolActivity: null,
            statusText: null,
            recent: [],
            showHistory: this.props.mode === "page",
            useContext: true,
            context: null,
            canConfigure: false,
            preset: null,
            stagedFiles: [],
            uploading: false,
        });
        onWillStart(async () => {
            const config = await this.bridge.bootstrap();
            this.state.enabled = Boolean(config?.enabled);
            if (!this.state.enabled) {
                this.state.ready = true;
                return;
            }
            this.state.assistants = config.assistants;
            const requested = this.props.initialAssistantId;
            this.state.assistantId = config.assistants.some((a) => a.id === requested)
                ? requested
                : config.default_assistant_id;
            this.state.recent = config.recent || [];
            this.state.canConfigure = config.can_configure;
            this.state.context = this.props.mode === "overlay" ? this.bridge.currentContext() : null;
            await this.loadPreset(!requested);
            this.state.ready = true;
        });
        onMounted(() => {
            const prompt = this.bridge.state.pendingPrompt;
            if (prompt) {
                this.bridge.state.pendingPrompt = null;
                this.send(prompt);
            }
        });
    }

    // ------------------------------------------------------------------
    // Getters
    // ------------------------------------------------------------------
    get currentAssistant() {
        return this.state.assistants.find((a) => a.id === this.state.assistantId) || null;
    }

    get recordContext() {
        return this.state.session ? this.state.session.context : this.state.context;
    }

    get contextAvailable() {
        const ctx = this.recordContext;
        return Boolean(ctx && (ctx.resId || ctx.res_id) && this.currentAssistant?.uses_context);
    }

    get suggestions() {
        if (this.state.preset?.buttons?.length) {
            return this.state.preset.buttons.slice(0, 6);
        }
        const fallback = [_t("What can you help me with?")];
        if (this.currentAssistant?.has_knowledge) {
            fallback.push(_t("What does our warranty policy cover?"));
        }
        return fallback.map((text) => ({ title: text, prompt: text }));
    }

    /** Apply the context preset of the place the console was opened from. */
    async loadPreset(selectAssistant) {
        try {
            const info = await this.bridge.launchInfo(this.state.useContext ? this.state.context : null);
            this.state.preset = info.preset || null;
        } catch {
            this.state.preset = null;
        }
        const presetAssistant = this.state.preset?.assistant_id;
        if (selectAssistant && presetAssistant && this.state.assistants.some((a) => a.id === presetAssistant)) {
            this.state.assistantId = presetAssistant;
        }
    }

    // ------------------------------------------------------------------
    // Files
    // ------------------------------------------------------------------
    async attachFiles(files) {
        this.state.error = null;
        this.state.uploading = true;
        try {
            const session = await this.ensureSession();
            for (const file of files) {
                this.state.stagedFiles.push(await this.bridge.stageFile(session.id, file));
            }
        } catch (error) {
            this.state.error = error?.data?.message || error?.message || _t("The file could not be attached.");
        } finally {
            this.state.uploading = false;
        }
    }

    async removeFile(file) {
        await this.bridge.unstageFile(this.state.session.id, file.id);
        this.state.stagedFiles = this.state.stagedFiles.filter((f) => f.id !== file.id);
    }

    // ------------------------------------------------------------------
    // Actions
    // ------------------------------------------------------------------
    onAssistantChange(ev) {
        this.state.assistantId = parseInt(ev.target.value) || false;
        this.newConversation();
    }

    async toggleContext() {
        if (!this.state.session) {
            this.state.useContext = !this.state.useContext;
            await this.loadPreset(false);
        }
    }

    toggleHistory() {
        this.state.showHistory = !this.state.showHistory;
    }

    newConversation() {
        this.state.session = null;
        this.state.exchanges = [];
        this.state.error = null;
        this.state.toolActivity = null;
        this.state.stagedFiles = [];
        if (this.props.mode === "overlay") {
            this.state.context = this.bridge.currentContext();
        }
    }

    async openSession(sessionId) {
        const payload = await this.bridge.loadSession(sessionId);
        this.state.session = payload;
        this.state.assistantId = payload.assistant.id;
        this.state.exchanges = payload.exchanges;
        this.state.stagedFiles = payload.staged_files || [];
        this.state.error = null;
        if (this.props.mode === "overlay") {
            this.state.showHistory = false;
        }
    }

    async refreshRecent() {
        this.state.recent = await this.bridge.recentSessions();
    }

    async ensureSession() {
        if (!this.state.session) {
            const ctx = this.state.useContext ? this.state.context : null;
            this.state.session = await this.bridge.startSession(this.state.assistantId, ctx, this.state.preset?.id);
            this.state.exchanges = [];
        }
        return this.state.session;
    }

    async send(text) {
        if (this.state.busy) {
            return;
        }
        this.state.error = null;
        this.state.busy = true;
        try {
            const session = await this.ensureSession();
            this.state.stagedFiles = [];
            await this.bridge.runTurn(session.id, text, (event) => this.onEvent(event));
        } catch (error) {
            this.state.error = error?.data?.message || error?.message || _t("The request failed.");
        } finally {
            this.state.busy = false;
            this.state.toolActivity = null;
            this.state.statusText = null;
            this.refreshRecent();
        }
    }

    async retry() {
        if (!this.state.session || this.state.busy) {
            return;
        }
        this.state.busy = true;
        this.state.error = null;
        try {
            await this.bridge.runTurn(this.state.session.id, null, (event) => this.onEvent(event), {
                mode: "retry",
            });
        } catch (error) {
            this.state.error = error?.data?.message || error?.message || _t("The request failed.");
        } finally {
            this.state.busy = false;
            this.state.toolActivity = null;
            this.state.statusText = null;
        }
    }

    async decide(operation, approve) {
        this.state.busy = true;
        this.state.error = null;
        try {
            const result = await this.bridge.decideOperation(operation.id, approve);
            this.updateOperation(result.operation);
            for (const event of result.events) {
                this.onEvent(event);
            }
        } catch (error) {
            this.state.error = error?.data?.message || error?.message || _t("The operation failed.");
        } finally {
            this.state.busy = false;
        }
    }

    navigate(action) {
        this.bridge.openAction(action);
    }

    async postAnswer(exchange, mode) {
        try {
            await this.bridge.composeFromAnswer(exchange.id, mode);
        } catch (error) {
            this.state.error = error?.data?.message || error?.message || _t("The request failed.");
        }
    }

    close() {
        if (this.props.onClose) {
            this.props.onClose();
        } else {
            this.bridge.close();
        }
    }

    openFullPage() {
        this.bridge.close();
        this.bridge.openAction("ebshel_ai_suite.action_cai_console_page");
    }

    // ------------------------------------------------------------------
    // Streaming events
    // ------------------------------------------------------------------
    upsert(exchange) {
        const index = this.state.exchanges.findIndex((ex) => ex.id === exchange.id);
        if (index >= 0) {
            Object.assign(this.state.exchanges[index], exchange);
        } else {
            this.state.exchanges.push(exchange);
        }
    }

    updateOperation(operation) {
        for (const ex of this.state.exchanges) {
            if (ex.operation && ex.operation.id === operation.id) {
                ex.operation = operation;
            }
        }
    }

    onEvent(event) {
        switch (event.type) {
            case "user":
            case "step":
            case "done":
                this.upsert(event.exchange);
                break;
            case "start":
                this.upsert({ id: event.exchange_id, speaker: "assistant", body: "", state: "running" });
                break;
            case "delta": {
                const ex = this.state.exchanges.find((e) => e.id === event.exchange_id);
                if (ex) {
                    ex.body += event.text;
                }
                this.state.statusText = null;
                break;
            }
            case "status":
                this.state.statusText = event.text;
                break;
            case "tool":
                if (event.exchange) {
                    this.upsert(event.exchange);
                    this.state.toolActivity = null;
                } else {
                    this.state.toolActivity = event.name;
                }
                break;
            case "error":
                if (event.exchange) {
                    this.upsert(event.exchange);
                } else {
                    this.state.error = event.message;
                }
                break;
            default:
                // "sources", "confirmation" and "navigation" are rendered from
                // the exchanges themselves.
                break;
        }
    }
}
