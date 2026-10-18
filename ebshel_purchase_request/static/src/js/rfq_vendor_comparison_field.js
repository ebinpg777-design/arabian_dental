/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { formatMonetary } from "@web/views/fields/formatters";

// ─────────────────────────────────────────────────────────────────────────────
//  RFQ Vendor Comparison Field Widget
//  An embeddable one2many widget: ranks vendor quotations as a "podium",
//  surfaces best-price / fastest / best-score badges, and lets the procurement
//  officer crown a winner inline with a single click.
// ─────────────────────────────────────────────────────────────────────────────

export class RfqVendorComparisonField extends Component {
    static template = "ebshel_purchase_request.RfqVendorComparisonField";
    static props = { ...standardFieldProps };

    setup() {
        this.orm          = useService("orm");
        this.action       = useService("action");
        this.notification = useService("notification");
        this.dialog       = useService("dialog");

        this.state = useState({
            busy:        false,
            hoveredId:   null,
            justifyId:   null,   // quotation id whose justification box is open
            justifyText: "",
            justifyMode: "award",  // "award" | "order"
        });
    }

    // ─── Data access ────────────────────────────────────────────────────────

    get list() {
        return this.props.record.data[this.props.name];
    }

    /** Raw records → plain objects with the metrics we need. */
    get vendors() {
        const recs = this.list.records || [];
        return recs.map((r) => {
            const d = r.data;
            // Odoo 19: a many2one in record.data is an object {id, display_name}.
            const vendorName = d.vendor_id ? d.vendor_id.display_name
                             : (d.new_vendor_name || "New Vendor");
            return {
                id:            r.resId,
                record:        r,
                name:          vendorName,
                isNewVendor:   d.is_new_vendor && !d.vendor_id,
                untaxed:       d.amount_untaxed || 0,
                tax:           d.amount_tax || 0,
                total:         d.amount_total || 0,
                deliveryDays:  d.delivery_days || 0,
                paymentTerm:   d.payment_term_id ? d.payment_term_id.display_name : "",
                score:         d.score || 0,
                rank:          d.rank || 0,
                state:         d.state,
                isWinner:      d.is_winner,
                justification: d.winner_justification || "",
                currencyId:    d.currency_id ? d.currency_id.id : false,
            };
        });
    }

    /** All vendor quotations, sorted best-first. Everything is shown — a draft
     *  or zero-total quote still belongs in the comparison so nothing silently
     *  disappears from the tab. */
    get rankedVendors() {
        return [...this.vendors].sort((a, b) => {
            if (a.isWinner !== b.isWinner) return a.isWinner ? -1 : 1;
            if (b.score !== a.score) return b.score - a.score;
            return (a.total || Infinity) - (b.total || Infinity);
        });
    }

    get hasVendors() {
        return this.rankedVendors.length > 0;
    }

    get winner() {
        return this.vendors.find((v) => v.isWinner) || null;
    }

    // ─── Cross-vendor analytics ───────────────────────────────────────────────

    get bestPrice() {
        const totals = this.rankedVendors.map((v) => v.total).filter((t) => t > 0);
        return totals.length ? Math.min(...totals) : 0;
    }

    get worstPrice() {
        const totals = this.rankedVendors.map((v) => v.total).filter((t) => t > 0);
        return totals.length ? Math.max(...totals) : 0;
    }

    get fastestDelivery() {
        const days = this.rankedVendors.map((v) => v.deliveryDays).filter((d) => d > 0);
        return days.length ? Math.min(...days) : 0;
    }

    get bestScore() {
        const scores = this.rankedVendors.map((v) => v.score);
        return scores.length ? Math.max(...scores) : 0;
    }

    get potentialSavings() {
        if (this.rankedVendors.filter((v) => v.total > 0).length < 2) return 0;
        return this.worstPrice - this.bestPrice;
    }

    isBestPrice(v) {
        return v.total > 0 && v.total === this.bestPrice;
    }

    isFastest(v) {
        return v.deliveryDays > 0 && v.deliveryDays === this.fastestDelivery;
    }

    isBestScore(v) {
        return v.score > 0 && v.score === this.bestScore;
    }

    // ─── Visual helpers ───────────────────────────────────────────────────────

    medal(rank) {
        return { 1: "🥇", 2: "🥈", 3: "🥉" }[rank] || `#${rank}`;
    }

    scoreBarWidth(v) {
        const max = this.bestScore || 1;
        return `${Math.min(100, (v.score / max) * 100).toFixed(0)}%`;
    }

    priceBarWidth(v) {
        const max = this.worstPrice || 1;
        return `${Math.min(100, (v.total / max) * 100).toFixed(0)}%`;
    }

    /** Delta vs best price as a "+X.X%" string, or null if this is the best. */
    priceDelta(v) {
        if (!v.total || v.total <= this.bestPrice || !this.bestPrice) return null;
        return `+${(((v.total - this.bestPrice) / this.bestPrice) * 100).toFixed(1)}%`;
    }

    cardClass(v) {
        const c = ["rvc-card"];
        if (v.isWinner)                       c.push("rvc-card--winner");
        else if (v.rank === 1 || this.isBestPrice(v)) c.push("rvc-card--best");
        if (this.state.hoveredId === v.id)    c.push("rvc-card--hover");
        if (v.state === "rejected")           c.push("rvc-card--rejected");
        if (v.isNewVendor)                    c.push("rvc-card--new");
        return c.join(" ");
    }

    fmt(amount, currencyId) {
        return formatMonetary(amount, { currencyId: currencyId || false });
    }

    get readonly() {
        return this.props.readonly;
    }

    // ─── Parent RFQ state (this widget lives on the RFQ form) ─────────────────

