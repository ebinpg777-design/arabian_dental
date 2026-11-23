/** @odoo-module **/

import { Component, onWillStart, proxy, t, usePlugin, useProps } from "@odoo/owl";
import { DateTimeInput } from "@web/core/datetime/datetime_input";
import { Dialog } from "@web/core/dialog/dialog";
import { deserializeDate, serializeDate } from "@web/core/l10n/dates";
import { _t } from "@web/core/l10n/translation";
import { NotificationPlugin } from "@web/core/notifications/notification_plugin";
import { RecordSelector } from "@web/core/record_selectors/record_selector";
import { useService } from "@web/core/utils/hooks";

const ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
    "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen",
    "Nineteen"];
const TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"];

function belowHundred(n) {
    return n < 20 ? ONES[n] : TENS[Math.floor(n / 10)] + (n % 10 ? " " + ONES[n % 10] : "");
}

function belowThousand(n) {
    const hundreds = Math.floor(n / 100);
    const rest = n % 100;
    return [hundreds ? ONES[hundreds] + " Hundred" : "", rest ? belowHundred(rest) : ""]
        .filter(Boolean).join(" ");
}

/** 125000 -> "One Lakh Twenty Five Thousand": the way an Indian cheque writes it. */
export function indianWords(n) {
    n = Math.floor(n);
    if (!n) {
        return "";
    }
    const parts = [];
    const crore = Math.floor(n / 10000000);
    n %= 10000000;
    const lakh = Math.floor(n / 100000);
    n %= 100000;
    const thousand = Math.floor(n / 1000);
    n %= 1000;
    if (crore) {
        parts.push(indianWords(crore) + " Crore");
    }
    if (lakh) {
        parts.push(belowHundred(lakh) + " Lakh");
    }
    if (thousand) {
        parts.push(belowHundred(thousand) + " Thousand");
    }
    if (n) {
        parts.push(belowThousand(n));
    }
    return parts.join(" ");
}

export function rupeesInWords(amount) {
    const value = Math.round((Number(amount) || 0) * 100) / 100;
    const rupees = Math.floor(value);
    const paise = Math.round((value - rupees) * 100);
    if (!rupees && !paise) {
        return "";
    }
    const words = [rupees ? "Rupees " + indianWords(rupees) : "", paise ? belowHundred(paise) + " Paise" : ""]
        .filter(Boolean).join(" and ");
    return words + " Only";
}

/** "000164" -> "000165": the next cheque of a series keeps its leading zeros. */
export function nextChequeNumber(number) {
    const match = /^(.*?)(\d+)$/.exec(number || "");
    if (!match) {
        return "";
    }
    const next = String(Number(match[2]) + 1).padStart(match[2].length, "0");
    return match[1] + next;
}

/**
 * Add a cheque, the way it is held in the hand (client, 2026-11-23: "provide option to
 * add cheques from Payments. provide an innovative widget with userfriendly access").
 *
 * A cheque-shaped card: the clinic, the number and the date as written, the amount with
 * its words underneath - a slipped zero shows at once - and the bank, remembered from the
 * clinic's last cheque. Below it, the clinic's open invoices, settled oldest first as the
 * amount is typed and changeable line by line. Save & Receive settles them as of the
 * received day; Save & Next Cheque keeps the clinic, the bank and the amount and moves to
 * the next number and the next month - a series of post-dated cheques in a minute.
 */
export class ChequeEntryDialog extends Component {
    static template = "lab_finance_ops.ChequeEntryDialog";
    static components = { Dialog, RecordSelector, DateTimeInput };
    props = useProps({
        close: t.function(),
        onSaved: t.function().optional(),
        partnerId: t.number().optional(),
    });

    setup() {
        this.orm = useService("orm");
        this.notification = usePlugin(NotificationPlugin);
        this.state = proxy({
            loading: true, saving: false, data: null,
            partnerId: this.props.partnerId || false,
            number: "", chequeDate: null, receivedDate: null, bank: "", branch: "",
            amount: 0, amountText: "", journalId: false,
            invoices: [], manual: false, saved: [],
        });
        onWillStart(() => this.load());
    }

    // ------------------------------------------------------------------ data
    async load() {
        this.state.loading = true;
        const data = await this.orm.call("lab.cheque", "lab_cheque_entry_data", [], {
            partner_id: this.state.partnerId || false,
        });
        this.state.data = data;
        const today = deserializeDate(data.today);
        this.state.chequeDate = this.state.chequeDate || today;
        this.state.receivedDate = this.state.receivedDate || today;
        const last = data.last;
        if (last) {
            this.state.bank = this.state.bank || last.bank_name;
            this.state.branch = this.state.branch || last.branch;
            this.state.journalId = this.state.journalId || last.journal_id;
        }
        if (!this.state.journalId) {
            const first = data.journals.find((j) => j.pdc) || data.journals[0];
            this.state.journalId = first ? first.id : false;
        }
        this.state.invoices = data.invoices.map((inv) => ({ ...inv, amount: 0, picked: false }));
        this.state.manual = false;
        this.allocate();
        this.state.loading = false;
    }

    async setPartner(partnerId) {
        this.state.partnerId = partnerId || false;
        this.state.bank = "";
        this.state.branch = "";
        await this.load();
    }

    // ------------------------------------------------------------------ the card
    get partnerDomain() {
        return ["|", ["is_clinic", "=", true], ["customer_rank", ">", 0]];
    }

    get words() {
        return this.currencyIsRupee ? rupeesInWords(this.state.amount) : "";
    }

