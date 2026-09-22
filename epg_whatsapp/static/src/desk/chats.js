/** @odoo-module **/

import { Component, onWillDestroy, onWillStart, useEffect, useRef, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useBus, useService } from "@web/core/utils/hooks";
import { Layout } from "@web/search/layout";

import { initials, waLines } from "./format";
import { WaSendDialog } from "./send_dialog";

/**
 * Chats: every conversation with a doctor, as a chat app shows it.
 *
 * Two panes on a desk, one at a time on a phone: the list, then the thread with a
 * back arrow. Writing here creates the message and opens WhatsApp, the same way as
 * on the Desk; what the doctor answers is logged from the same bar.
 * (client, 2026-09-17)
 */
export class WaChats extends Component {
    static template = "epg_whatsapp.Chats";
    static components = { Layout };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        const params = (this.props.action && this.props.action.params) || {};
        this.state = useState({
            chats: [],
            loading: true,
            search: "",
            openId: false,
            thread: null,
            threadLoading: false,
            showThread: false,
            text: "",
            busy: false,
            replying: false,
            replyText: "",
            // Opened from a record: its own messages are picked out in the thread.
            focus: params.res_model && params.res_id ? { model: params.res_model, id: params.res_id } : null,
        });
        this.threadRef = useRef("thread");
        this.composeRef = useRef("compose");
        onWillStart(() => this.load(params.partner_id));
        useBus(this.env.bus, "EPG_WA:CHANGED", () => this.refresh());
        // The newest bubble is the one being looked for: keep the thread at the bottom.
        useEffect(
            () => {
                const el = this.threadRef.el;
                if (el) {
                    el.scrollTop = el.scrollHeight;
                }
            },
            () => [this.state.thread && this.state.thread.bubbles.length, this.state.openId]
        );
        this.timer = setInterval(() => {
            if (document.visibilityState === "visible" && !this.state.busy) {
                this.refresh();
            }
        }, 60000);
        onWillDestroy(() => {
            clearInterval(this.timer);
            clearTimeout(this._searchTimer);
        });
    }

    async load(partnerId) {
        this.state.loading = true;
        try {
            const data = await this.orm.call("epg.whatsapp.desk", "get_chats", [
                this.state.search,
                partnerId || false,
            ]);
            this.state.chats = data.chats;
            // The breadcrumb names the doctor when the chat was opened from a
            // record or a contact: "OC245822 / WhatsApp · Dr Menon".
            const opened = partnerId && data.chats.find((c) => c.partner_id === partnerId);
            if (opened && opened.partner && this.env.config && this.env.config.setDisplayName) {
                this.env.config.setDisplayName(_t("WhatsApp · %s", opened.partner));
            }
            if (data.open_id && !this.state.openId) {
                await this.open(data.open_id);
            }
        } finally {
            this.state.loading = false;
        }
    }

    async refresh() {
        const data = await this.orm.silent.call("epg.whatsapp.desk", "get_chats", [
            this.state.search,
            false,
        ]);
        this.state.chats = data.chats;
        if (this.state.openId) {
            this.state.thread = await this.orm.silent.call(
                "epg.whatsapp.desk", "get_thread", [this.state.openId]);
        }
    }

    onSearch() {
        clearTimeout(this._searchTimer);
        this._searchTimer = setTimeout(() => this.load(), 250);
    }

    async open(id) {
        this.state.openId = id;
        this.state.showThread = true;
        this.state.threadLoading = true;
        this.state.replying = false;
        try {
            this.state.thread = await this.orm.call("epg.whatsapp.desk", "get_thread", [id]);
        } finally {
            this.state.threadLoading = false;
        }
    }

    back() {
        this.state.showThread = false;
    }

    get t() {
        return this.state.thread;
    }

    initials(name) {
        return initials(name);
    }

    lines(text) {
        return waLines(text);
    }

    isFocused(bubble) {
        const f = this.state.focus;
        return Boolean(f && bubble.res_model === f.model && bubble.res_id === f.id);
    }

    // ------------------------------------------------------------------ writing
    async send() {
        const text = this.state.text.trim();
        if (!text || this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            const res = await this.orm.call("epg.whatsapp.desk", "compose", [
                this.state.openId,
                text,
            ]);
            this.state.text = "";
            if (res.channel === "link") {
                this.dialog.add(WaSendDialog, {
                    messageIds: [res.id],
                    onDone: () => this.refresh(),
                });
            } else {
                this.notification.add(_t("Sent through the Cloud API."), { type: "success" });
            }
            await this.refresh();
        } finally {
            this.state.busy = false;
        }
    }

    onComposeKeydown(ev) {
        // Enter alone is a new line, as in WhatsApp; Ctrl/⌘+Enter sends.
        if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {
            ev.preventDefault();
            this.send();
        }
    }

    async pickSnippet(ev) {
        const id = Number(ev.target.value);
        ev.target.value = "";
        if (!id) {
            return;
        }
        const text = await this.orm.call("epg.whatsapp.snippet", "render", [
            [id],
            "res.partner",
            this.t.partner_id || false,
        ]);
        this.state.text = this.state.text.trim() ? `${this.state.text.trim()}\n\n${text}` : text;
        if (this.composeRef.el) {
            this.composeRef.el.focus();
        }
    }

    async logReply() {
        const text = this.state.replyText.trim();
        if (!text) {
            return;
        }
        await this.orm.call("epg.whatsapp.desk", "log_inbound", [this.state.openId, text]);
        this.state.replyText = "";
        this.state.replying = false;
        this.notification.add(_t("Logged on the contact."), { type: "success" });
        await this.refresh();
    }

    sendWaiting(bubble) {
        this.dialog.add(WaSendDialog, { messageIds: [bubble.id], onDone: () => this.refresh() });
    }

    // ------------------------------------------------------------------ drills
    openRecord(bubble) {
        if (!bubble.res_model || !bubble.res_id) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: bubble.res_model,
            res_id: bubble.res_id,
            views: [[false, "form"]],
        });
    }

    openPartner() {
        if (!this.t || !this.t.partner_id) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "res.partner",
            res_id: this.t.partner_id,
            views: [[false, "form"]],
        });
    }

    importChat() {
        this.action.doAction("epg_whatsapp.action_chat_import", {
            additionalContext: { default_partner_id: this.t.partner_id },
            onClose: () => this.refresh(),
        });
    }
}

registry.category("actions").add("epg_whatsapp_chats", WaChats);
