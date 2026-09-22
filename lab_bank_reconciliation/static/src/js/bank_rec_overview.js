/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { formatMonetary } from "@web/views/fields/formatters";

/**
 * The banks overview: one card per bank journal, saying how far its books have been
 * checked against the passbook, and one click into reconciling it.
 */
export class BankRecOverview extends Component {
    static template = "lab_bank_reconciliation.Overview";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({ data: null, busy: false });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.data = await this.orm.call("bank.reconciliation", "get_overview", []);
    }

    get companies() {
        const groups = [];
        for (const card of this.state.data.cards) {
            let group = groups.find((g) => g.id === card.company_id);
            if (!group) {
                group = { id: card.company_id, name: card.company, cards: [] };
                groups.push(group);
            }
            group.cards.push(card);
        }
        return groups;
    }

    /** A 12-week line of outstanding entries, and the change over the last four weeks. */
    spark(trend) {
        if (!trend || trend.length < 2) {
            return null;
        }
        const counts = trend.map((point) => point.count);
        const max = Math.max(...counts, 1);
        const [width, height] = [120, 28];
        const points = counts.map((count, i) =>
            `${((i * width) / (counts.length - 1)).toFixed(1)},${(height - 2 - (count / max) * (height - 4)).toFixed(1)}`
        ).join(" ");
        const last = counts[counts.length - 1];
        const change = last - counts[Math.max(0, counts.length - 5)];
        return { points, last, change, width, height };
    }

    money(value, currencyId) {
        return formatMonetary(value || 0, { currencyId });
    }

    async reconcile(card) {
        this.state.busy = true;
        try {
            const action = await this.orm.call("bank.reconciliation", "open_bank", [card.journal_id]);
            await this.action.doAction(action);
        } catch (error) {
            this.notification.add(error.data?.message || error.message, { type: "danger" });
        } finally {
            this.state.busy = false;
        }
    }

    history(card) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: `${card.journal}: statements`,
            res_model: "bank.reconciliation",
            views: [[false, "list"], [false, "form"]],
            domain: [["journal_id", "=", card.journal_id]],
        });
    }

    statements() {
        this.action.doAction("lab_bank_reconciliation.action_bank_reconciliation");
    }
}

registry.category("actions").add("bank_reconciliation_overview", BankRecOverview);
