/** @odoo-module **/

import { Component, onWillStart, useState, useExternalListener } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { formatDate, deserializeDate } from "@web/core/l10n/dates";
import { formatFloat } from "@web/core/utils/numbers";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";

/**
 * The statements on screen, from the wizard's filters. (client, 2026-09-30)
 *
 * Left: every partner the wizard selected, with what they owe and what is overdue.
 * Right: the chosen partner's statement exactly as it prints - opening, every line
 * with its running balance, the amount due and the ageing. Print, Excel and e-mail
 * work on the one partner shown or on the whole run, through the same engine and
 * the same data the PDF is made from.
 */
export class StatementView extends Component {
    static template = "epg_partner_statement.StatementView";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.dialog = useService("dialog");
        const params = (this.props.action && this.props.action.params) || {};
        this.partnerIds = params.partner_ids || [];
        this.data = params.data || {};
        this.routes = params.routes || [];
        this.state = useState({
            summary: null, loading: true, failed: null,
            current: null, statement: null, stLoading: false,
            q: "", show: "all", sort: "route", busy: null, mobileDetail: false,
        });
        useExternalListener(window, "keydown", (ev) => this.onKey(ev));
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        try {
            this.state.summary = await this.orm.call("epg.partner.statement", "view_summary", [this.partnerIds, this.data]);
            const first = this.visible.find((p) => p.has_data) || this.visible[0];
            if (first) {
                this.select(first, true);
            }
        } catch (error) {
            this.state.failed = (error.data && error.data.message) || error.message || String(error);
        } finally {
            this.state.loading = false;
        }
    }

    // ------------------------------------------------------------------ the list
    main(p) {
        return (p.blocks && p.blocks[0]) || null;
    }
    due(p) {
        const b = this.main(p);
        return b ? b.closing : 0;
    }
    overdue(p) {
        const b = this.main(p);
        return b ? b.overdue : 0;
    }
    get counts() {
        const all = (this.state.summary && this.state.summary.partners) || [];
        return {
            all: all.length,
            overdue: all.filter((p) => this.overdue(p) > 0.005).length,
            due: all.filter((p) => this.due(p) > 0.005).length,
            quiet: all.filter((p) => !p.has_data).length,
            noemail: all.filter((p) => !p.email).length,
        };
    }
    get visible() {
        const all = (this.state.summary && this.state.summary.partners) || [];
        const q = this.state.q.trim().toLowerCase();
        let rows = all.filter((p) => !q || p.name.toLowerCase().includes(q) || (p.route || "").toLowerCase().includes(q)
                                         || (p.city || "").toLowerCase().includes(q));
        const show = this.state.show;
        if (show === "overdue") {
            rows = rows.filter((p) => this.overdue(p) > 0.005);
        } else if (show === "due") {
            rows = rows.filter((p) => this.due(p) > 0.005);
        } else if (show === "quiet") {
            rows = rows.filter((p) => !p.has_data);
        } else if (show === "noemail") {
            rows = rows.filter((p) => !p.email);
        }
        const sort = this.state.sort;
        if (sort === "due") {
            rows = [...rows].sort((a, b) => this.due(b) - this.due(a));
        } else if (sort === "overdue") {
            rows = [...rows].sort((a, b) => this.overdue(b) - this.overdue(a));
        } else if (sort === "name") {
            rows = [...rows].sort((a, b) => a.name.localeCompare(b.name));
        }
        return rows;
    }
    /** Route headings, only while the list is in route order. */
    routeHead(p, index) {
        if (this.state.sort !== "route") {
            return "";
        }
        const prev = this.visible[index - 1];
        return !prev || prev.route !== p.route ? (p.route || _t("No route")) : "";
    }

    async select(p, quiet) {
        if (!p) {
            return;
        }
        this.state.current = p.id;
        this.state.mobileDetail = !quiet;
        this.state.stLoading = true;
        try {
            const st = await this.orm.call("epg.partner.statement", "view_statement", [p.id, this.data]);
            if (this.state.current === p.id) {
                this.state.statement = st;
            }
        } finally {
            if (this.state.current === p.id) {
                this.state.stLoading = false;
            }
        }
        const el = document.querySelector(`.o_esv_prow[data-id="${p.id}"]`);
        if (el && el.scrollIntoView) {
            el.scrollIntoView({ block: "nearest" });
        }
    }
    onKey(ev) {
        const tag = (ev.target && ev.target.tagName) || "";
        if (["INPUT", "TEXTAREA", "SELECT"].includes(tag) || !this.state.summary) {
            return;
        }
        if (!["ArrowDown", "ArrowUp", "j", "k"].includes(ev.key)) {
            return;
        }
        const rows = this.visible;
        const i = rows.findIndex((p) => p.id === this.state.current);
        const next = ev.key === "ArrowDown" || ev.key === "j" ? rows[i + 1] : rows[i - 1];
        if (next) {
            ev.preventDefault();
            this.select(next, true);
        }
    }

    // ------------------------------------------------------------------ formatting
    money(value, currency) {
        const c = currency || (this.state.summary && this.state.summary.totals[0] && this.state.summary.totals[0].currency) || {};
        const text = formatFloat(value || 0, { digits: [false, c.digits ?? 2] });
        if (!c.symbol) {
            return text;
        }
        return c.position === "before" ? `${c.symbol} ${text}` : `${text} ${c.symbol}`;
    }
    date(value) {
        return value ? formatDate(deserializeDate(value)) : "";
    }
    get periodText() {
        const s = this.state.summary;
        return s ? `${this.date(s.date_from)} – ${this.date(s.date_to)}` : "";
    }
    get filterText() {
        const s = this.state.summary, bits = [];
        if (!s) {
            return "";
        }
        if (this.routes.length) {
            bits.push(this.routes.join(", "));
        }
        if (s.open_items_only) {
            bits.push(_t("open items only"));
        }
        if (s.entered_from || s.entered_to) {
            bits.push(_t("entered %(from)s – %(to)s", { from: s.entered_from ? this.date(s.entered_from.slice(0, 10)) : "…", to: s.entered_to ? this.date(s.entered_to.slice(0, 10)) : "…" }));
        }
        return bits.join(" · ");
    }
    ageingBar(block) {
        const total = block.ageing.reduce((t, a) => t + Math.max(a[1], 0), 0);
        return block.ageing.map((a, i) => ({ label: a[0], amount: a[1], late: a[2], i, pct: total ? (Math.max(a[1], 0) / total) * 100 : 0 }));
    }

    // ------------------------------------------------------------------ actions
    ids(scope) {
        return scope === "one" ? [this.state.current] : this.visible.map((p) => p.id);
    }
    async run(key, fn) {
        if (this.state.busy) {
            return;
        }
        this.state.busy = key;
        try {
            await fn();
        } finally {
            this.state.busy = null;
        }
    }
    print(scope) {
        return this.run("print-" + scope, async () => {
            const action = await this.orm.call("epg.partner.statement", "view_print", [this.ids(scope), this.data]);
            await this.action.doAction(action);
        });
    }
    excel(scope) {
        return this.run("xlsx-" + scope, async () => {
            const action = await this.orm.call("epg.partner.statement", "view_xlsx", [this.ids(scope), this.data]);
            await this.action.doAction(action);
        });
    }
    email(scope) {
        const ids = this.ids(scope);
        const rows = (this.state.summary.partners || []).filter((p) => ids.includes(p.id));
        const withMail = rows.filter((p) => p.email).length;
        if (!withMail) {
            this.notification.add(_t("None of these partners has an e-mail address."), { type: "warning" });
            return;
        }
        const go = () => this.run("mail-" + scope, async () => {
            const res = await this.orm.call("epg.partner.statement", "view_email", [ids, this.data]);
            let message = _t("%s statement(s) e-mailed.", res.sent);
            if (res.skipped_count) {
                message += " " + _t("No e-mail address: %s.", res.skipped.join(", ") + (res.skipped_count > res.skipped.length ? " …" : ""));
            }
            this.notification.add(message, { type: res.sent ? "success" : "warning", sticky: !!res.skipped_count });
        });
        const text = scope === "one"
            ? _t("E-mail this statement (PDF attached) to %s?", rows[0] && rows[0].email)
            : _t("E-mail the statement (PDF attached) to the %(n)s partner(s) shown that have an e-mail address?", { n: withMail });
        this.dialog.add(ConfirmationDialog, {
            body: text, confirm: go, cancel: () => {},
        });
    }
    openMove(line) {
        if (line.move_id) {
            this.action.doAction({ type: "ir.actions.act_window", res_model: "account.move", res_id: line.move_id, views: [[false, "form"]] });
        }
    }
    openPartner() {
        if (this.state.current) {
            this.action.doAction({ type: "ir.actions.act_window", res_model: "res.partner", res_id: this.state.current, views: [[false, "form"]] });
        }
    }
    backToList() {
        this.state.mobileDetail = false;
    }
}

registry.category("actions").add("epg_statement_view", StatementView);
