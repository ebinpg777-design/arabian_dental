/** @odoo-module **/

import { Component, onMounted, onPatched, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { formatMonetary } from "@web/views/fields/formatters";

const LIVE_MS = 60000;

/**
 * The shared machinery of the three role desks.
 *
 * Every desk is the same shape: one payload call, panels rendered from it, and a
 * handful of actions that write through lab.daily.update's own guarded methods —
 * so a desk can never do what its owner's role could not do from the form. The
 * differences (what is on screen, how live it is) live in the subclasses.
 */
class DeskBase extends Component {
    static props = ["*"];
    static payloadMethod = null;
    static live = false;
    /** Which guarded approval this desk's cards and bulk bar call. */
    static approveMethod = "action_ops_approve";

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({
            data: null, loading: true, busy: false,
            day: null,                 // ops desk time travel
            sendback: null,            // {id, reason} while the inline box is open
            remarks: {},               // marketing: sheet id -> remark being typed
            openWhy: {},               // sheet id -> anomaly reasons unfolded
            // Triage: the queue is 40 cards on a Monday, and a manager works it
            // by person, by exception or by what is about to breach - never by
            // reading it top to bottom.
            search: "", lens: "all", sort: "age",
            picked: {},                // sheet id -> chosen for a bulk action
            cursor: 0,                 // keyboard position in the visible queue
            keys: false,               // the shortcut card is open
            nudging: null,             // {id, text} while the nudge box is open
            rescuing: null,            // {partner_id, name, user_id} planner open
            updatedAt: "",             // when the payload was last fetched
            introClosed: false,        // re-render hook for dismissIntro
            showSystem: false,         // Command Center: the plumbing drawer
        });
        onWillStart(() => this.load());
        this.onKey = (ev) => this.handleKey(ev);
        // Fold a panel by clicking its title - remembered per browser, so a
        // manager who never reads the ticker stops paying for it in scroll.
        // DOM-level on purpose: ~25 panels across three desks, one delegate.
        this.onFoldClick = (ev) => {
            const head = ev.target.closest(".o_fwd_panel_head");
            if (!head || !head.closest(".o_fwd")
                || ev.target.closest("button, a, input, select")) {
                return;
            }
            const panel = head.closest(".o_fwd_panel");
            panel.classList.toggle("o_fwd_folded");
            this._saveFold(head, panel.classList.contains("o_fwd_folded"));
        };
        onMounted(() => {
            document.addEventListener("keydown", this.onKey);
            document.addEventListener("click", this.onFoldClick);
            this._applyFolds();
        });
        onPatched(() => this._applyFolds());
        onWillUnmount(() => {
            document.removeEventListener("keydown", this.onKey);
            document.removeEventListener("click", this.onFoldClick);
        });
        if (this.constructor.live) {
            // Live only while somebody is looking - a wall board on a sleeping
            // tab must not poll all day for nobody. (ported from the old tower)
            this.tick = () => {
                if (!document.hidden && !this.state.day) {
                    this.load(true);
                }
            };
            this.timer = setInterval(this.tick, LIVE_MS);
            document.addEventListener("visibilitychange", this.tick);
            onWillUnmount(() => {
                clearInterval(this.timer);
                document.removeEventListener("visibilitychange", this.tick);
            });
        }
    }

    async load(quiet = false) {
        if (!quiet) {
            this.state.loading = true;
        }
        const args = this.state.day ? [this.state.day] : [];
        this.state.data = await this.orm.call(
            "lab.desk", this.constructor.payloadMethod, args);
        this.state.loading = false;
        // Trust needs a timestamp: a desk that says WHEN it last looked never
        // gets second-guessed against a phone call.
        this.state.updatedAt = new Date().toLocaleTimeString(
            [], { hour: "2-digit", minute: "2-digit" });
    }

    // ------------------------------------------------------- the field board
    get statusLabel() {
        return { at_clinic: "at a clinic", moving: "on the road",
                 finished: "day finished", idle: "not started",
                 // On duty with nothing booked is a real morning, and a different one
                 // from being off; "not set up" is HR's job, not the executive's.
                 on_duty: "on duty", visits_done: "visits done",
                 no_setup: "not set up", off: "off today" };
    }

    /** The day as plain text on the clipboard - the lab runs on WhatsApp,
     *  and a summary someone can paste gets shared; a screen never does. */
    async copyDaySummary() {
        const d = this.state.data;
        const lines = [
            `Field work — ${d.date_label}`,
            `Visits: ${d.totals.done}/${d.totals.planned} done`,
            `Cases: ${d.totals.cases} (${this.money(d.totals.value)})`,
            `Collected: ${this.money(d.totals.collected)}`,
            `Distance: ${Math.round(d.totals.distance)} km`,
        ];
        if (d.queue) {
            lines.push(`Waiting for approval: ${d.queue.length}`);
        }
        for (const row of d.rows) {
            lines.push(`- ${row.name}: ${row.done}/${row.planned} visits, ` +
                       `${this.money(row.collected)} collected`);
        }
        await browser.navigator.clipboard.writeText(lines.join("\n"));
        this.notification.add("Day summary copied. Paste it anywhere.",
                              { type: "success" });
    }

    // ------------------------------------------------------------- folding
    get _foldKey() {
        return "lab_fwd.folds." + this.constructor.payloadMethod;
    }

    _folds() {
        try {
            return JSON.parse(browser.localStorage.getItem(this._foldKey)) || {};
        } catch {
            return {};
        }
    }

    _saveFold(head, folded) {
        const key = head.textContent.trim().slice(0, 40);
        const folds = this._folds();
        if (folded) {
            folds[key] = 1;
        } else {
            delete folds[key];
        }
        try {
            browser.localStorage.setItem(this._foldKey, JSON.stringify(folds));
        } catch {
            // private mode: folds last only as long as the page
        }
    }

    /** Re-renders rebuild the DOM, so remembered folds are re-applied after
     *  every patch - cheap, and idempotent. */
    _applyFolds() {
        const folds = this._folds();
        for (const head of document.querySelectorAll(
                ".o_fwd .o_fwd_panel_head")) {
            const key = head.textContent.trim().slice(0, 40);
            head.closest(".o_fwd_panel").classList.toggle(
                "o_fwd_folded", !!folds[key]);
        }
    }

    // ---------------------------------------------------------------- intro
    /** "How this desk works", three lines, shown until this browser closes it. */
    get introKey() {
        return "lab_fwd.intro." + this.constructor.payloadMethod;
    }

    get showIntro() {
        try {
            return !browser.localStorage.getItem(this.introKey);
        } catch {
            return false;
        }
    }

    dismissIntro() {
        try {
            browser.localStorage.setItem(this.introKey, "seen");
        } catch {
            // private mode: it will simply show again
        }
        this.state.introClosed = true;
    }

    // ------------------------------------------------------------- day travel
    /**
     * Pin any past day, on any desk. The windowed figures move there; the
     * queue stays live, because what is waiting to be signed is waiting now.
     * Ops additionally stops its live polling while pinned (see the tick).
     */
    pickDate(value) {
        const today = this.state.data && this.state.data.today;
        this.state.day = value && value !== today ? value : null;
        this.load();
    }

    shiftDay(delta) {
        if (!this.state.data) {
            return;
        }
        const [y, m, d] = this.state.data.date.split("-").map((n) => +n);
        const moved = new Date(y, m - 1, d + delta);
        const pad = (n) => String(n).padStart(2, "0");
        const iso = `${moved.getFullYear()}-${pad(moved.getMonth() + 1)}-${pad(moved.getDate())}`;
        if (iso > this.state.data.today) {
            return;                       // the future has no field work in it
        }
        this.pickDate(iso);
    }

    // ----------------------------------------------------------------- triage
    /**
     * The queue as this manager is currently looking at it.
     *
     * Client-side over the payload already loaded: filtering a 40-card queue is
     * a thing a manager does five times a minute while deciding what to sign,
     * and a round trip for each one turns the desk into a form.
     */
    get queue() {
        const rows = (this.state.data && this.state.data.queue) || [];
        const needle = this.state.search.trim().toLowerCase();
        const lens = this.state.lens;
        const seen = rows.filter((r) => {
            if (needle && !r.user.toLowerCase().includes(needle)
                && !r.date_label.toLowerCase().includes(needle)) {
                return false;
            }
            if (lens === "exceptions") { return !r.clean; }
            if (lens === "clean") { return r.clean; }
            if (lens === "overdue") { return r.overdue; }
            if (lens === "asked") { return r.nudges > 0; }
            return true;
        });
        const by = {
            // Oldest first is the default because the SLA is what bites.
            age: (a, b) => b.age_h - a.age_h,
            value: (a, b) => (b.value || 0) - (a.value || 0),
            flags: (a, b) => b.flags.length - a.flags.length || b.age_h - a.age_h,
            person: (a, b) => a.user.localeCompare(b.user) || b.age_h - a.age_h,
        };
        return [...seen].sort(by[this.state.sort] || by.age);
    }

    /** The id under the keyboard cursor. A getter of its own because `queue`
     *  filters and sorts on every read, and the card template asks once per
     *  card - which would be the whole queue re-sorted forty times a render. */
    get cursorId() {
        const row = this.queue[this.state.cursor];
        return row ? row.id : null;
    }

    get lenses() {
        const rows = (this.state.data && this.state.data.queue) || [];
        return [
            { key: "all", label: "All", n: rows.length },
            { key: "exceptions", label: "With issues",
              n: rows.filter((r) => !r.clean).length },
            { key: "clean", label: "Clean", n: rows.filter((r) => r.clean).length },
            { key: "overdue", label: "Late",
              n: rows.filter((r) => r.overdue).length },
            { key: "asked", label: "Asked",
              n: rows.filter((r) => r.nudges > 0).length },
        ];
    }

    setLens(key) {
        this.state.lens = key;
        this.state.cursor = 0;
    }

    // ------------------------------------------------------------------ bulk
    get pickedIds() {
        return this.queue.filter((r) => this.state.picked[r.id]).map((r) => r.id);
    }

    togglePick(row) {
        this.state.picked[row.id] = !this.state.picked[row.id];
    }

    pickAllVisible() {
        const rows = this.queue;
        const all = rows.every((r) => this.state.picked[r.id]);
        for (const r of rows) {
            this.state.picked[r.id] = !all;
        }
    }

    /** Sign everything ticked, in one call, through the same guarded action. */
    async approvePicked() {
        // Only clean days are approved in bulk; a day with issues needs its note.
        const picked = this.queue.filter((r) => this.state.picked[r.id]);
        const ids = picked.filter((r) => r.clean).map((r) => r.id);
        const skipped = picked.length - ids.length;
        if (skipped) {
            this.notification.add(
                `${skipped} day sheet(s) with issues were left - mark them checked with a note.`,
                { type: "warning" });
        }
        if (!ids.length) {
            return;
        }
        const method = this.constructor.approveMethod;
        await this.act(method, ids);
        this.state.picked = {};
        this.notification.add(`${ids.length} day sheet(s) approved.`,
                              { type: "success" });
    }

    // -------------------------------------------------------------- keyboard
    /**
     * A manager signs forty of these on a Monday. j/k to walk, a to approve,
     * s to send back, n to ask, x to tick, o to open, ? for the card.
     *
     * Typing in a box is never a shortcut: the search field is right there, and
     * "a" landing as an approval while somebody types a name would be the worst
     * bug on this screen.
     */
    handleKey(ev) {
        const tag = (ev.target && ev.target.tagName) || "";
        if (["INPUT", "TEXTAREA", "SELECT"].includes(tag) || ev.target.isContentEditable
            || ev.ctrlKey || ev.metaKey || ev.altKey || this.state.busy) {
            return;
        }
        const rows = this.queue;
        const row = rows[this.state.cursor];
        const move = (delta) => {
            if (!rows.length) {
                return;
            }
            this.state.cursor = Math.min(rows.length - 1,
                                         Math.max(0, this.state.cursor + delta));
            this.scrollToCursor();
        };
        switch (ev.key) {
            case "j": case "ArrowDown": move(1); break;
            case "k": case "ArrowUp": move(-1); break;
            case "a": if (row) { this.approveRow(row); } break;
            case "s": if (row) { this.startSendBack(row); } break;
            case "n": if (row) { this.startNudge(row); } break;
            case "x": if (row) { this.togglePick(row); } break;
            case "o": if (row) { this.openSheet(row.id); } break;
            case "?": this.state.keys = !this.state.keys; break;
            case "Escape":
                this.state.keys = false;
                this.state.sendback = null;
                this.state.nudging = null;
                return;               // no preventDefault: Escape is the client's
            default: return;
        }
        ev.preventDefault();
    }

    scrollToCursor() {
        const row = this.queue[this.state.cursor];
        if (!row) {
            return;
        }
        // After the state settles, or the card being scrolled to is the old one.
        requestAnimationFrame(() => {
            const el = document.querySelector(`[data-sheet="${row.id}"]`);
            if (el) {
                el.scrollIntoView({ block: "nearest", behavior: "smooth" });
            }
        });
    }

    /** Whichever approval this desk signs — the subclass says which. */
    approveRow(row) {
        // A day with issues is marked checked with a note, never approved.
        if (!row.clean) {
            return this.markChecked(row);
        }
        return this.constructor.approveMethod === "action_marketing_approve"
            ? this.approveMarketing(row) : this.approveOps(row);
    }

    /**
     * Checked, not approved: the day had issues. One press, no note needed -
     * the next desk and the administrator see it was read. (client, 2026-09-15)
     */
    async markChecked(row) {
        const marketing = this.constructor.approveMethod === "action_marketing_approve";
        const remark = (this.state.remarks[row.id] || "").trim();
        if (marketing && remark) {
            await this.orm.write("lab.daily.update", [row.id],
                                 { marketing_remark: remark });
        }
        await this.act(marketing ? "action_marketing_check" : "action_ops_check", [row.id]);
        this.notification.add(`Checked, issues noted: ${row.user} — ${row.date_label}.`,
                              { type: "success" });
    }

    // ------------------------------------------------------------ formatting
    money(value) {
        return formatMonetary(value || 0, {
            currencyId: this.state.data && this.state.data.currency_id,
        });
    }

    /** Points for an inline SVG sparkline, normalised into a w×h box. */
    spark(values, w = 120, h = 28) {
        const vals = (values || []).map((v) => v || 0);
        if (!vals.length) {
            return "";
        }
        const max = Math.max(...vals, 1);
        const step = vals.length > 1 ? w / (vals.length - 1) : 0;
        return vals
            .map((v, i) => `${(i * step).toFixed(1)},${(h - 2 - (v / max) * (h - 4)).toFixed(1)}`)
            .join(" ");
    }

    /** A conic-gradient string for the outcome donut. */
    donut(slices) {
        const palette = ["#0e7c86", "#4f46e5", "#d99b00", "#0f9d58",
                        "#dc2626", "#6c757d", "#7c3aed", "#0284c7"];
        let at = 0;
        const stops = (slices || []).map((s, i) => {
            const from = at;
            at += s.pct;
            return `${palette[i % palette.length]} ${from}% ${at}%`;
        });
        stops.push(`#e9ecef ${at}% 100%`);
        return `conic-gradient(${stops.join(", ")})`;
    }

    donutColor(i) {
        const palette = ["#0e7c86", "#4f46e5", "#d99b00", "#0f9d58",
                        "#dc2626", "#6c757d", "#7c3aed", "#0284c7"];
        return palette[i % palette.length];
    }

    urgencyStyle(row) {
        const pct = Math.round((row.urgency || 0) * 100);
        const colour = row.overdue ? "#dc2626" : pct > 66 ? "#d99b00" : "#0f9d58";
        return `width:${Math.max(pct, 4)}%; background:${colour};`;
    }

    /** The days the desks could not sign, as a list. */
    async openFlags() {
        const action = await this.orm.call("lab.desk", "action_open_flags", []);
        this.action.doAction(action);
    }

    // ------------------------------------------------------------ navigation
    openSheet(id) {
        this.action.doAction({
            type: "ir.actions.act_window", res_model: "lab.daily.update",
            res_id: id, views: [[false, "form"]],
        });
    }

    openList(model, name, ids, domain) {
        this.action.doAction({
            type: "ir.actions.act_window", res_model: model, name,
            views: [[false, "list"], [false, "form"]],
            domain: ids && ids.length ? [["id", "in", ids]] : domain || [],
        });
    }

    /**
     * The whole set behind a panel that shows a top handful.
     *
     * Resolved on the server (`action_see_all`), not by rebuilding the domain
     * here: the panel's rows come from the countback, the live delivery set or
     * a neglected-clinic scan, and a lookalike domain in the client would
     * drift from them the first time either changed. (client, 2026-09-02)
     */
    async seeAll(kind) {
        const action = await this.orm.call(
            "lab.desk",
            kind === "receivables" ? "action_see_all_receivables" : "action_see_all",
            kind === "receivables" ? [] : [kind, this.state.day || false]
        );
        this.action.doAction(action);
    }

    openRecord(model, id) {
        this.action.doAction({
            type: "ir.actions.act_window", res_model: model, res_id: id,
            views: [[false, "form"]],
        });
    }

    // ------------------------------------------------------------- the writes
    async act(method, ids, extra = {}) {
        if (this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            await this.orm.call("lab.daily.update", method, [ids], extra);
        } finally {
            this.state.busy = false;
        }
        await this.load(true);
    }

    async approveOps(row) {
        await this.act("action_ops_approve", [row.id]);
        this.notification.add(`Approved: ${row.user} — ${row.date_label}.`,
                              { type: "success" });
    }

    async approveMarketing(row) {
        const remark = (this.state.remarks[row.id] || "").trim();
        if (remark) {
            await this.orm.write("lab.daily.update", [row.id],
                                 { marketing_remark: remark });
        }
        await this.act("action_marketing_approve", [row.id]);
        this.notification.add(`Approved: ${row.user} — ${row.date_label}.`,
                              { type: "success" });
    }

    async approveAllClean() {
        const clean = (this.state.data.queue || [])
            .filter((q) => q.clean).map((q) => q.id);
        if (!clean.length) {
            return;
        }
        await this.act("action_approve_clean", clean);
    }

    /** Stars write the score immediately - judgement is one tap, not a form.
     *  A LOW score asks for a written reason first: a 1-star with no words
     *  helps nobody improve, and the remark box is right there. */
    async score(row, n) {
        if (n <= 2 && !(this.state.remarks[row.id] || "").trim()) {
            this.notification.add(
                "Please write a short remark first - a low score needs a reason.",
                { type: "warning" });
            return;
        }
        const remark = (this.state.remarks[row.id] || "").trim();
        await this.orm.write("lab.daily.update", [row.id], {
            marketing_score: String(n),
            ...(remark ? { marketing_remark: remark } : {}),
        });
        row.score = String(n);
    }

    startSendBack(row) {
        this.state.sendback = { id: row.id, reason: "" };
    }

    async confirmSendBack() {
        const { id, reason } = this.state.sendback || {};
        if (!id || !reason.trim()) {
            this.notification.add("Please write what is wrong before sending it back.",
                                  { type: "warning" });
            return;
        }
        await this.orm.write("lab.daily.update", [id],
                             { send_back_reason: reason.trim() });
        await this.act("action_send_back", [id]);
        this.state.sendback = null;
    }

    toggleWhy(row) {
        this.state.openWhy[row.id] = !this.state.openWhy[row.id];
    }

    // ---------------------------------------------------- travel, in place
    /** Sign a claim without leaving the desk - through lab.trip's own
     *  guarded action, so the desk can never sign what the role could not. */
    async approveTrip(row) {
        if (this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            await this.orm.call("lab.trip", "action_approve", [[row.id]]);
        } finally {
            this.state.busy = false;
        }
        await this.load(true);
        this.notification.add(`${row.user} — ${row.km} km approved.`,
                              { type: "success" });
    }

    async rejectTrip(row) {
        if (this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            await this.orm.call("lab.trip", "action_reject", [[row.id]]);
        } finally {
            this.state.busy = false;
        }
        await this.load(true);
        this.notification.add(`Sent back to ${row.user} for correction.`,
                              { type: "info" });
    }

    startNudge(row) {
        this.state.nudging = { id: row.id, text: "" };
    }

    /**
     * Ask about a day without rejecting it. The sheet stays on the desk — which
     * is the difference between this and Send back, and the reason the question
     * gets asked at all instead of being left in a WhatsApp thread.
     */
    async confirmNudge() {
        const { id, text } = this.state.nudging || {};
        if (!id) {
            return;
        }
        await this.act("action_nudge", [id], { message: text.trim() });
        this.state.nudging = null;
        this.notification.add("Question sent. The day sheet stays on your desk.",
                              { type: "info" });
    }

    /** A donut slice is a question; the visits behind it are the answer.
     *
     * On the BASE, not on the marketing desk: DeskOutcomePanel is called by the
     * Command Center too, and there `this.openOutcome` did not exist - clicking a
     * legend row on that desk threw "v1.openOutcome is not a function" and put the
     * Oops dialog over the screen. A handler in a shared sub-template belongs to
     * every component that renders it. (client, 2026-09-09)
     */
    openOutcome(slice) {
        const to = this.state.data.date;
        const [y, m, d] = to.split("-").map((n) => +n);
        const from = new Date(y, m - 1, d - 7);
        const pad = (n) => String(n).padStart(2, "0");
        const fromIso = `${from.getFullYear()}-${pad(from.getMonth() + 1)}-${pad(from.getDate())}`;
        this.openList("lab.visit", `${slice.label} — last 7 days`, null, [
            ["date", ">", fromIso], ["date", "<=", to],
            ["state", "=", "done"],
            ["outcome", "=", slice.key === "none" ? false : slice.key],
        ]);
    }

    openAlert(alert) {
        if (alert.ids && alert.ids.length) {
            this.openList(alert.model, alert.title, alert.ids);
        } else if (alert.domain && alert.domain.length) {
            this.openList(alert.model, alert.title, null, alert.domain);
        } else {
            this.openList(alert.model, alert.title, null, []);
        }
    }
}

