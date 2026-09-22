/** @odoo-module **/

import {
    Component,
    onPatched,
    onWillStart,
    onWillUnmount,
    useExternalListener,
    useRef,
    useState,
} from "@odoo/owl";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { useHotkey } from "@web/core/hotkeys/hotkey_hook";
import { RecordSelector } from "@web/core/record_selectors/record_selector";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { formatMonetary } from "@web/views/fields/formatters";

const BUCKETS = ["a", "b", "c", "d"];
const SOURCES = ["cash", "bank", "party", "other", "query", "carried"];
export const ENTRY_KINDS = [
    ["receipt", "Receipt from a party"],
    ["payment", "Payment to a party"],
    ["transfer_in", "Other money into the bank"],
    ["transfer_out", "Other money out of the bank"],
    ["charge", "Bank charge"],
    ["interest", "Interest credited"],
];

/**
 * The matching screen: tick the bank-account lines the bank has cleared.
 *
 * Kept to one idea per row: where the statement stands and what to do next, the
 * passbook line being worked through, the tools, and the two columns. Type a passbook
 * line and it is found (and ticked when there is only one answer); click an entry for
 * everything behind it; post what only the passbook has without leaving the screen.
 */
export class BankRecScreen extends Component {
    static template = "lab_bank_reconciliation.Screen";
    static components = { RecordSelector };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        const action = this.props.action || {};
        this.recId = action.params?.reconciliation_id || action.context?.active_id;
        this.searchRef = useRef("search");
        this.passRef = useRef("passAmount");
        this.balanceRef = useRef("balance");
        this.queryRef = useRef("queryInput");
        this.drawerQueryRef = useRef("drawerQueryInput");
        this.entryTypes = ENTRY_KINDS;
        this.state = useState({
            data: null,
            missing: false,
            search: "",
            age: "",
            source: "",
            view: "lines",
            days: { deposits: null, withdrawals: null },
            busy: false,
            focus: -1,
            keys: false,
            more: false,
            filters: false,
            statement: false,
            pass: {
                side: "deposits", amount: "", balance: "", date: "", window: "7", auto: true,
                result: null, lastBalance: null, lastBalanceDate: "",
            },
            entry: {
                open: false, kind: "receipt", amount: "", date: "", label: "", reference: "",
                partner_id: false, account_id: false, cleared: true,
            },
            upto: { deposits: "", withdrawals: "" },
            dup: { open: false, data: null },
            query: { lineId: false, note: "" },
            detail: { lineId: false, data: null },
            lastTick: { side: null, index: -1 },
        });
        this.timer = null;
        this.scrollFocus = false;
        this.focusQuery = false;
        this.focusPass = false;
        useExternalListener(window, "keydown", (ev) => this.onKeydown(ev));
        // Esc goes through the hotkey service: it handles Escape itself and stops the
        // event before any other window listener, so a plain keydown listener never
        // saw it. Claimed only while there is something of this screen to close.
        useHotkey("escape", () => this.onEscape(), {
            bypassEditableProtection: true,
            isAvailable: () => Boolean(this.state.detail.lineId || this.state.entry.open || this.state.more),
        });
        useExternalListener(window, "click", (ev) => {
            if (this.state.more && !ev.target.closest(".o_bank_rec_more_wrap")) {
                this.state.more = false;
            }
        });
        onWillStart(async () => {
            await this.load();
            this.loadDuplicates();
        });
        onPatched(() => {
            if (this.scrollFocus) {
                this.scrollFocus = false;
                document.querySelector(".o_bank_rec_focus")?.scrollIntoView({ block: "nearest" });
            }
            if (this.focusQuery) {
                this.focusQuery = false;
                (this.drawerQueryRef.el || this.queryRef.el)?.focus();
            }
            if (this.focusPass) {
                this.focusPass = false;
                this.passRef.el?.focus();
            }
        });
        onWillUnmount(() => clearTimeout(this.timer));
    }

    // ------------------------------------------------------------------ derived
    get sides() {
        const d = this.state.data;
        const { age, source } = this.state;
        return [
            { key: "deposits", title: "Deposits", hint: "money into the bank", offset: 0 },
            { key: "withdrawals", title: "Withdrawals", hint: "money out of the bank",
              offset: d.deposits.length },
        ].map((side) => {
            const stats = d.stats[side.key];
            let outstanding = stats.count;
            if (age && source) {
                outstanding = null;
            } else if (source === "query") {
                outstanding = stats.queries;
            } else if (source) {
                outstanding = stats.sources[source].count;
            } else if (age) {
                outstanding = stats.ageing[age].count;
            }
            return { ...side, lines: d[side.key], days: this.state.days[side.key], outstanding };
        });
    }

    get flatLines() {
        return [...this.state.data.deposits, ...this.state.data.withdrawals];
    }

    get buckets() {
        const d = this.state.data;
        return BUCKETS.map((key) => ({
            key,
            label: d.age_labels[key],
            count: d.stats.deposits.ageing[key].count + d.stats.withdrawals.ageing[key].count,
            amount: d.stats.deposits.ageing[key].amount + d.stats.withdrawals.ageing[key].amount,
        }));
    }

    get sourceChips() {
        const d = this.state.data;
        const { deposits, withdrawals } = d.stats;
        const counts = {
            query: [deposits.queries + withdrawals.queries, "flagged"],
            carried: [deposits.carried + withdrawals.carried, "still outstanding"],
        };
        return SOURCES.map((key) => {
            const special = counts[key];
            return {
                key,
                label: d.source_labels[key],
                count: special ? special[0] : deposits.sources[key].count + withdrawals.sources[key].count,
                amount: special ? null : deposits.sources[key].amount + withdrawals.sources[key].amount,
                note: special ? special[1] : "",
            };
        }).filter((chip) => chip.count || this.state.source === chip.key);
    }

    get activeFilters() {
        const d = this.state.data;
        const pills = [];
        if (this.state.age) {
            pills.push({ key: "age", label: d.age_labels[this.state.age] });
        }
        if (this.state.source) {
            pills.push({ key: "source", label: d.source_labels[this.state.source] });
        }
        return pills;
    }

    get queryCount() {
        const { deposits, withdrawals } = this.state.data.stats;
        return deposits.queries + withdrawals.queries;
    }

    /** The one thing to do next, most important first. */
    get coach() {
        const d = this.state.data;
        if (d.state === "done") {
            return { kind: "done" };
        }
        if (!d.editable) {
            return false;
        }
        if (d.opening) {
            return { kind: "opening" };
        }
        if (!d.bank_balance) {
            return { kind: "balance" };
        }
        if (d.figures.ready) {
            return { kind: "ready" };
        }
        return d.hints.length ? { kind: "hints" } : false;
    }

    get accountDomain() {
        const d = this.state.data;
        return [["company_ids", "in", [d.company_id]], ["id", "!=", d.account_id]];
    }

    get entryNeedsPartner() {
        return ["receipt", "payment"].includes(this.state.entry.kind);
    }

    get entryNeedsAccount() {
        return ["transfer_in", "transfer_out"].includes(this.state.entry.kind);
    }

    get entryNote() {
        const d = this.state.data;
        const e = this.state.entry;
        const into = ["receipt", "transfer_in", "interest"].includes(e.kind);
        const other = {
            receipt: "the party's receivable account (or the account picked)",
            payment: "the party's payable account (or the account picked)",
            transfer_in: "the account picked",
            transfer_out: "the account picked",
            charge: d.charge_account || "the Bank Charges Account (set it on the statement form)",
            interest: d.interest_account || "the Interest Account (set it on the statement form)",
        }[e.kind];
        return `Posts a journal entry in ${d.journal}: money ${into ? "into" : "out of"} the bank against ${other}${e.cleared ? ", ticked as cleared" : ", left outstanding"}.`;
    }

    get openingText() {
        const { count, amount } = this.state.data.opening;
        const one = count === 1;
        return `${count} ${one ? "entry was" : "entries were"} posted from the general journal, like the opening balance, totalling ${this.money(amount)}. If the bank already held that money, tick ${one ? "it" : "them"}.`;
    }

    matchBadge(result) {
        if (result.kind === "single") {
            return "Exact entry";
        }
        if (result.kind === "near") {
            return `Short by ${this.money(result.difference)}`;
        }
        if (result.kind === "batch") {
            return `Batch of ${result.line_ids.length}`;
        }
        return `${result.line_ids.length} entries add up`;
    }

    money(value) {
        return formatMonetary(value || 0, { currencyId: this.state.data?.currency_id });
    }

    abs(value) {
        return Math.abs(value || 0);
    }

    plural(count, one, many) {
        return count === 1 ? one : many;
    }

    // ------------------------------------------------------------------ server
    kwargs(extra = {}) {
        return {
            search: this.state.search,
            age: this.state.age || false,
            source: this.state.source || false,
            ...extra,
        };
    }

    notifyError(error) {
        this.notification.add(error.data?.message || error.message, { type: "danger" });
    }

    /** A delta carries only the changed lines: patch them in, keep the rest. */
    applyPayload(payload) {
        if (!payload.delta || !this.state.data) {
            this.state.data = payload;
            return;
        }
        const { lines, delta, ...rest } = payload;
        const changed = new Map(lines.map((line) => [line.id, line]));
        const data = this.state.data;
        for (const key of ["deposits", "withdrawals"]) {
            data[key] = data[key].map((line) => changed.get(line.id) || line);
        }
        Object.assign(data, rest);
    }

    /**
     * Calls are queued, never dropped: typing the bank balance and clicking a tick or
     * a hint straight away fires the balance's change (on blur) and the click together,
     * and the click used to be thrown away while the first call was still running.
     */
    call(method, args = [], extra = {}) {
        const run = async () => {
            this.state.busy = true;
            try {
                this.applyPayload(await this.orm.call("bank.reconciliation", method,
                    [[this.recId], ...args], this.kwargs(extra)));
                if (this.state.view === "days") {
                    await this.loadDays();
                }
                if (this.state.detail.lineId) {
                    this.refreshDetails();
                }
                return true;
            } catch (error) {
                this.notifyError(error);
                return false;
            } finally {
                this.state.busy = false;
            }
        };
        this.queue = (this.queue || Promise.resolve()).then(run, run);
        return this.queue;
    }

    async load() {
        if (!this.recId) {
            this.state.missing = true;
            return;
        }
        this.state.data = await this.orm.call("bank.reconciliation", "get_screen",
            [[this.recId]], this.kwargs());
        const d = this.state.data;
        this.state.entry.date ||= d.date;
        this.state.pass.date ||= d.date;
        if (this.state.focus >= this.flatLines.length) {
            this.state.focus = this.flatLines.length - 1;
        }
        if (this.state.view === "days") {
            await this.loadDays();
        }
    }

    async loadDays() {
        const kwargs = this.kwargs();
        const [deposits, withdrawals] = await Promise.all(["deposits", "withdrawals"].map(
            (side) => this.orm.call("bank.reconciliation", "get_day_totals",
                [[this.recId], side], kwargs)));
        this.state.days = { deposits: deposits.days, withdrawals: withdrawals.days };
    }

    // ------------------------------------------------------------------ header
    toggleMore() {
        this.state.more = !this.state.more;
    }

    toggleStatement() {
        this.state.statement = !this.state.statement;
    }

    toggleKeys() {
        this.state.keys = !this.state.keys;
        this.state.more = false;
    }

    focusBalance() {
        this.balanceRef.el?.focus();
        this.balanceRef.el?.select();
    }

    // ------------------------------------------------------------------ filters and views
    onSearch(ev) {
        this.state.search = ev.target.value;
        clearTimeout(this.timer);
        this.timer = setTimeout(() => this.load(), 300);
    }

    toggleFilters() {
        this.state.filters = !this.state.filters;
    }

    setAge(key) {
        this.state.age = this.state.age === key ? "" : key;
        this.state.focus = -1;
        return this.load();
    }

    setSource(key) {
        this.state.source = this.state.source === key ? "" : key;
        this.state.focus = -1;
        return this.load();
    }

    removeFilter(key) {
        this.state[key] = "";
        return this.load();
    }

    setView(view) {
        this.state.view = view;
        this.state.focus = -1;
        if (view === "days") {
            this.state.days = { deposits: null, withdrawals: null };
            return this.loadDays();
        }
    }

    // ------------------------------------------------------------------ ticking
    toggle(line, index) {
        if (index !== undefined) {
            this.state.focus = index;
        }
        if (line.locked || !this.state.data.editable) {
            return;
        }
        return this.call("set_cleared", [[line.id], !line.cleared, false], { delta: true });
    }

    /** A click on a checkbox ticks it; shift-click ticks (or unticks) the whole range. */
    onTick(ev, line, side, index) {
        ev.preventDefault();
        const last = this.state.lastTick;
        this.state.lastTick = { side: side.key, index };
        if (line.locked || !this.state.data.editable) {
            return;
        }
        if (ev.shiftKey && last.side === side.key && last.index >= 0 && last.index !== index) {
            const target = !line.cleared;
            const [from, to] = [Math.min(last.index, index), Math.max(last.index, index)];
            const ids = side.lines.slice(from, to + 1)
                .filter((l) => !l.locked && l.cleared !== target).map((l) => l.id);
            this.state.focus = side.offset + index;
            return ids.length && this.call("set_cleared", [ids, target, false], { delta: true });
        }
        return this.toggle(line, side.offset + index);
    }

    setDate(line, ev) {
        if (!ev.target.value) {
            return;
        }
        return this.call("set_cleared", [[line.id], true, ev.target.value], { delta: true });
    }

    saveBankBalance(ev) {
        return this.call("set_bank_balance", [ev.target.value || 0]);
    }

    confirm(title, body, confirmLabel, confirm) {
        this.dialog.add(ConfirmationDialog, { title, body, confirmLabel, confirm, cancel: () => {} });
    }

    filterWords() {
        const d = this.state.data;
        const parts = [];
        if (this.state.search.trim()) {
            parts.push(`matching “${this.state.search.trim()}”`);
        }
        if (this.state.age) {
            parts.push(`aged ${d.age_labels[this.state.age]}`);
        }
        if (this.state.source) {
            parts.push(`from “${d.source_labels[this.state.source]}”`);
        }
        return parts.length ? ` ${parts.join(", ")}` : "";
    }

    tickAll(side) {
        const count = side.outstanding === null ? side.lines.length : side.outstanding;
        this.confirm(
            "Tick them all as cleared?",
            `All ${count} outstanding ${side.title.toLowerCase()}${this.filterWords()} will be marked cleared, each on its own date.`,
            "Tick them all",
            () => this.call("clear_matching", [side.key]),
        );
    }

    setUpto(side, ev) {
        this.state.upto[side.key] = ev.target.value;
    }

    tickUpTo(side) {
        const upto = this.state.upto[side.key];
        if (!upto) {
            return;
        }
        this.confirm(
            "Tick everything up to that date?",
            `Every outstanding ${side.title.toLowerCase().replace(/s$/, "")}${this.filterWords()} booked on or before ${upto} will be marked cleared, each on its own date.`,
            "Tick them",
            () => this.call("clear_matching", [side.key], { upto }),
        );
    }

    tickDay(side, day, group) {
        const what = group
            ? `${group.label}: ${group.count} ${this.plural(group.count, "entry", "entries")}`
            : `All ${day.count} ${this.plural(day.count, "entry", "entries")}`;
        this.confirm(
            "Tick this batch as cleared?",
            `${what} booked on ${day.label}${this.filterWords()}, ${this.money(group ? group.amount : day.amount)}, will be marked cleared on their own date.`,
            "Tick them",
            () => this.call("clear_day", [side.key, day.day, group ? group.key : false]),
        );
    }

    clearOpening() {
        const opening = this.state.data.opening;
        this.confirm(
            "Was this money already in the bank?",
            `${opening.count} ${this.plural(opening.count, "entry", "entries")} from the general journal, totalling ${this.money(opening.amount)}, will be marked cleared on ${this.plural(opening.count, "its", "their")} own date.`,
            "Yes, tick them",
            () => this.call("clear_opening"),
        );
    }

    applyHint(hint) {
        switch (hint.kind) {
            case "tick":
            case "pair":
                return this.call("set_cleared", [hint.line_ids, true, false], { delta: true });
            case "untick":
                return this.call("set_cleared", [hint.line_ids, false, false], { delta: true });
            case "search":
                this.state.search = hint.search;
                return this.load();
            case "post":
                return this.openEntry(hint.item_kind, { amount: String(hint.amount) });
        }
    }

    async undo() {
        const step = this.state.data?.undo;
        if (!step) {
            return;
        }
        if (await this.call("undo_last")) {
            this.notification.add(`Undone: ${step.label}.`, { type: "info" });
        }
    }

    // ------------------------------------------------------------------ the passbook line
    setPassSide(side) {
        this.state.pass.side = side;
        this.state.pass.result = null;
    }

    /**
     * The passbook's own balance column, checked line by line. Each balance must be the
     * last one plus this credit (or less this debit); when it is not, a passbook line was
     * skipped, and the gap is the amount to look for.
     */
    checkBalance(balance, amount) {
        const pass = this.state.pass;
        if (pass.lastBalance !== null && amount) {
            const expected = pass.lastBalance + (pass.side === "deposits" ? amount : -amount);
            const gap = Math.round((balance - expected) * 100) / 100;
            if (gap) {
                this.notification.add(
                    `After this line the passbook should show ${this.money(expected)}, but it shows ${this.money(balance)}. A ${gap > 0 ? "credit" : "debit"} of ${this.money(Math.abs(gap))} may be missing before it.`, {
                        type: "warning",
                        sticky: true,
                        buttons: [{ name: `Look for ${this.money(Math.abs(gap))}`, onClick: () => this.lookForGap(gap) }],
                    });
            }
        } else if (!amount) {
            this.notification.add(
                `Passbook balance ${this.money(balance)} noted. Type each line's balance too, and a skipped line will show up.`,
                { type: "info" });
        }
        pass.lastBalance = balance;
        pass.lastBalanceDate = pass.date;
    }

    lookForGap(gap) {
        Object.assign(this.state.pass, {
            side: gap > 0 ? "deposits" : "withdrawals", amount: String(Math.abs(gap)), balance: "", result: null,
        });
        this.focusPass = true;
    }

    useLastBalance() {
        return this.call("set_bank_balance", [this.state.pass.lastBalance]);
    }

    async tickNear(match, result = this.state.pass.result) {
        const ok = await this.call("tick_with_difference", [match.line_ids, result.day, match.difference]);
        if (!ok) {
            return;
        }
        this.notification.add(
            `Ticked ${match.lines[0].label || match.lines[0].move} and booked ${this.money(match.difference)} as bank charges.`, {
                type: "success",
                buttons: [{ name: "Undo the tick", onClick: () => this.undo() }],
            });
        Object.assign(this.state.pass, { result: null, amount: "" });
        this.focusPass = true;
    }

    closeMatches() {
        this.state.pass.result = null;
    }

    onPassKey(ev) {
        if (ev.key === "Enter") {
            this.findPassbook();
        } else if (ev.key === "Escape") {
            this.state.pass.result = null;
        }
    }

    /**
     * Find the passbook line in the books, and tick it straight away when exactly one
     * outstanding entry has that amount and no whole batch does. Two-to-four-entry
     * combinations do not stop it: on a bank with a hundred receipts a day almost any
     * figure can be made up from other entries, and those are guesses, not answers.
     */
    autoMatch(result) {
        const singles = result.results.filter((r) => r.kind === "single");
        const batches = result.results.filter((r) => r.kind === "batch");
        return singles.length === 1 && !batches.length ? singles[0] : false;
    }

    async findPassbook() {
        const pass = this.state.pass;
        const amount = parseFloat(pass.amount);
        const balance = pass.balance === "" ? null : parseFloat(pass.balance);
        if (balance !== null && !isNaN(balance)) {
            this.checkBalance(balance, amount || 0);
            pass.balance = "";
        }
        if (!amount) {
            if (balance !== null && !isNaN(balance)) {
                this.focusPass = true;
                return;
            }
            this.notification.add("Type the amount from the passbook line.", { type: "warning" });
            return;
        }
        this.state.busy = true;
        let result;
        try {
            result = await this.orm.call("bank.reconciliation", "find_matches",
                [[this.recId], amount, pass.date || false, pass.side, parseInt(pass.window)]);
        } catch (error) {
            this.notifyError(error);
            return;
        } finally {
            this.state.busy = false;
        }
        const match = pass.auto && this.state.data.editable && this.autoMatch(result);
        if (match) {
            return this.tickMatch(match, result);
        }
        pass.result = result;
    }

    async tickMatch(match, result = this.state.pass.result) {
        const ok = await this.call("set_cleared", [match.line_ids, true, result.day], { delta: true });
        if (!ok) {
            return;
        }
        const what = match.line_ids.length === 1
            ? `${match.lines[0].label || match.lines[0].move} (${this.money(match.total)})`
            : `${match.line_ids.length} entries totalling ${this.money(match.total)}`;
        this.notification.add(`Ticked ${what} as cleared on ${result.day_label}.`, {
            type: "success",
            buttons: [{ name: "Undo", onClick: () => this.undo() }],
        });
        this.state.pass.result = null;
        this.state.pass.amount = "";
        this.focusPass = true;
    }

    createFromPassbook() {
        const pass = this.state.pass;
        const credit = pass.side === "deposits";
        this.openEntry(credit ? "receipt" : "payment", { amount: pass.amount, date: pass.date });
        pass.result = null;
    }

    // ------------------------------------------------------------------ bank entries
    openEntry(kind = "receipt", preset = {}) {
        Object.assign(this.state.entry, {
            open: true, kind, label: "", reference: "", partner_id: false, account_id: false,
            cleared: true, amount: "", date: this.state.data.date, ...preset,
        });
    }

    closeEntry() {
        this.state.entry.open = false;
    }

    setEntryPartner(resId) {
        this.state.entry.partner_id = resId || false;
    }

    setEntryAccount(resId) {
        this.state.entry.account_id = resId || false;
    }

    async postEntry() {
        const e = this.state.entry;
        const ok = await this.call("create_bank_entry", [e.kind, parseFloat(e.amount) || 0, e.date || false], {
            partner_id: this.entryNeedsPartner ? e.partner_id || false : false,
            account_id: e.account_id || false,
            label: e.label || false,
            reference: e.reference || false,
            cleared: e.cleared,
        });
        const posted = ok && this.state.data.posted;
        if (posted) {
            this.notification.add(
                `${posted.name} posted${e.cleared ? " and ticked as cleared" : ""}.`, {
                    type: "success",
                    buttons: [{ name: "Open entry", onClick: () => this.openRecord("account.move", posted.id) }],
                });
            e.open = false;
        }
    }

    // ------------------------------------------------------------------ duplicates
    async loadDuplicates() {
        try {
            this.state.dup.data = await this.orm.call("bank.reconciliation", "get_duplicates", [[this.recId]]);
        } catch (error) {
            this.notifyError(error);
        }
    }

    async toggleDuplicates() {
        this.state.dup.open = !this.state.dup.open;
        if (this.state.dup.open) {
            await this.loadDuplicates();
        }
    }

    showDuplicate(group) {
        this.state.search = group.partner;
        this.state.dup.open = false;
        this.state.view = "lines";
        return this.load();
    }

    // ------------------------------------------------------------------ queries
    openQuery(line) {
        this.state.query = { lineId: line.id, note: line.query || "" };
        this.focusQuery = true;
    }

    closeQuery() {
        this.state.query = { lineId: false, note: "" };
    }

    async saveQuery(resolved = false) {
        const query = this.state.query;
        if (!resolved && !query.note.trim()) {
            this.notification.add("Say what to ask the bank.", { type: "warning" });
            return;
        }
        if (await this.call("set_query", [query.lineId, resolved ? false : query.note], { delta: true })) {
            this.closeQuery();
        }
    }

    onQueryKey(ev) {
        if (ev.key === "Enter") {
            this.saveQuery();
        } else if (ev.key === "Escape") {
            ev.stopPropagation();
            this.closeQuery();
        }
    }

    // ------------------------------------------------------------------ entry details
    async openDetails(line, index) {
        if (index !== undefined) {
            this.state.focus = index;
        }
        this.state.detail = { lineId: line.id, data: null };
        await this.refreshDetails();
    }

    async refreshDetails() {
        const lineId = this.state.detail.lineId;
        try {
            const data = await this.orm.call("bank.reconciliation", "get_line_details", [[this.recId], lineId]);
            if (this.state.detail.lineId === lineId) {
                this.state.detail.data = data;
            }
        } catch (error) {
            this.notifyError(error);
            this.closeDetails();
        }
    }

    closeDetails() {
        this.state.detail = { lineId: false, data: null };
        this.closeQuery();
    }

    detailTick() {
        const x = this.state.detail.data;
        return this.call("set_cleared", [[x.line_id], !x.cleared, false], { delta: true });
    }

    detailQuery() {
        const x = this.state.detail.data;
        this.state.query = { lineId: x.line_id, note: x.query ? x.query.note : "" };
        this.focusQuery = true;
    }

    showParty(party) {
        this.state.search = party.name;
        this.closeDetails();
        this.state.view = "lines";
        return this.load();
    }

    openRecord(model, id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: model,
            res_id: id,
            views: [[false, "form"]],
            target: "new",
        }, { onClose: () => this.state.detail.lineId && this.refreshDetails() });
    }

    // ------------------------------------------------------------------ keyboard
    onEscape() {
        if (this.state.more) {
            this.state.more = false;
        } else if (this.state.detail.lineId) {
            this.closeDetails();
        } else if (this.state.entry.open) {
            this.closeEntry();
        }
    }

    onKeydown(ev) {
        if (!this.state.data || document.querySelector(".modal")) {
            return;
        }
        const target = ev.target;
        const typing = target && (["INPUT", "SELECT", "TEXTAREA"].includes(target.tagName)
            || target.isContentEditable);
        if (typing) {
            if (ev.key === "Escape") {
                target.blur();
            }
            return;
        }
        if (ev.ctrlKey || ev.metaKey || ev.altKey) {
            return;
        }
        const editable = this.state.data.editable;
        const lines = this.state.view === "lines" ? this.flatLines : [];
        const focused = lines[this.state.focus];
        const move = (step) => {
            if (lines.length) {
                this.state.focus = Math.max(0, Math.min(lines.length - 1, this.state.focus + step));
                this.scrollFocus = true;
            }
        };
        switch (ev.key) {
            case "Escape":
                if (this.state.detail.lineId) {
                    this.closeDetails();
                } else if (this.state.entry.open) {
                    this.closeEntry();
                } else {
                    return;
                }
                break;
            case "ArrowDown":
            case "j":
                move(1);
                break;
            case "ArrowUp":
            case "k":
                move(-1);
                break;
            case " ":
            case "x":
                if (!focused) {
                    return;
                }
                this.toggle(focused);
                break;
            case "Enter":
            case "o":
                if (!focused) {
                    return;
                }
                this.openDetails(focused);
                break;
            case "q":
                if (!focused || !editable) {
                    return;
                }
                this.openQuery(focused);
                break;
            case "/":
                this.searchRef.el?.focus();
                break;
            case "p":
                this.passRef.el?.focus();
                break;
            case "n":
                if (editable) {
                    this.openEntry();
                }
                break;
            case "u":
                if (editable) {
                    this.undo();
                }
                break;
            case "d":
                this.setView(this.state.view === "days" ? "lines" : "days");
                break;
            case "?":
                this.toggleKeys();
                break;
            default:
                return;
        }
        ev.preventDefault();
    }

    // ------------------------------------------------------------------ navigation
    async validate() {
        this.state.busy = true;
        try {
            await this.orm.call("bank.reconciliation", "action_validate", [[this.recId]]);
            this.notification.add("Reconciled. The cleared lines are now locked.", { type: "success" });
        } catch (error) {
            this.notifyError(error);
        } finally {
            this.state.busy = false;
        }
        await this.load();
    }

    async runAction(method) {
        this.state.more = false;
        try {
            const action = await this.orm.call("bank.reconciliation", method, [[this.recId]]);
            await this.action.doAction(action);
        } catch (error) {
            this.notifyError(error);
        }
    }

    excel() {
        this.state.more = false;
        this.action.doAction({
            type: "ir.actions.act_url",
            url: `/bank_reconciliation/${this.recId}/xlsx`,
            target: "self",
        });
    }

    back() {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "bank.reconciliation",
            res_id: this.recId,
            views: [[false, "form"]],
        });
    }

    openOverview() {
        this.action.doAction("lab_bank_reconciliation.action_bank_reconciliation_overview");
    }
}

registry.category("actions").add("bank_reconciliation_screen", BankRecScreen);
