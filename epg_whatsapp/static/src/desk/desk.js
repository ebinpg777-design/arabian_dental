/** @odoo-module **/

import { Component, onWillDestroy, onWillStart, useState } from "@odoo/owl";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useBus, useService } from "@web/core/utils/hooks";
import { Layout } from "@web/search/layout";

import { initials } from "./format";
import { WaSendDialog } from "./send_dialog";

/**
 * The WhatsApp Desk: every message waiting to be sent from the lab's own WhatsApp,
 * and what became of the ones already sent - opened, answered, or silent.
 *
 * "Send all" walks the queue one message at a time. Each is opened, sent in
 * WhatsApp, confirmed, and the next one comes up on its own. (client, 2026-09-17)
 */
export class WaDesk extends Component {
    static template = "epg_whatsapp.Desk";
    static components = { Layout };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.state = useState({
            data: null,
            scope: "mine",
            loading: true,
            tab: "queue",
            editing: {},
            reply: {},
            snooze: {},
            running: false,
            query: "",
            // What the Desk shows: everything, the notifications, or the marketing.
            kind: "all",
        });
        onWillStart(() => this.load());
        useBus(this.env.bus, "EPG_WA:CHANGED", () => {
            if (!this.state.running) {
                this.load(true);
            }
        });
        // The Desk is left open on a desk: new notifications appear on their own.
        this.timer = setInterval(() => {
            if (
                document.visibilityState === "visible" &&
                !this.state.running &&
                !Object.keys(this.state.editing).length
            ) {
                this.load(true);
            }
        }, 60000);
        onWillDestroy(() => clearInterval(this.timer));
    }

    // ------------------------------------------------------------------ search
    _matches(card) {
        if (this.state.kind === "notify" && card.marketing) {
            return false;
        }
        if (this.state.kind === "marketing" && !card.marketing) {
            return false;
        }
        const q = this.state.query.trim().toLowerCase();
        if (!q) {
            return true;
        }
        return [card.partner, card.number, card.template, card.body, card.record]
            .join(" ")
            .toLowerCase()
            .includes(q);
    }

    get visibleGroups() {
        return this.d.groups.filter((g) => g.cards.some((c) => this._matches(c)));
    }

    get visibleLater() {
        return this.d.later.filter((c) => this._matches(c));
    }

    get visibleRecent() {
        return this.d.recent.filter((c) => this._matches(c));
    }

    async load(quiet) {
        if (!quiet) {
            this.state.loading = true;
        }
        try {
            this.state.data = await this.orm.call("epg.whatsapp.desk", "get_desk", [
                this.state.scope,
            ]);
        } finally {
            this.state.loading = false;
        }
    }

    get d() {
        return this.state.data;
    }

    async setScope(scope) {
        this.state.scope = scope;
        await this.load();
    }

    async setOpenWith(ev) {
        await this.orm.call("epg.whatsapp.desk", "set_open_with", [ev.target.value]);
        this.d.open_with = ev.target.value;
    }

    // ------------------------------------------------------------------ sending
    send(card) {
        this.dialog.add(WaSendDialog, { messageIds: [card.id] });
    }

    /** Every message waiting for this doctor, as the one WhatsApp they receive. */
    sendGroup(group) {
        this.dialog.add(WaSendDialog, { messageIds: group.ids });
    }

    /** One doctor after another - each doctor's messages as one - until the queue
     * is empty or the person stops. */
    sendAll() {
        // What is on screen: a search narrows "all" to the doctors it found.
        const ids = this.visibleGroups.map((g) => g.ids);
        if (!ids.length) {
            return;
        }
        this.state.running = true;
        let sent = 0;
        const next = (index) => {
            if (index >= ids.length) {
                this.stopRun(sent);
                return;
            }
            this.dialog.add(WaSendDialog, {
                messageIds: ids[index],
                position: `${index + 1} / ${ids.length}`,
                onDone: (result) => {
                    if (result === "closed") {
                        this.stopRun(sent);
                        return;
                    }
                    if (result === "sent") {
                        sent += 1;
                    }
                    // After the dialog has gone: the next one opens on a clean stack.
                    setTimeout(() => next(index + 1), 150);
                },
            });
        };
        next(0);
    }

    stopRun(sent) {
        if (!this.state.running) {
            return;
        }
        this.state.running = false;
        if (sent) {
            this.notification.add(_t("%s messages sent and logged.", sent), {
                type: "success",
            });
        }
        this.load(true);
    }

    cancel(card) {
        this.dialog.add(ConfirmationDialog, {
            title: _t("Don't send this message?"),
            body: _t("The message to %s will be cancelled.", card.partner || card.number),
            confirmLabel: _t("Cancel message"),
            confirm: async () => {
                await this.orm.call("epg.whatsapp.message", "action_cancel", [[card.id]]);
                this.env.bus.trigger("EPG_WA:CHANGED");
            },
            cancel: () => {},
        });
    }

    // ------------------------------------------------------------------ later
    toggleSnooze(card) {
        this.state.snooze[card.id] = !this.state.snooze[card.id];
    }

    snoozeOptions(card) {
        const out = [
            ["hour", _t("In 1 hour")],
            ["evening", _t("This evening")],
            ["tomorrow", _t("Tomorrow 9 AM")],
        ];
        if (card.best_time) {
            out.push(["best", _t("Best time")]);
        }
        return out;
    }

    async snooze(ids, preset) {
        const when = await this.orm.call("epg.whatsapp.message", "action_snooze", [ids, preset]);
        this.state.snooze = {};
        if (when) {
            this.notification.add(_t("Back on the Desk %s.", when), { type: "info" });
        }
        this.env.bus.trigger("EPG_WA:CHANGED");
    }

    async unsnooze(card) {
        await this.orm.call("epg.whatsapp.message", "action_unsnooze", [[card.id]]);
        this.env.bus.trigger("EPG_WA:CHANGED");
    }

    openCampaigns() {
        this.action.doAction("epg_whatsapp.action_campaign");
    }

    // ------------------------------------------------------------------ the tray
    /** Answer a doctor: their chat, with the box ready. */
    answer(row) {
        this.action.doAction({
            type: "ir.actions.client", tag: "epg_whatsapp_chats", name: _t("WhatsApp"),
            params: { partner_id: row.partner_id || false },
        });
    }

    openRecord(row) {
        this.action.doAction({
            type: "ir.actions.act_window", res_model: row.res_model,
            res_id: row.res_id, views: [[false, "form"]],
        });
    }

    async handled(row) {
        await this.orm.call("epg.whatsapp.desk", "mark_handled", [row.id]);
        this.env.bus.trigger("EPG_WA:CHANGED");
        await this.load(true);
    }

    // ------------------------------------------------------------------ editing
    edit(card) {
        this.state.editing[card.id] = card.body;
    }

    async saveEdit(card) {
        await this.orm.call("epg.whatsapp.desk", "update_body", [
            card.id,
            this.state.editing[card.id],
        ]);
        delete this.state.editing[card.id];
        await this.load(true);
    }

    cancelEdit(card) {
        delete this.state.editing[card.id];
    }

    // ------------------------------------------------------------------ replies
    openReply(card) {
        this.state.reply[card.id] = "";
    }

    async saveReply(card) {
        const text = (this.state.reply[card.id] || "").trim();
        if (!text) {
            return;
        }
        await this.orm.call("epg.whatsapp.message", "log_reply", [[card.id], text]);
        delete this.state.reply[card.id];
        this.notification.add(_t("Reply logged on the record."), { type: "success" });
        await this.load(true);
    }

    // ------------------------------------------------------------------ drills
    openRecord(card) {
        if (!card.res_model || !card.res_id) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: card.res_model,
            res_id: card.res_id,
            views: [[false, "form"]],
        });
    }

    openMessages(domain, name) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name,
            res_model: "epg.whatsapp.message",
            views: [[false, "list"], [false, "form"]],
            domain,
        });
    }

    openSenders() {
        this.action.doAction("epg_whatsapp.action_account");
    }

    initials(name) {
        return initials(name);
    }

    openChats() {
        this.action.doAction("epg_whatsapp.action_chats");
    }
}

registry.category("actions").add("epg_whatsapp_desk", WaDesk);