/** The Operational Manager's morning: sign what happened, watch the field. */
export class LabOpsDesk extends DeskBase {
    static template = "lab_fieldwork.OpsDesk";
    static payloadMethod = "get_ops_desk";
    static live = true;


    /** The fortnight strip toggles: clicking the pinned day unpins it. */
    pickDay(day) {
        this.pickDate(day === this.state.data.date ? null : day);
    }

    /** Where this person's cash is. With a float: its collections and
        handovers, the custody ledger. Without one: the visits it came from. */
    openCashHeld(row) {
        if (row.allocation_id) {
            return this.openList("petty.cash.transaction", `Cash held — ${row.user}`, null,
                [["allocation_id", "=", row.allocation_id],
                 ["type", "in", ["collection", "handover"]]]);
        }
        this.openList("lab.visit", `Cash held — ${row.user}`, row.ids);
    }

    openPendingHandovers(row) {
        this.openList("lab.cash.handover", `Waiting to be counted — ${row.user}`, null,
            [["user_id", "=", row.user_id], ["state", "=", "declared"]]);
    }

    openTrips() {
        this.openList("lab.trip", "Travel claims waiting", null,
                      [["state", "=", "closed"]]);
    }

    openSentBack() {
        this.openList("lab.daily.update", "Sent back, being fixed", null,
                      [["state", "=", "sent_back"]]);
    }
}

