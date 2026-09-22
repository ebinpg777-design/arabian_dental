/** @odoo-module **/

import { Component, markup, onMounted, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

import { waLines } from "./format";

const MOBILE = /Android|iPhone|iPad|iPod|Mobile/i;

/**
 * Where a prepared message is opened, and how.
 *
 * WhatsApp Web is reused in ONE named tab: a front desk clearing forty messages
 * must not end up with forty WhatsApp tabs, each logging in again.
 */
export function whatsappUrl(mode, number, text) {
    const t = encodeURIComponent(text || "");
    const phone = (number || "").replace(/\D/g, "");
    if (mode === "app") {
        return { url: `whatsapp://send?phone=${phone}&text=${t}`, target: "_self" };
    }
    if (mode === "web" || (mode === "auto" && !MOBILE.test(navigator.userAgent))) {
        return {
            url: `https://web.whatsapp.com/send?phone=${phone}&text=${t}`,
            target: "epg_whatsapp_web",
        };
    }
    return { url: `https://wa.me/${phone}?text=${t}`, target: "_blank" };
}

/**
 * Send one message through the lab's own WhatsApp.
 *
 * Preview → Open in WhatsApp → (the person presses Send there) → back here →
 * "Did it go?". The open happens inside the click itself, so no popup blocker
 * stands in the way, and coming back to this tab is noticed and asked about.
 * (client, 2026-09-17)
 */
export class WaSendDialog extends Component {
    static template = "epg_whatsapp.SendDialog";
    static components = { Dialog };
    static props = {
        messageId: { type: Number, optional: true },
        messageIds: { type: Array, optional: true },
        close: Function,
        onDone: { type: Function, optional: true },
        position: { type: String, optional: true },
    };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({
            loading: true,
            payload: null,
            error: "",
            step: "preview",
            openWith: "auto",
            back: false,
            busy: false,
            showHistory: false,
            snoozing: false,
            qr: "",
            editing: false,
            draft: "",
        });
        this.ids = this.props.messageIds || [this.props.messageId];
        this.decided = false;
        this.onVisible = () => {
            if (document.visibilityState === "visible" && this.state.step === "confirm") {
                this.state.back = true;
            }
        };
        onWillStart(() => this.load());
        onMounted(() => document.addEventListener("visibilitychange", this.onVisible));
        onWillUnmount(() => {
            document.removeEventListener("visibilitychange", this.onVisible);
            if (!this.decided && this.props.onDone) {
                this.props.onDone("closed");
            }
        });
    }

    async load() {
        try {
            const payload = await this.orm.call(
                "epg.whatsapp.message", "get_open_payload", [this.ids]);
            if (payload.blocked) {
                this.state.error = payload.blocked;
                this.env.bus.trigger("EPG_WA:CHANGED");
                return;
            }
            this.state.payload = payload;
            this.state.openWith = payload.open_with || "auto";
            if (payload.state === "opened") {
                this.state.step = "confirm";
            }
        } catch (e) {
            this.state.error = (e.data && e.data.message) || e.message || String(e);
        } finally {
            this.state.loading = false;
        }
    }

    get p() {
        return this.state.payload;
    }

    // ------------------------------------------------------------------ editing
    /** The words changed here, before they go: a name to add, a line to drop. */
    get canEdit() {
        return Boolean(this.p && !this.bundled && this.p.body);
    }

    edit() {
        this.state.draft = this.p.body;
        this.state.editing = true;
    }

    async saveEdit() {
        if (!this.state.draft.trim()) {
            this.notification.add(_t("The message cannot be empty."), { type: "warning" });
            return;
        }
        this.state.busy = true;
        try {
            await this.orm.call("epg.whatsapp.desk", "update_body", [this.p.id, this.state.draft]);
            await this.load();
        } finally {
            this.state.busy = false;
            this.state.editing = false;
        }
        this.env.bus.trigger("EPG_WA:CHANGED");
    }

    get qr() {
        return this.state.qr ? markup(this.state.qr) : "";
    }

    /** Drawn when a phone is chosen, not with every open: a QR of a long bundle
     * is real work, and most sends never need one. */
    async loadQr() {
        if (this.state.qr) {
            return;
        }
        this.state.qr = await this.orm.call("epg.whatsapp.message", "get_qr", [this.p.ids]);
    }

    get modes() {
        return [
            ["auto", _t("Automatic"), "fa-magic"],
            ["web", _t("WhatsApp Web"), "fa-globe"],
            ["app", _t("Desktop app"), "fa-desktop"],
            ["phone", _t("My phone"), "fa-mobile"],
        ];
    }

    /** The text as the doctor will see it, links whole. */
    get lines() {
        return waLines((this.p && this.p.text) || "", false);
    }

    async setMode(mode) {
        this.state.openWith = mode;
        // Remembered: the next message opens the same way without asking.
        await this.orm.call("epg.whatsapp.desk", "set_open_with", [mode]);
    }

    open() {
        if (!this.p) {
            return;
        }
        if (this.state.openWith === "phone") {
            this.state.step = "qr";
            this.loadQr();
        } else {
            const { url, target } = whatsappUrl(this.state.openWith, this.p.number, this.p.text);
            if (target === "_self") {
                const a = document.createElement("a");
                a.href = url;
                a.click();
            } else {
                window.open(url, target);
            }
            this.state.step = "confirm";
        }
        this.state.back = false;
        this.orm.call("epg.whatsapp.message", "action_mark_opened", [this.p.ids]);
    }

    get bundled() {
        return this.p && this.p.ids.length > 1;
    }

    /** The phone's share sheet, where WhatsApp (or WhatsApp Business) is picked. */
    get canShare() {
        return Boolean(navigator.share);
    }

    get attachments() {
        return (this.p && this.p.attachments) || [];
    }

    /** A phone can hand the PDF itself to WhatsApp through its share sheet. */
    get canShareFiles() {
        return Boolean(navigator.share && navigator.canShare && this.attachments.length);
    }

    async shareDocument() {
        const files = [];
        try {
            for (const a of this.attachments) {
                const blob = await (await fetch(a.url)).blob();
                files.push(new File([blob], a.name, { type: a.mimetype || "application/pdf" }));
            }
        } catch {
            this.notification.add(_t("The document could not be read; it goes as a link."), {
                type: "warning",
            });
            return;
        }
        if (!navigator.canShare({ files })) {
            this.notification.add(
                _t("This phone cannot share files from here; the document goes as a link."),
                { type: "warning" }
            );
            return;
        }
        try {
            await navigator.share({ files, text: this.p.text });
        } catch (e) {
            if (e && e.name === "AbortError") {
                return;
            }
            this.notification.add(_t("Sharing did not work here; open WhatsApp instead."), {
                type: "warning",
            });
            return;
        }
        this.orm.call("epg.whatsapp.message", "action_mark_opened", [this.p.ids]);
        this.state.step = "confirm";
        this.state.back = false;
    }

    async share() {
        try {
            await navigator.share({ text: this.p.text });
        } catch (e) {
            if (e && e.name === "AbortError") {
                return;
            }
            this.notification.add(_t("Sharing is not available here; use Copy text."), {
                type: "warning",
            });
            return;
        }
        this.orm.call("epg.whatsapp.message", "action_mark_opened", [this.p.ids]);
        this.state.step = "confirm";
        this.state.back = false;
    }

    /** When nothing opens - a locked-down computer, an odd browser - the text itself. */
    async copy() {
        let ok = false;
        try {
            await navigator.clipboard.writeText(this.p.text);
            ok = true;
        } catch {
            const area = document.createElement("textarea");
            area.value = this.p.text;
            document.body.appendChild(area);
            area.select();
            try {
                ok = document.execCommand("copy");
            } finally {
                area.remove();
            }
        }
        if (!ok) {
            this.notification.add(_t("Could not copy. Select the text and copy it by hand."), {
                type: "warning",
            });
            return;
        }
        this.notification.add(_t("Copied. Paste it into the doctor's chat in WhatsApp."), {
            type: "info",
        });
        this.orm.call("epg.whatsapp.message", "action_mark_opened", [this.p.ids]);
        this.state.step = "confirm";
        this.state.back = false;
    }

    get snoozes() {
        const out = [
            ["hour", _t("In 1 hour")],
            ["evening", _t("This evening")],
            ["tomorrow", _t("Tomorrow 9 AM")],
        ];
        if (this.p && this.p.best_time) {
            out.push(["best", _t("Best time (%s)", this.p.best_time.label.replace(/^.*around /, ""))]);
        }
        return out;
    }

    async snooze(preset) {
        const when = await this.orm.call(
            "epg.whatsapp.message", "action_snooze", [this.p.ids, preset]);
        if (when) {
            this.notification.add(_t("Back on the Desk %s.", when), { type: "info" });
        }
        this.finish("snoozed");
    }

    async sent() {
        this.state.busy = true;
        try {
            await this.orm.call("epg.whatsapp.message", "action_mark_sent", [this.p.ids]);
        } finally {
            this.state.busy = false;
        }
        this.notification.add(
            this.p.record
                ? _t("Logged on %s.", this.p.record)
                : _t("Logged as sent."),
            { type: "success", title: _t("WhatsApp sent") }
        );
        this.finish("sent");
    }

    async notSent() {
        await this.orm.call("epg.whatsapp.message", "action_mark_not_sent", [this.p.ids]);
        this.finish("skipped");
    }

    skip() {
        this.finish("skipped");
    }

    finish(result) {
        this.decided = true;
        this.env.bus.trigger("EPG_WA:CHANGED");
        if (this.props.onDone) {
            this.props.onDone(result);
        }
        this.props.close();
    }
}

/**
 * `epg_whatsapp_send`: open the send dialog for one message, from any button.
 * `close` also closes the dialog it was called from (the composer), and the
 * page underneath is reloaded once the message is sent so its chatter shows it.
 */
registry.category("actions").add("epg_whatsapp_send", async (env, action) => {
    const params = action.params || {};
    if (!params.message_id) {
        return;
    }
    env.services.dialog.add(WaSendDialog, {
        messageId: params.message_id,
        onDone: async (result) => {
            if (result === "sent" && (params.close || params.reload)) {
                env.services.action.doAction({ type: "ir.actions.client", tag: "soft_reload" });
            }
            // Made for this one click and not sent: it does not linger on the Desk.
            if (params.discard_on_close && (result === "closed" || result === "skipped")) {
                await env.services.orm.call(
                    "epg.whatsapp.message", "action_discard", [[params.message_id]]);
                env.bus.trigger("EPG_WA:CHANGED");
            }
        },
    });
    return params.close ? { type: "ir.actions.act_window_close" } : undefined;
});
