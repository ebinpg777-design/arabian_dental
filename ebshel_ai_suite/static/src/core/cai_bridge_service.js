import { reactive } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { router } from "@web/core/browser/router";
import { _t } from "@web/core/l10n/translation";
import { rpc } from "@web/core/network/rpc";
import { registry } from "@web/core/registry";
import { blobToBase64 } from "./cai_voice";

/**
 * Ebshel AI bridge: the single client-side entry point to the AI backend.
 *
 * - keeps the console open/closed state (shared by the systray launcher, the
 *   command palette and the console itself);
 * - talks to the server through ORM calls, or through the NDJSON streaming
 *   endpoint when streaming is enabled (with automatic fallback);
 * - relays background-job notifications received on the bus.
 */
export class CaiBridge {
    constructor(env, services) {
        this.env = env;
        this.orm = services.orm;
        this.action = services.action;
        this.notification = services.notification;
        this.state = reactive({ open: false, pendingPrompt: null, config: null });
        this._boot = null;
    }

    // ------------------------------------------------------------------
    // Console state
    // ------------------------------------------------------------------
    bootstrap(force = false) {
        if (!this._boot || force) {
            this._boot = rpc("/ebshel_ai/bootstrap", {}, { silent: true })
                .then((config) => {
                    this.state.config = config;
                    return config;
                })
                .catch(() => {
                    this.state.config = { enabled: false };
                    return this.state.config;
                });
        }
        return this._boot;
    }

    open({ prompt } = {}) {
        if (prompt) {
            this.state.pendingPrompt = prompt;
        }
        this.state.open = true;
    }

    close() {
        this.state.open = false;
    }

    toggle() {
        this.state.open = !this.state.open;
    }

    /** The record currently displayed in a form view, if any. */
    currentContext() {
        const controller = this.action.currentController;
        if (!controller) {
            return null;
        }
        const props = controller.props || {};
        const viewType = controller.view?.type || props.type;
        const resModel = props.resModel;
        let resId = props.resId;
        if (typeof resId !== "number" && typeof router.current.resId === "number") {
            resId = router.current.resId;
        }
        if (viewType !== "form" || !resModel || typeof resId !== "number") {
            return { model: resModel || null, resId: null, label: null };
        }
        return { model: resModel, resId, label: controller.displayName || resModel };
    }

    // ------------------------------------------------------------------
    // Conversations
    // ------------------------------------------------------------------
    launchInfo(context) {
        return this.orm.call("community.ai.session", "cai_launch_info", [], {
            context_model: context?.model || false,
            context_res_id: context?.resId || false,
        });
    }

    startSession(assistantId, context, presetId = false) {
        return this.orm.call("community.ai.session", "cai_start", [], {
            assistant_id: assistantId || false,
            context_model: context?.model || false,
            context_res_id: context?.resId || false,
            preset_id: presetId || false,
        });
    }

