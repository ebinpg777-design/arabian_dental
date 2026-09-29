/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { money } from "../common/utils";

/**
 * Journal entries typed like a spreadsheet. Tab across, Enter for a new line,
 * paste rows straight from Excel (account, label, debit, credit), one button to
 * balance, templates that split an amount by share, and the checks run before
 * anything is posted.
 */
export class EntryStudio extends Component {
    static template = "ebshel_account_advanced.EntryStudio";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({
            setup: null, journal_id: null, date: null, ref: "", lines: [], template_id: null, template_amount: "",
            ac: null, problems: [], busy: false, saved: null, templateName: "",
        });
        this.money = (v) => money(v, this.state.setup && this.state.setup.currency);
        onWillStart(async () => {
            this.state.setup = await this.orm.call("ebshel.entry.studio", "get_setup", []);
            this.state.journal_id = this.state.setup.default_journal;
            this.state.date = this.state.setup.today;
            this.reset();
        });
    }

    blank() {
        return { account_id: null, account: "", partner_id: null, partner: "", label: "", analytic_account_id: null, analytic: "", debit: "", credit: "" };
    }
    reset() {
        this.state.lines = [this.blank(), this.blank()];
        this.state.ref = "";
        this.state.problems = [];
        this.state.saved = null;
        this.state.template_id = null;
    }
    addLine(i) {
        this.state.lines.splice(i === undefined ? this.state.lines.length : i + 1, 0, this.blank());
    }
    removeLine(i) {
        this.state.lines.splice(i, 1);
        if (!this.state.lines.length) {
            this.addLine();
        }
    }
    get totals() {
        const d = this.state.lines.reduce((t, l) => t + (Number(l.debit) || 0), 0);
        const c = this.state.lines.reduce((t, l) => t + (Number(l.credit) || 0), 0);
        return { debit: d, credit: c, diff: Math.round((d - c) * 100) / 100 };
    }
    get canPost() {
        const t = this.totals;
        return this.state.lines.some((l) => l.account_id) && Math.abs(t.diff) < 0.005 && t.debit > 0;
    }
    sideEntered(line, side) {
        if (side === "debit" && Number(line.debit)) {
            line.credit = "";
        } else if (side === "credit" && Number(line.credit)) {
            line.debit = "";
        }
        this.state.problems = [];
    }
    balance() {
        const t = this.totals;
        if (Math.abs(t.diff) < 0.005) {
            return;
        }
        let target = this.state.lines.find((l) => !Number(l.debit) && !Number(l.credit));
        if (!target) {
            this.addLine();
            target = this.state.lines[this.state.lines.length - 1];
        }
        if (t.diff > 0) {
            target.credit = t.diff.toFixed(2);
        } else {
            target.debit = (-t.diff).toFixed(2);
        }
    }
    onKey(ev, i) {
        if (ev.key === "Enter" && !this.state.ac) {
            ev.preventDefault();
            this.addLine(i);
            setTimeout(() => {
                const row = ev.target.closest("tr");
                const next = row && row.nextElementSibling && row.nextElementSibling.querySelector("input");
                if (next) {
                    next.focus();
                }
            }, 0);
        }
    }

    // ------------------------------------------------------------ autocomplete
    async lookup(model, line, field, query) {
        line[field] = query;
        line[field + "_id"] = null;
        const results = await this.orm.call("ebshel.entry.studio", "find", [model, query]);
        this.state.ac = { line, field, results };
    }
    pick(r) {
        const { line, field } = this.state.ac;
        line[field] = r.name;
        line[field + "_id"] = r.id;
        this.state.ac = null;
    }
    closeAc() {
        setTimeout(() => (this.state.ac = null), 150);
    }

    // ------------------------------------------------------------ paste
    async onPaste(ev) {
        const text = (ev.clipboardData || window.clipboardData).getData("text");
        if (!text || !text.includes("\t") && !text.includes("\n")) {
            return;
        }
        ev.preventDefault();
        const rows = text.split(/\r?\n/).filter((r) => r.trim()).map((r) => r.split("\t").map((c) => c.trim()));
        const codes = rows.map((r) => r[0]).filter(Boolean);
        const found = await this.orm.call("ebshel.entry.studio", "resolve_accounts", [codes]);
        let missing = 0;
        for (const r of rows) {
            const acc = found[r[0]];
            if (!acc) {
                missing += 1;
            }
            const nums = r.slice(1).filter((c) => /^-?[\d,]+(\.\d+)?$/.test(c.replace(/\s/g, "")));
            const label = r.slice(1).find((c) => c && !/^-?[\d,]+(\.\d+)?$/.test(c.replace(/\s/g, ""))) || "";
            const val = (s) => Number(String(s || "").replace(/,/g, "")) || 0;
            let debit = "", credit = "";
            if (nums.length >= 2) {
                debit = val(nums[0]) ? val(nums[0]).toFixed(2) : "";
                credit = val(nums[1]) ? val(nums[1]).toFixed(2) : "";
            } else if (nums.length === 1) {
                const n = val(nums[0]);
                if (n >= 0) {
                    debit = n.toFixed(2);
                } else {
                    credit = (-n).toFixed(2);
                }
            }
            this.state.lines.push({ ...this.blank(), account_id: acc ? acc.id : null, account: acc ? acc.name : r[0], label, debit, credit });
        }
        this.state.lines = this.state.lines.filter((l) => l.account_id || l.account || Number(l.debit) || Number(l.credit));
        if (missing) {
            this.notification.add(`${missing} account(s) were not found; pick them from the list.`, { type: "warning" });
        }
    }

    // ------------------------------------------------------------ templates
    async loadTemplate() {
        if (!this.state.template_id) {
            return;
        }
        const tpl = await this.orm.call("ebshel.entry.studio", "load_template", [this.state.template_id],
                                        { amount: Number(this.state.template_amount) || 0 });
        this.state.lines = tpl.lines.map((l) => ({ ...this.blank(), ...l, debit: l.debit ? l.debit.toFixed(2) : "", credit: l.credit ? l.credit.toFixed(2) : "" }));
        if (tpl.journal_id) {
            this.state.journal_id = tpl.journal_id;
        }
        if (tpl.ref && !this.state.ref) {
            this.state.ref = tpl.ref;
        }
    }
    async saveTemplate() {
        const name = (this.state.templateName || "").trim();
        if (!name) {
            this.notification.add("Give the template a name.", { type: "warning" });
            return;
        }
        const tpl = await this.orm.call("ebshel.entry.studio", "save_template", [name, this.vals()]);
        this.state.setup.templates.push({ id: tpl.id, name: tpl.name, journal_id: this.state.journal_id, lines: this.state.lines.length, uses_share: true });
        this.state.templateName = "";
        this.notification.add(`Template "${tpl.name}" saved.`, { type: "success" });
    }

    // ------------------------------------------------------------ save / post
    vals() {
        return {
            journal_id: this.state.journal_id, date: this.state.date, ref: this.state.ref, template_id: this.state.template_id,
            lines: this.state.lines.filter((l) => l.account_id || Number(l.debit) || Number(l.credit)).map((l) => ({
                account_id: l.account_id, partner_id: l.partner_id, label: l.label, analytic_account_id: l.analytic_account_id,
                debit: Number(l.debit) || 0, credit: Number(l.credit) || 0 })),
        };
    }
    async check() {
        const res = await this.orm.call("ebshel.entry.studio", "check", [this.vals()]);
        this.state.problems = res.problems;
        return !res.problems.length;
    }
    async save(post) {
        if (!(await this.check())) {
            return;
        }
        this.state.busy = true;
        try {
            const res = await this.orm.call("ebshel.entry.studio", "create_entry", [this.vals()], { post });
            this.state.saved = res;
            this.state.setup.recent.unshift({ id: res.id, name: res.name, date: this.state.date, ref: this.state.ref, amount: this.totals.debit, state: res.state });
            this.notification.add(post ? `${res.name} posted.` : `${res.name} saved as a draft.`, { type: "success" });
            const keepJournal = this.state.journal_id, keepDate = this.state.date;
            this.reset();
            this.state.journal_id = keepJournal;
            this.state.date = keepDate;
        } finally {
            this.state.busy = false;
        }
    }
    openMove(id) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "account.move", res_id: id, views: [[false, "form"]] });
    }
}

registry.category("actions").add("ebshel_entry_studio", EntryStudio);
