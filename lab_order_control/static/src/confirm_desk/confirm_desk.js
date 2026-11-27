/**
 * Confirm Orders: type or scan an order number, see the order, confirm it.
 *
 * For the person who confirms the field executives' orders, who is not an Odoo
 * expert (client, 2026-11-27). So: one box that is ready for typing the moment the
 * screen opens, the order shown in plain words, one big green button that asks
 * once before it does anything, and every refusal said as a sentence on the screen
 * instead of an error dialog. The waiting orders are listed beside it, oldest first.
 */
import { Component, onMounted, onWillStart, proxy, signal, useProps } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class LabOrderConfirmDesk extends Component {
    static template = "lab_order_control.OrderConfirmDesk";
    props = useProps();

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.inputRef = signal.ref();
        this.state = proxy({
            query: "", busy: false, order: null, choices: [], message: null,
            waiting: { count: 0, rows: [] }, done: [],
        });
        onWillStart(() => this.loadWaiting());
        onMounted(() => this.focusInput());
    }

    focusInput() {
        setTimeout(() => {
            const el = this.inputRef();
            if (el) {
                el.focus();
                el.select();
            }
        }, 60);
    }

    say(tone, text) {
        this.state.message = text ? { tone, text } : null;
    }

    async loadWaiting() {
        try {
            this.state.waiting = await this.orm.call("lab.order.confirm.desk", "waiting", []);
        } catch {
            // the list is a help, not the job: the box still works without it
        }
    }

    onInput(ev) {
        this.state.query = ev.target.value;
    }

    onKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.find();
        }
    }

    async find() {
        const query = (this.state.query || "").trim();
        if (!query || this.state.busy) {
            if (!query) {
                this.say("info", _t("Type the order number first, for example 81488."));
                this.focusInput();
            }
            return;
        }
        this.state.busy = true;
        this.say(null);
        try {
            const res = await this.orm.call("lab.order.confirm.desk", "find", [query]);
            this.state.choices = res.choices || [];
            this.state.order = res.order || null;
            if (!res.order && !this.state.choices.length) {
                this.say("danger", _t("No order found for “%s”. Check the number and try again.", query));
                this.focusInput();
            } else if (!res.order) {
                this.say("info", _t("More than one order matches “%s”. Tap the right one.", query));
            }
        } finally {
            this.state.busy = false;
        }
    }

    async openOrder(id) {
        if (this.state.busy) {
            return;
        }
        this.state.busy = true;
        this.say(null);
        try {
            this.state.order = await this.orm.call("lab.order.confirm.desk", "open_order", [id]);
            this.state.choices = [];
            this.state.query = this.state.order.name;
        } finally {
            this.state.busy = false;
        }
    }

    clear() {
        this.state.order = null;
        this.state.choices = [];
        this.state.query = "";
        this.say(null);
        this.focusInput();
    }

    askConfirm() {
        const order = this.state.order;
        if (!order || !order.can_confirm || this.state.busy) {
            return;
        }
        const who = [order.clinic, order.patient && _t("patient %s", order.patient)].filter(Boolean).join(" · ");
        this.dialog.add(ConfirmationDialog, {
            title: _t("Confirm order %s?", order.name),
            body: _t("%(who)s — %(amount)s. Once confirmed, the work goes into production.",
                     { who: who || order.name, amount: order.amount }),
            confirmLabel: String(_t("Yes, confirm")),
            confirmClass: "btn-success",
            cancelLabel: String(_t("No, go back")),
            confirm: () => this.confirm(order.id),
            cancel: () => {},
        });
    }

    async confirm(id) {
        this.state.busy = true;
        this.say(null);
        try {
            const res = await this.orm.call("lab.order.confirm.desk", "confirm", [id]);
            if (res.card) {
                this.state.order = res.card;
            }
            if (res.action) {
                // a question to answer first (the company's warning): answer it, then
                // the order is read again to see whether it went through
                await this.action.doAction(res.action, {
                    onClose: async () => {
                        await this.openOrder(id);
                        if (this.state.order && this.state.order.state === "sale") {
                            this.confirmed(this.state.order);
                        }
                    },
                });
                return;
            }
            if (res.done) {
                this.confirmed(res.card);
            } else if (res.error) {
                this.say("danger", res.error);
            }
        } finally {
            this.state.busy = false;
        }
    }

    confirmed(card) {
        this.say("success", _t("%s is confirmed. Type the next order number.", card.name));
        this.state.done = [
            { id: card.id, name: card.name, clinic: card.clinic,
              at: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) },
            ...this.state.done.filter((d) => d.id !== card.id),
        ].slice(0, 15);
        this.state.query = "";
        this.loadWaiting();
        this.focusInput();
    }

    openFullOrder() {
        const order = this.state.order;
        if (!order) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window", res_model: "sale.order", res_id: order.id,
            views: [[false, "form"]], target: "current",
        });
    }

    async refreshWaiting() {
        this.state.busy = true;
        try {
            await this.loadWaiting();
        } finally {
            this.state.busy = false;
        }
    }
}

registry.category("actions").add("lab_order_confirm_desk", LabOrderConfirmDesk);