    get rfqId() {
        return this.props.record.resId;
    }

    get rfqState() {
        return this.props.record.data.state;
    }

    get hasWinner() {
        return !!this.winner;
    }

    get poRef() {
        // purchase_order_id is loaded by the RFQ form (summary group)
        const po = this.props.record.data.purchase_order_id;
        return po ? { id: po.id, name: po.display_name } : null;
    }

    get hasPO() {
        // True once any PO exists for this RFQ (single or split award).
        return !!this.poRef || this.rfqState === "po_created";
    }

    /** Award is still possible while no PO exists and the RFQ isn't closed. */
    get canAward() {
        return !this.readonly && !this.hasPO
            && !["po_created", "cancelled"].includes(this.rfqState);
    }

    // ─── Actions ──────────────────────────────────────────────────────────────

    onHover(id)  { this.state.hoveredId = id; }
    onLeave()    { this.state.hoveredId = null; }

    /** Open the justification box. mode: "award" (crown only) or "order"
     *  (crown + immediately create the purchase order). */
    openJustify(v, mode = "award") {
        this.state.justifyId   = v.id;
        this.state.justifyMode = mode;
        this.state.justifyText = v.justification || "";
    }

    cancelJustify() {
        this.state.justifyId   = null;
        this.state.justifyText = "";
    }

    async confirmWinner(v) {
        if (this.state.busy) return;
        const createPo = this.state.justifyMode === "order";
        this.state.busy = true;
        try {
            // Persist any pending price edits so the server sees the latest quotes.
            await this.props.record.save();

            const result = await this.orm.call(
                "ebshel.purchase.rfq",
                "action_award_winner",
                [[this.rfqId]],
                {
                    quotation_id: v.id,
                    justification: (this.state.justifyText || "").trim()
                                   || "Selected as the most competitive quotation.",
                    create_po: createPo,
                }
            );

            if (result && result.status === "need_vendor") {
                // New vendor must be registered first → open the wizard.
                await this.action.doAction(result.action, {
                    onClose: () => this.props.record.load(),
                });
                return;
            }

            if (result && result.status === "po_created") {
                this.notification.add(
                    `${v.name} awarded — Purchase Order ${result.po_name} created.`,
                    { type: "success" }
                );
                await this.props.record.load();
                await this.action.doAction({
                    type: "ir.actions.act_window",
                    res_model: "purchase.order",
                    res_id: result.po_id,
                    view_mode: "form",
                    views: [[false, "form"]],
                });
                return;
            }

            // Lower-level user → award submitted to a manager for approval.
            if (result && result.status === "pending_approval") {
                this.notification.add(
                    result.message || `${v.name} submitted for manager approval.`,
                    { type: "warning" });
                await this.props.record.load();
                return;
            }

            this.notification.add(`${v.name} crowned as the winning vendor.`, {
                type: "success",
            });
            await this.props.record.load();
        } catch (e) {
            this.notification.add(e.message || "Could not select winner.", {
                type: "danger",
                sticky: true,
            });
        } finally {
            this.state.busy        = false;
            this.state.justifyId   = null;
            this.state.justifyText = "";
        }
    }

    /** Create the purchase order from the already-crowned winner. */
    async createPO() {
        if (this.state.busy) return;
        this.state.busy = true;
        try {
            await this.props.record.save();
            const action = await this.orm.call(
                "ebshel.purchase.rfq",
                "action_create_purchase_order",
                [[this.rfqId]]
            );
            await this.props.record.load();
            if (action && action.type) {
                await this.action.doAction(action);
            }
        } catch (e) {
            this.notification.add(e.message || "Could not create the purchase order.", {
                type: "danger",
                sticky: true,
            });
        } finally {
            this.state.busy = false;
        }
    }

    /** Open the purchase order(s) for this RFQ. Uses the backend action so a
     *  split award (several POs) opens the full list, not just the first PO. */
    async viewPO() {
        const action = await this.orm.call(
            "ebshel.purchase.rfq", "action_view_purchase_order", [[this.rfqId]]);
        if (action && action.type) {
            await this.action.doAction(action);
        } else if (this.poRef) {
            await this.action.doAction({
                type: "ir.actions.act_window",
                res_model: "purchase.order",
                res_id: this.poRef.id,
                view_mode: "form",
                views: [[false, "form"]],
            });
        }
    }

    /** Open the full quotation form for editing line-level prices. */
    openQuotation(v) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "ebshel.purchase.vendor.quotation",
            res_id: v.id,
            view_mode: "form",
            views: [[false, "form"]],
            target: "new",
        }, {
            onClose: () => this.props.record.load(),
        });
    }
}

export const rfqVendorComparisonField = {
    component: RfqVendorComparisonField,
    supportedTypes: ["one2many"],
    relatedFields: () => [
        { name: "vendor_id",            type: "many2one", relation: "res.partner" },
        { name: "new_vendor_name",      type: "char" },
        { name: "is_new_vendor",        type: "boolean" },
        { name: "amount_untaxed",       type: "monetary" },
        { name: "amount_tax",           type: "monetary" },
        { name: "amount_total",         type: "monetary" },
        { name: "delivery_days",        type: "integer" },
        { name: "payment_term_id",      type: "many2one", relation: "account.payment.term" },
        { name: "score",                type: "float" },
        { name: "rank",                 type: "integer" },
        { name: "state",                type: "selection" },
        { name: "is_winner",            type: "boolean" },
        { name: "winner_justification", type: "text" },
        { name: "currency_id",          type: "many2one", relation: "res.currency" },
    ],
};

registry.category("fields").add("rfq_vendor_comparison", rfqVendorComparisonField);