    get currencyIsRupee() {
        const currency = this.state.data && this.state.data.currency;
        return Boolean(currency && currency.symbol === "₹");
    }

    get dateChip() {
        const today = this.state.data && deserializeDate(this.state.data.today);
        const day = this.state.chequeDate;
        if (!today || !day) {
            return null;
        }
        const days = Math.round(day.diff(today, "days").days);
        if (days > 0) {
            return { label: _t("Post-dated · %s days", days), kind: "post" };
        }
        return { label: days < -90 ? _t("Stale? %s days old", -days) : _t("Due"), kind: days < -90 ? "stale" : "due" };
    }

    get journal() {
        return (this.state.data?.journals || []).find((j) => j.id === Number(this.state.journalId));
    }

    get duplicate() {
        const numbers = this.state.data?.numbers || [];
        const typed = (this.state.number || "").trim();
        return typed && numbers.includes(typed);
    }

    money(value) {
        const currency = this.state.data?.currency || {};
        const decimals = currency.decimals ?? 2;
        const text = Number(value || 0).toLocaleString("en-IN", {
            minimumFractionDigits: decimals, maximumFractionDigits: decimals,
        });
        return currency.position === "after" ? `${text} ${currency.symbol || ""}` : `${currency.symbol || ""} ${text}`;
    }

    setJournal(ev) {
        this.state.journalId = parseInt(ev.target.value, 10) || false;
    }

    onAmount(ev) {
        this.state.amountText = ev.target.value;
        const value = parseFloat(String(ev.target.value).replace(/[^0-9.]/g, ""));
        this.state.amount = isNaN(value) ? 0 : value;
        if (!this.state.manual) {
            this.allocate();
        }
    }

    // ------------------------------------------------------------------ invoices
    /** Oldest first until the cheque runs out - how the ledger is settled. */
    allocate() {
        let left = this.state.amount;
        for (const inv of this.state.invoices) {
            const take = Math.max(0, Math.min(inv.open, left));
            inv.amount = Math.round(take * 100) / 100;
            inv.picked = take > 0;
            left -= take;
        }
    }

    resetAllocation() {
        this.state.manual = false;
        this.allocate();
    }

    togglePick(inv) {
        this.state.manual = true;
        inv.picked = !inv.picked;
        if (!inv.picked) {
            inv.amount = 0;
        } else {
            const room = Math.max(0, this.state.amount - this.allocated + inv.amount);
            inv.amount = Math.round(Math.min(inv.open, room || inv.open) * 100) / 100;
        }
    }

    onLineAmount(inv, ev) {
        this.state.manual = true;
        const value = parseFloat(String(ev.target.value).replace(/[^0-9.]/g, ""));
        inv.amount = isNaN(value) ? 0 : Math.min(value, inv.open);
        inv.picked = inv.amount > 0;
    }

    get allocated() {
        return Math.round(this.state.invoices.reduce((sum, inv) => sum + (inv.picked ? inv.amount : 0), 0) * 100) / 100;
    }

    get leftOver() {
        return Math.round((this.state.amount - this.allocated) * 100) / 100;
    }

    // ------------------------------------------------------------------ save
    get problem() {
        if (!this.state.partnerId) {
            return _t("Who is the cheque from?");
        }
        if (!(this.state.number || "").trim()) {
            return _t("Type the cheque number.");
        }
        if (this.duplicate) {
            return _t("Cheque %s from this clinic is already recorded.", this.state.number.trim());
        }
        if (!(this.state.amount > 0)) {
            return _t("Type the amount.");
        }
        if (!this.state.journalId) {
            return _t("Choose the bank it will be deposited into.");
        }
        if (this.journal && !this.journal.pdc) {
            return _t("%s has no Cheques in Hand (PDC) account: set it on the journal first.", this.journal.name);
        }
        if (this.leftOver < -0.001) {
            return _t("The invoices take more than the cheque is worth.");
        }
        return "";
    }

    async save(next = false) {
        if (this.problem || this.state.saving) {
            return;
        }
        this.state.saving = true;
        try {
            const vals = {
                partner_id: this.state.partnerId,
                cheque_number: this.state.number.trim(),
                cheque_date: serializeDate(this.state.chequeDate),
                received_date: serializeDate(this.state.receivedDate),
                bank_name: this.state.bank,
                branch: this.state.branch,
                amount: this.state.amount,
                journal_id: Number(this.state.journalId),
            };
            const allocations = this.state.invoices
                .filter((inv) => inv.picked && inv.amount > 0)
                .map((inv) => ({ move_id: inv.id, amount: inv.amount }));
            const result = await this.orm.call("lab.cheque", "lab_cheque_quick_create", [vals, allocations], {
                receive: true,
            });
            const settled = allocations.length;
            this.notification.add(
                settled
                    ? _t("Cheque %(name)s received: %(count)s invoice(s) settled.", { name: result.name, count: settled })
                    : _t("Cheque %s received, kept as credit on the clinic's account.", result.name),
                { type: "success" }
            );
            this.state.saved.push(result.name);
            if (this.props.onSaved) {
                await this.props.onSaved(result);
            }
            if (!next) {
                this.props.close();
                return;
            }
            // the next cheque of the series: same clinic, bank and amount
            const number = this.state.number.trim();
            this.state.number = nextChequeNumber(number);
            this.state.chequeDate = this.state.chequeDate.plus({ months: 1 });
            await this.load();
        } finally {
            this.state.saving = false;
        }
    }
}
