/** @odoo-module **/
import { Component, onMounted, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { serializeDateTime } from "@web/core/l10n/dates";

const { DateTime } = luxon;

/**
 * The doctor call desk.
 *
 * One board for the people whose job is to ring the doctor: what is waiting to be
 * taken, what each of them has in hand, what is waiting for a callback (and comes
 * back to the top when its time arrives), what has gone to the field, and what was
 * closed today. A ticket opens beside the board with everything somebody needs in
 * front of them before they ring - and what they do next is one press away.
 *
 * The server owns every rule (who may take, what closing does to the order); this
 * component only asks and repaints.
 */
const COLUMNS = [
    { key: "new", tone: "blue", icon: "fa-inbox", name: _t("To take"), empty: _t("Nothing is waiting to be taken") },
    { key: "taken", tone: "violet", icon: "fa-hand-paper-o", name: _t("In hand"), empty: _t("Nobody has a ticket in hand") },
    { key: "waiting", tone: "amber", icon: "fa-clock-o", name: _t("Waiting for the doctor"), empty: _t("No callback is pending") },
    { key: "field", tone: "teal", icon: "fa-map-marker", name: _t("Asked in person"), empty: _t("Nothing is with the field") },
    { key: "closed", tone: "green", icon: "fa-check", name: _t("Closed today"), empty: _t("Nothing closed yet today") },
];

export class DoctorDesk extends Component {
    static template = "lab_doctor_desk.Desk";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        const params = (this.props.action && this.props.action.params) || {};
        this.columns = COLUMNS;
        this.state = useState({
            loading: true,
            error: null,
            data: null,
            filter: "all",              // all | mine | rush | late | escalated | due
            search: "",
            tab: "new",                 // the column shown on a phone
            ticket: null,               // the ticket open beside the board
            opening: false,
            form: null,                 // {kind, ...}: what is being written on the open ticket
            busy: false,
            also: {},                   // other tickets of the same doctor settled by the same call
        });
        this.wanted = params.ticket_id || null;
        this.onKey = (ev) => this.keyboard(ev);
        onWillStart(() => this.load());
        onMounted(() => {
            document.addEventListener("keydown", this.onKey);
            // the board repaints by itself: a callback comes due, somebody else takes a ticket
            this.timer = setInterval(() => {
                if (!this.state.form && !this.state.busy && document.visibilityState === "visible") {
                    this.load(true);
                }
            }, 60000);
            if (this.wanted) {
                this.open({ id: this.wanted });
                this.wanted = null;
            }
        });
        onWillUnmount(() => {
            document.removeEventListener("keydown", this.onKey);
            clearInterval(this.timer);
        });
    }

    // ------------------------------------------------------------------ loading
    async load(quiet) {
        if (!quiet) {
            this.state.loading = !this.state.data;
        }
        try {
            this.state.data = await this.orm.call("lab.doctor.desk", "get_desk", []);
            this.state.error = null;
        } catch (error) {
            this.state.error = (error && error.data && error.data.message) || String(error);
        } finally {
            this.state.loading = false;
        }
    }

    async refreshTicket() {
        if (this.state.ticket) {
            const fresh = await this.orm.call("lab.doctor.desk", "get_ticket", [this.state.ticket.id]);
            this.state.ticket = fresh || null;
        }
    }

    /** Ask, repaint the board and the open ticket, and say what happened. */
    async act(method, args, kwargs, done) {
        if (this.state.busy) {
            return false;
        }
        this.state.busy = true;
        try {
            await this.orm.call("lab.doctor.ticket", method, args, kwargs || {});
            this.state.form = null;
            this.state.also = {};
            await Promise.all([this.load(true), this.refreshTicket()]);
            if (done) {
                this.notification.add(done, { type: "success" });
            }
            return true;
        } finally {
            this.state.busy = false;
        }
    }

    // ------------------------------------------------------------------ the board
    matches(card) {
        const f = this.state.filter;
        if (f === "mine" && !card.mine) return false;
        if (f === "rush" && card.priority === "0") return false;
        if (f === "late" && card.due_state !== "late") return false;
        if (f === "escalated" && !card.escalated) return false;
        if (f === "due" && !card.callback_due) return false;
        const q = this.state.search.trim().toLowerCase();
        if (!q) {
            return true;
        }
        return [card.clinic, card.practice, card.patient, card.order, card.name, card.question, card.agent, card.phone]
            .some((v) => (v || "").toLowerCase().includes(q));
    }

    cardsOf(column) {
        const d = this.state.data;
        if (!d) {
            return [];
        }
        if (column.key === "closed") {
            return d.done.filter((c) => this.matches(c));
        }
        const cards = d.cards.filter((c) => c.state === column.key && this.matches(c));
        if (column.key === "waiting") {
            // what is due comes first; then the callback that is nearest
            cards.sort((a, b) => Number(b.callback_due) - Number(a.callback_due));
        }
        if (column.key === "taken") {
            cards.sort((a, b) => Number(b.mine) - Number(a.mine));
        }
        return cards;
    }

    get filters() {
        const k = this.state.data.kpis;
        const out = [
            { key: "all", name: _t("All"), count: k.open },
            { key: "mine", name: _t("Mine"), count: k.mine },
            { key: "due", name: _t("Due now"), count: k.due, tone: "amber" },
            { key: "late", name: _t("Late"), count: k.late, tone: "red" },
            { key: "rush", name: _t("Urgent"), count: this.state.data.cards.filter((c) => c.priority !== "0").length, tone: "rose" },
        ];
        if (k.escalated || this.state.data.me.lead) {
            out.push({ key: "escalated", name: _t("Escalated"), count: k.escalated, tone: "red" });
        }
        return out;
    }

    cardClass(card) {
        const cls = ["o_ddk_card", "o_ddk_p" + card.priority, "o_ddk_due_" + card.due_state];
        if (this.state.ticket && this.state.ticket.id === card.id) cls.push("on");
        if (card.callback_due) cls.push("o_ddk_now");
        if (card.escalated) cls.push("o_ddk_esc");
        if (card.state === "taken" && !card.mine) cls.push("o_ddk_other");
        return cls.join(" ");
    }

    clock(card) {
        if (card.state === "closed" || card.minutes_left === null) {
            return "";
        }
        return card.minutes_left < 0 ? _t("late by %s", card.left) : _t("%s left", card.left);
    }

    initials(name) {
        // letters only: "Asha (desk)" is A, not A(
        const words = (name || "").split(/\s+/).filter((w) => /^\p{L}/u.test(w));
        return words.slice(0, 2).map((w) => w[0].toUpperCase()).join("") || "?";
    }

    async toggleAtDesk() {
        const me = this.state.data.me;
        me.at_desk = await this.orm.call("lab.doctor.desk", "set_at_desk", [!me.at_desk]);
        this.notification.add(me.at_desk ? _t("You are at the desk: new tickets can come to you.")
            : _t("You are away: new tickets pass you by."), { type: "info" });
    }

    // ------------------------------------------------------------------ one ticket
    async open(card) {
        this.state.opening = true;
        this.state.form = null;
        this.state.also = {};
        try {
            const ticket = await this.orm.call("lab.doctor.desk", "get_ticket", [card.id]);
            this.state.ticket = ticket || null;
            if (ticket && ticket.state !== "closed" && ticket.state !== "cancelled") {
                this.state.tab = ticket.state;
            }
        } finally {
            this.state.opening = false;
        }
    }

    close() {
        this.state.ticket = null;
        this.state.form = null;
    }

    get isOpen() {
        const t = this.state.ticket;
        return !!t && ["new", "taken", "waiting", "field"].includes(t.state);
    }

    get alsoIds() {
        return Object.keys(this.state.also).filter((id) => this.state.also[id]).map(Number);
    }

    take(card, ev) {
        if (ev) {
            ev.stopPropagation();
        }
        return this.act("action_take", [[card.id]], {}, _t("%s is yours.", card.name));
    }

    release() {
        return this.act("action_release", [[this.state.ticket.id]], {}, _t("Back in the pool."));
    }

    backToDesk() {
        return this.act("action_back_to_desk", [[this.state.ticket.id]], {}, _t("In hand again."));
    }

    settleEscalation() {
        return this.act("action_settle_escalation", [[this.state.ticket.id]], {}, _t("Escalation dealt with."));
    }

    reopen() {
        return this.act("action_reopen", [[this.state.ticket.id]], {}, _t("Reopened."));
    }

    startForm(kind) {
        const base = { kind };
        if (kind === "answer") {
            Object.assign(base, { outcome: "as_is", channel: "phone", answer: "", tell: true });
        } else if (kind === "attempt") {
            Object.assign(base, { response: "no_answer", remarks: "", wait: this.state.data.retry, custom: "" });
        } else if (kind === "unreached") {
            Object.assign(base, { answer: "" });
        } else if (kind === "assign") {
            Object.assign(base, { user: this.state.ticket.agent_id || "" });
        } else {
            Object.assign(base, { note: "" });
        }
        this.state.form = this.state.form && this.state.form.kind === kind ? null : base;
    }

    get waits() {
        const retry = this.state.data.retry;
        const out = [{ minutes: 30, name: _t("30 min") }, { minutes: 60, name: _t("1 hour") },
                     { minutes: 120, name: _t("2 hours") }, { minutes: 240, name: _t("4 hours") }];
        if (!out.some((w) => w.minutes === retry)) {
            out.unshift({ minutes: retry, name: _t("%s min", retry) });
        }
        out.push({ minutes: "tomorrow", name: _t("Tomorrow 10 am") });
        out.push({ minutes: "custom", name: _t("At…") });
        return out;
    }

    /** The moment to call again, as the server wants it (UTC). */
    callbackOf(form) {
        let when;
        if (form.wait === "custom") {
            when = form.custom ? DateTime.fromISO(form.custom) : null;
        } else if (form.wait === "tomorrow") {
            when = DateTime.now().plus({ days: 1 }).set({ hour: 10, minute: 0, second: 0, millisecond: 0 });
        } else {
            when = DateTime.now().plus({ minutes: Number(form.wait) });
        }
        return when && when.isValid ? serializeDateTime(when) : false;
    }

    async saveAttempt() {
        const f = this.state.form;
        const callback = this.callbackOf(f);
        if (!callback) {
            this.notification.add(_t("Choose when to call again."), { type: "warning" });
            return;
        }
        await this.act("log_call", [[this.state.ticket.id], f.response], { remarks: f.remarks, callback_at: callback, also: this.alsoIds },
                       _t("Attempt saved. The ticket comes back when it is time."));
    }

    async saveAnswer() {
        const f = this.state.form;
        if ((f.answer || "").trim().length < 3) {
            this.notification.add(_t("Write what the doctor said."), { type: "warning" });
            return;
        }
        const name = this.state.ticket.name;
        const ok = await this.act("action_answer", [[this.state.ticket.id], f.outcome, f.answer],
                                  { channel: f.channel, tell_production: f.tell, also: this.alsoIds },
                                  _t("%s closed. The order moves on.", name));
        if (ok && this.state.ticket && this.state.ticket.state === "closed") {
            this.state.tab = "closed";
        }
    }

    saveUnreached() {
        const f = this.state.form;
        return this.act("action_answer", [[this.state.ticket.id], "unreached", f.answer], { also: this.alsoIds },
                        _t("Closed without the doctor's answer."));
    }

    saveField() {
        return this.act("action_hand_to_field", [[this.state.ticket.id]], { note: this.state.form.note },
                        _t("Handed over, to be asked at the clinic."));
    }

    saveEscalation() {
        return this.act("action_escalate", [[this.state.ticket.id]], { note: this.state.form.note }, _t("Escalated to the desk lead."));
    }

    saveWithdraw() {
        return this.act("action_withdraw", [[this.state.ticket.id]], { reason: this.state.form.note }, _t("Withdrawn."));
    }

    saveAssign() {
        const user = Number(this.state.form.user);
        if (!user) {
            return;
        }
        return this.act("action_assign", [[this.state.ticket.id], user], {}, _t("Given over."));
    }

    // ------------------------------------------------------------------ reaching the doctor
    async copyNumber() {
        try {
            await navigator.clipboard.writeText(this.state.ticket.phone);
            this.notification.add(_t("Number copied."), { type: "success" });
        } catch {
            this.notification.add(this.state.ticket.phone, { type: "info" });
        }
    }

    hourHeight(hour) {
        const top = Math.max(...this.state.ticket.habits.hours.map((h) => h.tried), 1);
        return Math.round((hour.tried / top) * 100);
    }

    hourAnswered(hour) {
        return hour.tried ? Math.round((hour.answered / hour.tried) * 100) : 0;
    }

    openOrder() {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "sale.order", res_id: this.state.ticket.order_id,
                               views: [[false, "form"]], target: "current" });
    }

    openTicketForm() {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "lab.doctor.ticket", res_id: this.state.ticket.id,
                               views: [[false, "form"]], target: "current" });
    }

    openList() {
        this.action.doAction("lab_doctor_desk.action_doctor_tickets");
    }

    keyboard(ev) {
        if (ev.key === "Escape") {
            if (this.state.form) {
                this.state.form = null;
            } else {
                this.close();
            }
            return;
        }
        if (ev.target && /INPUT|TEXTAREA|SELECT/.test(ev.target.tagName)) {
            return;
        }
        if (ev.key === "/") {
            const input = document.querySelector(".o_ddk_search input");
            if (input) {
                ev.preventDefault();
                input.focus();
            }
        }
    }
}

registry.category("actions").add("lab_doctor_desk", DoctorDesk);