/** The Marketing Manager's morning: judge the days, see where to push. */
export class LabMarketingDesk extends DeskBase {
    static template = "lab_fieldwork.MarketingDesk";
    static payloadMethod = "get_marketing_desk";
    static approveMethod = "action_marketing_approve";

    openNewClinics() {
        this.action.doAction({
            type: "ir.actions.client", tag: "lab_new_clinics",
        });
    }

    openCoverage(row) {
        this.openRecord("lab.coverage", row.id);
    }

    openTarget(t) {
        this.openRecord("lab.target", t.id);
    }

    /** Footer total for the going-quiet table. */
    get rescueOwedTotal() {
        return ((this.state.data && this.state.data.rescue) || [])
            .reduce((sum, clinic) => sum + (clinic.outstanding || 0), 0);
    }

    // ------------------------------------------------------ rescue planner
    /** "Slipping away" was a list to worry about; this makes it a list to act
     *  on: pick an executive, and tomorrow's visit exists. */
    startRescue(row) {
        const first = (this.state.data.executives || [])[0];
        this.state.rescuing = row.partner_id === (this.state.rescuing || {}).partner_id
            ? null
            : { partner_id: row.partner_id, name: row.name,
                user_id: first ? first.id : false };
    }

    async confirmRescue() {
        const plan = this.state.rescuing;
        if (!plan || !plan.user_id || this.state.busy) {
            return;
        }
        const tomorrow = new Date(Date.now() + 24 * 3600 * 1000);
        const pad = (n) => String(n).padStart(2, "0");
        const date = `${tomorrow.getFullYear()}-${pad(tomorrow.getMonth() + 1)}-${pad(tomorrow.getDate())}`;
        this.state.busy = true;
        try {
            // A plain create, so the visit obeys exactly the ACLs and record
            // rules any planned visit obeys.
            await this.orm.create("lab.visit", [{
                partner_id: plan.partner_id,
                user_id: +plan.user_id,
                date,
                purpose: "order",
                note: "[rescue] planned from the marketing desk",
            }]);
        } finally {
            this.state.busy = false;
        }
        this.state.rescuing = null;
        this.notification.add(`Rescue visit planned for tomorrow at ${plan.name}.`,
                              { type: "success" });
    }
}

