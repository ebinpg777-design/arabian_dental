/** @odoo-module **/

import { Component, onWillStart, useState, onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { money } from "../common/utils";
import { splitRows, guessMapping, buildRows } from "./parse";

/**
 * Bank reconciliation in three tabs:
 *   Match   bank lines against invoices, bills, accounts and rules, with the best
 *           guess on every line and the keyboard (↑ ↓ Enter) to accept it.
 *   Tick    the book items the bank has seen, with the reconciliation statement.
 *   Paste   the statement itself: rows the books already hold are ticked; the rest
 *           become bank lines, and the automatic rules run on them.
 */
export class BankRec extends Component {
    static template = "ebshel_account_advanced.BankRec";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        const params = (this.props.action && this.props.action.params) || {};
        this.state = useState({
            overview: null, journalId: params.journal_id || null, tab: params.tab || "match",
            // match
            lines: null, lineState: "open", search: "", selectedId: null, detail: null, parts: [], partnerId: null,
            itemSearch: "", itemKind: "open", itemResults: [], busy: false, ruleOffer: null,
            // tick
            brs: null, brsDate: new Date().toISOString().slice(0, 10), brsShow: "uncleared", brsSearch: "", ticked: {},
            clearDate: new Date().toISOString().slice(0, 10), bankBalance: "", baselineDate: "",
            // paste
            pasteText: "", parsed: null, map: null, tickOnImport: true, importResult: null,
        });
        this.money = (v) => money(v, this.state.overview && this.state.overview.currency);
        this.onKey = this.onKey.bind(this);
        onWillStart(() => this.loadOverview());
        onMounted(() => window.addEventListener("keydown", this.onKey));
        onWillUnmount(() => window.removeEventListener("keydown", this.onKey));
    }

    // ------------------------------------------------------------ overview
    async loadOverview() {
        this.state.overview = await this.orm.call("ebshel.bank.desk", "get_journals", []);
        const js = this.state.overview.journals;
        if (!this.state.journalId && js.length) {
            this.state.journalId = js[0].id;
        }
        await this.loadTab();
    }
    get journal() {
        return (this.state.overview ? this.state.overview.journals : []).find((j) => j.id === this.state.journalId);
    }
    async pickJournal(id) {
        this.state.journalId = id;
        this.state.selectedId = null;
        this.state.detail = null;
        await this.loadTab();
    }
    async setTab(tab) {
        this.state.tab = tab;
        await this.loadTab();
    }
    async loadTab() {
        if (!this.state.journalId) {
            return;
        }
        if (this.state.tab === "match") {
            await this.loadLines();
        } else if (this.state.tab === "tick") {
            await this.loadBrs();
        }
    }
    async refreshAll() {
        this.state.overview = await this.orm.call("ebshel.bank.desk", "get_journals", []);
        await this.loadTab();
    }

    // ------------------------------------------------------------ match
    async loadLines() {
        this.state.lines = await this.orm.call("ebshel.bank.desk", "get_lines", [this.state.journalId],
                                               { state: this.state.lineState, search: this.state.search });
        const rows = this.state.lines.rows;
        if (rows.length && !rows.find((r) => r.id === this.state.selectedId)) {
            await this.select(rows[0].id);
        } else if (!rows.length) {
            this.state.selectedId = null;
            this.state.detail = null;
        }
    }
    async select(id) {
        this.state.selectedId = id;
        this.state.parts = [];
        this.state.itemResults = [];
        this.state.itemSearch = "";
        this.state.ruleOffer = null;
        this.state.detail = await this.orm.call("ebshel.bank.desk", "get_line", [id]);
        this.state.partnerId = this.state.detail.partner_id || null;
        if (!this.state.detail.reconciled) {
            this.searchItems();
        }
    }
    confClass(c) {
        return c >= 90 ? "good" : c >= 75 ? "info" : c >= 60 ? "warn" : "muted";
    }
    get allocated() {
        return this.state.parts.reduce((t, p) => t + (p.kind === "open" ? Math.abs(p.amount) : Math.abs(Number(p.amount) || 0)), 0);
    }
    get remaining() {
        const d = this.state.detail;
        if (!d) {
            return 0;
        }
        return Math.max(0, Math.abs(d.residual || d.amount) - this.allocated);
    }
    addOpen(item) {
        if (this.state.parts.find((p) => p.kind === "open" && p.aml_id === item.id)) {
            return;
        }
        this.state.parts.push({ kind: "open", aml_id: item.id, name: item.name, partner: item.partner, amount: item.amount });
    }
    addAccount(account) {
        this.state.parts.push({ kind: "account", account_id: account ? account.id : null, account: account ? account.name : "",
                                amount: Math.round(this.remaining * 100) / 100, label: this.state.detail.label });
    }
    removePart(i) {
        this.state.parts.splice(i, 1);
    }
    async searchItems() {
        if (!this.state.selectedId) {
            return;
        }
        this.state.itemResults = await this.orm.call("ebshel.bank.desk", "search_items", [this.state.selectedId],
                                                     { query: this.state.itemSearch, kind: this.state.itemKind });
    }
    setItemKind(k) {
        this.state.itemKind = k;
        this.searchItems();
    }
    async useCandidate(c) {
        if (c.kind === "book") {
            await this.booked(c.id);
        } else if (c.kind === "open") {
            this.addOpen(c);
        } else if (c.kind === "rule") {
            await this.run(() => this.orm.call("ebshel.bank.desk", "apply_rule", [this.state.selectedId, c.id]), false);
            await this.next();
        }
    }
    async booked(amlId) {
        await this.run(() => this.orm.call("ebshel.bank.desk", "already_booked", [this.state.selectedId, amlId]), true,
                       "Ticked in the books; the duplicate bank line is gone.");
    }
    async accountFor(part, id) {
        const acc = this.state.detail.accounts.find((a) => a.id === Number(id));
        part.account_id = acc ? acc.id : null;
        part.account = acc ? acc.name : "";
    }
    async reconcile() {
        const d = this.state.detail;
        const parts = this.state.parts.map((p) => p.kind === "open" ? { kind: "open", aml_id: p.aml_id }
            : { kind: "account", account_id: p.account_id, amount: Number(p.amount) || 0, label: p.label });
        if (parts.some((p) => p.kind === "account" && !p.account_id)) {
            this.notification.add("Choose the account for every write-off line.", { type: "warning" });
            return;
        }
        const acct = this.state.parts.find((p) => p.kind === "account");
        const res = await this.run(() => this.orm.call("ebshel.bank.desk", "reconcile", [d.id, parts], { partner_id: this.state.partnerId }), false);
        if (res && acct && res.reconciled) {
            const words = await this.orm.call("ebshel.bank.desk", "suggest_keywords", [d.id]);
            this.state.ruleOffer = { st_line_id: d.id, account_id: acct.account_id, account: acct.account, keywords: words, label: acct.label };
        }
        if (res) {
            this.next();
        }
    }
    async acceptBest(row) {
        const b = row.best;
        if (!b) {
            return;
        }
        this.state.selectedId = row.id;
        if (b.kind === "book") {
            await this.orm.call("ebshel.bank.desk", "already_booked", [row.id, b.id]);
        } else if (b.kind === "open") {
            await this.orm.call("ebshel.bank.desk", "reconcile", [row.id, [{ kind: "open", aml_id: b.id }]], {});
        } else if (b.kind === "rule") {
            await this.orm.call("ebshel.bank.desk", "apply_rule", [row.id, b.id]);
        }
        await this.refreshAll();
    }
    async makeRule() {
        const o = this.state.ruleOffer;
        await this.orm.call("ebshel.bank.desk", "create_rule", [o.st_line_id, o.account_id, o.keywords], { label: o.label });
        this.notification.add(`Rule saved: next time "${o.keywords}" goes to ${o.account} by itself.`, { type: "success" });
        this.state.ruleOffer = null;
        this.state.overview.rules += 1;
    }
    async undo() {
        await this.run(() => this.orm.call("ebshel.bank.desk", "undo", [this.state.selectedId]), true);
    }
    async autoReconcile() {
        const res = await this.run(() => this.orm.call("ebshel.bank.desk", "auto_reconcile", [this.state.journalId]), true);
        if (res) {
            this.notification.add(`${res.rules} by rules, ${res.booked} already in the books, ${res.invoices} matched to invoices.`, { type: "success" });
        }
    }
    async run(fn, reload, message) {
        this.state.busy = true;
        try {
            const res = await fn();
            if (message) {
                this.notification.add(message, { type: "success" });
            }
            if (reload) {
                await this.refreshAll();
            }
            return res;
        } finally {
            this.state.busy = false;
        }
    }
    async next() {
        const rows = (this.state.lines && this.state.lines.rows) || [];
        const i = rows.findIndex((r) => r.id === this.state.selectedId);
        await this.refreshAllKeep(rows[i + 1] ? rows[i + 1].id : null);
    }
    async refreshAllKeep(nextId) {
        this.state.overview = await this.orm.call("ebshel.bank.desk", "get_journals", []);
        this.state.lines = await this.orm.call("ebshel.bank.desk", "get_lines", [this.state.journalId],
                                               { state: this.state.lineState, search: this.state.search });
        const rows = this.state.lines.rows;
        const target = rows.find((r) => r.id === nextId) || rows[0];
        if (target) {
            await this.select(target.id);
        } else {
            this.state.selectedId = null;
            this.state.detail = null;
        }
    }
    onKey(ev) {
        if (this.state.tab !== "match" || !this.state.lines || ["INPUT", "TEXTAREA", "SELECT"].includes(ev.target.tagName)) {
            return;
        }
        const rows = this.state.lines.rows;
        const i = rows.findIndex((r) => r.id === this.state.selectedId);
        if (ev.key === "ArrowDown" && rows[i + 1]) {
            ev.preventDefault();
            this.select(rows[i + 1].id);
        } else if (ev.key === "ArrowUp" && i > 0) {
            ev.preventDefault();
            this.select(rows[i - 1].id);
        } else if (ev.key === "Enter" && rows[i] && rows[i].best && this.state.overview.can_write) {
            ev.preventDefault();
            this.acceptBest(rows[i]);
        }
    }
    openMove(id) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "account.move", res_id: id, views: [[false, "form"]] });
    }

    // ------------------------------------------------------------ tick
    async loadBrs() {
        this.state.brs = await this.orm.call("ebshel.bank.desk", "get_brs", [this.state.journalId],
                                             { date: this.state.brsDate, show: this.state.brsShow, search: this.state.brsSearch });
        this.state.ticked = {};
        const cp = this.state.brs.checkpoint;
        this.state.bankBalance = cp && cp.date === this.state.brs.date ? String(cp.balance) : "";
    }
    get tickedIds() {
        return Object.keys(this.state.ticked).filter((k) => this.state.ticked[k]).map(Number);
    }
    tickAll(ev) {
        for (const i of this.state.brs.items) {
            this.state.ticked[i.id] = ev.target.checked;
        }
    }
    async clearSelected(date) {
        const ids = this.tickedIds;
        if (!ids.length) {
            this.notification.add("Tick some items first.", { type: "warning" });
            return;
        }
        await this.orm.call("ebshel.bank.desk", "set_cleared", [ids], { date: date === false ? false : this.state.clearDate });
        await this.loadBrs();
        this.state.overview = await this.orm.call("ebshel.bank.desk", "get_journals", []);
    }
    async clearOne(item) {
        await this.orm.call("ebshel.bank.desk", "set_cleared", [[item.id]], { date: item.cleared ? false : this.state.clearDate });
        await this.loadBrs();
    }
    async saveBalance() {
        if (this.state.bankBalance === "") {
            return;
        }
        await this.orm.call("ebshel.bank.desk", "save_checkpoint", [this.state.journalId, this.state.brsDate, Number(this.state.bankBalance)]);
        await this.loadBrs();
        this.state.overview = await this.orm.call("ebshel.bank.desk", "get_journals", []);
    }
    get difference() {
        if (this.state.bankBalance === "" || !this.state.brs) {
            return null;
        }
        return Number(this.state.bankBalance) - this.state.brs.numbers.bank_expected;
    }
    async baseline() {
        if (!this.state.baselineDate) {
            this.notification.add("Choose the date the bank last agreed with the books.", { type: "warning" });
            return;
        }
        if (!confirm(`Mark every book item up to ${this.state.baselineDate} as seen by the bank?`)) {
            return;
        }
        const n = await this.orm.call("ebshel.bank.desk", "baseline", [this.state.journalId, this.state.baselineDate]);
        this.notification.add(`${n} items marked as cleared.`, { type: "success" });
        await this.loadBrs();
    }
    async printBrs() {
        const act = await this.orm.call("ebshel.bank.desk", "brs_pdf", [this.state.journalId], { date: this.state.brsDate });
        this.action.doAction(act);
    }

    // ------------------------------------------------------------ paste
    parse() {
        const { rows } = splitRows(this.state.pasteText);
        if (!rows.length) {
            this.state.parsed = null;
            return;
        }
        this.state.map = guessMapping(rows);
        this.state.parsed = rows;
    }
    get preview() {
        if (!this.state.parsed || !this.state.map) {
            return [];
        }
        return buildRows(this.state.parsed, this.state.map);
    }
    get columns() {
        return this.state.map ? [...Array(this.state.map.width).keys()] : [];
    }
    setMap(key, ev) {
        this.state.map[key] = Number(ev.target.value);
        if (key === "amount" && this.state.map.amount >= 0) {
            this.state.map.debit = -1;
            this.state.map.credit = -1;
        }
        if ((key === "debit" || key === "credit") && this.state.map[key] >= 0) {
            this.state.map.amount = -1;
        }
    }
    colLabel(i) {
        const head = this.state.map.hasHeader ? this.state.parsed[0][i] : "";
        return `Column ${i + 1}${head ? " · " + head : ""}`;
    }
    async doImport() {
        const rows = this.preview.filter((r) => r.ok);
        if (!rows.length) {
            this.notification.add("No row has both a date and an amount.", { type: "warning" });
            return;
        }
        const res = await this.run(() => this.orm.call("ebshel.bank.desk", "import_rows", [this.state.journalId, rows],
                                                      { tick: this.state.tickOnImport }), true);
        this.state.importResult = res;
        if (res) {
            this.state.pasteText = "";
            this.state.parsed = null;
        }
    }
    onFile(ev) {
        const file = ev.target.files[0];
        if (!file) {
            return;
        }
        const reader = new FileReader();
        reader.onload = () => {
            this.state.pasteText = String(reader.result || "");
            this.parse();
        };
        reader.readAsText(file);
    }
}

registry.category("actions").add("ebshel_bank_rec", BankRec);