    /** Upload a browser ``File`` so that it is sent with the next message. */
    async stageFile(sessionId, file) {
        const data = await new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(String(reader.result).split(",")[1] || "");
            reader.onerror = () => reject(reader.error);
            reader.readAsDataURL(file);
        });
        return this.orm.call("community.ai.session", "cai_stage_file", [[sessionId], file.name, data]);
    }

    unstageFile(sessionId, fileId) {
        return this.orm.call("community.ai.session", "cai_unstage_file", [[sessionId], fileId]);
    }

    async composeFromAnswer(exchangeId, mode) {
        const action = await this.orm.call("community.ai.exchange", "cai_compose_action", [[exchangeId], mode]);
        return this.action.doAction(action);
    }

    loadSession(sessionId) {
        return this.orm.call("community.ai.session", "cai_messages", [[sessionId]]);
    }

    recentSessions() {
        return this.orm.call("community.ai.session", "cai_recent", []);
    }

    closeSession(sessionId) {
        return this.orm.call("community.ai.session", "cai_close", [[sessionId]]);
    }

    /**
     * Run a turn. ``mode`` is "send" (new prompt) or "retry".
     * Events are delivered to ``onEvent`` as they arrive.
     */
    async runTurn(sessionId, text, onEvent, { mode = "send" } = {}) {
        const streaming =
            this.state.config?.streaming &&
            typeof TextDecoder !== "undefined" &&
            typeof ReadableStream !== "undefined";
        if (streaming) {
            let received = false;
            try {
                await this._stream(sessionId, text, mode, (event) => {
                    received = true;
                    onEvent(event);
                });
                return;
            } catch (error) {
                if (received) {
                    onEvent({ type: "error", message: _t("The connection was interrupted. Please retry.") });
                    return;
                }
                // nothing arrived: fall back to a regular RPC call
            }
        }
        const method = mode === "retry" ? "cai_retry" : "cai_send";
        const args = mode === "retry" ? [[sessionId]] : [[sessionId], text];
        const result = await this.orm.call("community.ai.session", method, args);
        for (const event of result.events) {
            onEvent(event);
        }
    }

    async _stream(sessionId, text, mode, onEvent) {
        const body = new URLSearchParams({
            session_id: String(sessionId),
            prompt: text || "",
            mode,
            csrf_token: odoo.csrf_token,
        });
        const response = await browser.fetch("/ebshel_ai/stream", {
            method: "POST",
            body,
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
        });
        if (!response.ok || !response.body) {
            throw new Error(`stream unavailable (${response.status})`);
        }
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (true) {
            const { done, value } = await reader.read();
            if (done) {
                break;
            }
            buffer += decoder.decode(value, { stream: true });
            let index;
            while ((index = buffer.indexOf("\n")) >= 0) {
                const line = buffer.slice(0, index).trim();
                buffer = buffer.slice(index + 1);
                if (line) {
                    onEvent(JSON.parse(line));
                }
            }
        }
        if (buffer.trim()) {
            onEvent(JSON.parse(buffer));
        }
    }

    decideOperation(operationId, approve) {
        return this.orm.call("community.ai.operation", "cai_decide", [[operationId], approve]);
    }

    // ------------------------------------------------------------------
    // Stateless helpers
    // ------------------------------------------------------------------
    writingOptions(resModel = false) {
        this._writingOptions = this._writingOptions || {};
        const key = resModel || "";
        if (!this._writingOptions[key]) {
            this._writingOptions[key] = this.orm.call("community.ai.text.service", "cai_writing_options", [], {
                res_model: resModel || false,
            });
        }
        return this._writingOptions[key];
    }

    async transcribe(blob, summarize = false) {
        const data = await blobToBase64(blob);
        return this.orm.call("community.ai.text.service", "cai_transcribe", [data, blob.type || "audio/webm"], {
            summarize,
        });
    }

    transformText(operation, text, options = {}) {
        return this.orm.call("community.ai.text.service", "cai_transform_text", [operation, text, options]);
    }

    async findWithAi(query) {
        const context = this.currentContext();
        const result = await this.orm.call("community.ai.text.service", "cai_natural_search", [
            query,
            context?.model || false,
        ]);
        this.notification.add(
            result.explanation || _t("%(count)s matching record(s).", { count: result.count }),
            { title: _t("AI search"), type: "info" }
        );
        return this.action.doAction(result.action);
    }

    openAction(action) {
        return this.action.doAction(action);
    }
}

export const communityAiBridgeService = {
    dependencies: ["orm", "action", "notification", "bus_service"],
    start(env, services) {
        const bridge = new CaiBridge(env, services);
        services.bus_service.subscribe("ebshel_ai/job", (payload) => {
            const messages = {
                done: _t("AI job finished: %(name)s %(record)s", payload),
                failed: _t("AI job failed: %(name)s %(record)s — %(error)s", payload),
                awaiting_approval: _t("AI proposal waiting for approval: %(name)s %(record)s", payload),
            };
            services.notification.add(messages[payload.state] || payload.name, {
                type: payload.state === "failed" ? "danger" : "info",
            });
        });
        return bridge;
    },
};

registry.category("services").add("communityAiBridge", communityAiBridgeService);