/** The Administrator's morning: is the machine running, and is the rule right. */
export class LabAdminDesk extends DeskBase {
    static template = "lab_fieldwork.AdminDesk";
    static payloadMethod = "get_admin_desk";

    openWeekly() {
        const last = this.state.data.last_report;
        if (last) {
            this.openRecord("lab.weekly.report", last.id);
        }
    }

    openGap(gap) {
        this.openAlert(gap);
    }

    openSettings() {
        this.action.doAction("lab_fieldwork.action_fieldwork_settings_own");
    }

    // ------------------------------------------------------------- charts
    /** Bar height for the filed-vs-approved pairs, scaled to the busiest day. */
    flowHeight(n) {
        const peak = Math.max(1, ...this.state.data.flow.map((d) => d.filed));
        return Math.max(3, Math.round((n * 44) / peak));
    }

    /** Polyline points for one desk's six-week approval-speed line. */
    speedPoints(key) {
        const rows = this.state.data.speed_trend || [];
        if (!rows.length) {
            return "";
        }
        const peak = Math.max(1, ...rows.map((w) => Math.max(w.ops, w.mkt)));
        const step = rows.length > 1 ? 240 / (rows.length - 1) : 0;
        return rows
            .map((w, i) => `${(i * step).toFixed(1)},${(56 - (w[key] * 52) / peak).toFixed(1)}`)
            .join(" ");
    }

    get speedFirstLabel() {
        const rows = this.state.data.speed_trend || [];
        return rows.length ? rows[0].label : "";
    }

    get speedLastLabel() {
        const rows = this.state.data.speed_trend || [];
        return rows.length ? rows[rows.length - 1].label : "";
    }

    /** The clinic's open items, from the countback - never the residual
     *  report, or the list holds different money than the number clicked. */
    async openDebtor(debtor) {
        const act = await this.orm.call(
            "lab.collection.performance", "action_open_items_for_partner",
            [debtor.id]);
        this.action.doAction(act);
    }

    openWeeklyList() {
        this.action.doAction("lab_fieldwork.action_weekly_report");
    }

    openHealth(check) {
        if (check.ids && check.ids.length && check.model) {
            this.openList(check.model, check.text, check.ids);
        }
    }

    openCovers() {
        this.action.doAction("lab_fieldwork.action_desk_cover");
    }

    /** Wednesday's picture in Wednesday's meeting: file Monday-to-now as an
     *  interim snapshot. The Monday cron still files the real week. */
    async fileWeekSoFar() {
        if (this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            const act = await this.orm.call(
                "lab.weekly.report", "action_file_week_so_far", []);
            await this.action.doAction(act);
        } finally {
            this.state.busy = false;
        }
    }

    funnelWidth(count) {
        const f = this.state.data.funnel;
        const top = Math.max(f.week_total, f.submitted + f.ops_approved
                             + f.approved_week + f.sent_back, 1);
        return Math.max(8, Math.round((count / top) * 100));
    }
}

registry.category("actions").add("lab_ops_desk", LabOpsDesk);
registry.category("actions").add("lab_marketing_desk", LabMarketingDesk);
registry.category("actions").add("lab_admin_desk", LabAdminDesk);
