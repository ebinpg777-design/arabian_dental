/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, onMounted, onWillUnmount, proxy, useProps } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";

// ─────────────────────────────────────────────────────────────────────────────
//  RFQ Vendor Price Comparison View
//  Advanced OWL dashboard for side-by-side vendor analysis, heat-map pricing,
//  inline winner selection, and one-click PO creation.
// ─────────────────────────────────────────────────────────────────────────────

export class RFQComparisonView extends Component {
    static template = "ebshel_purchase_request.RFQComparisonView";
    props = useProps(standardActionServiceProps);

    setup() {
        this.orm            = useService("orm");
        this.actionService  = useService("action");
        this.notification   = useService("notification");

        // Name our breadcrumb entry (client actions default to "Unnamed" unless
        // they set a display name explicitly).
        if (this.env.config?.setDisplayName) {
            this.env.config.setDisplayName(this.props.action.name || _t("Vendor Comparison"));
        }

        this.state = proxy({
            loading:             true,
            error:               null,
            data:                null,
            sortBy:              "rank",        // "rank" | "price" | "delivery" | "score" | "smart"
            hoveredVendorId:     null,
            awardVendorId:       null,          // id of vendor being awarded
            justification:       "",
            submitting:          false,
            showVendorDetail:    null,          // vendor id shown in detail popover
            animateIn:           false,
            // ── Vendor search & spotlight ──
            vendorSearch:        "",
            focusVendorId:       null,        // vendor "spotlighted" across the table
            // ── What-if: vendors excluded from the price analysis ──
            excluded:            {},          // vendorId -> true
            showRadar:           false,       // multi-dimension radar panel
            // ── Allocate & Order mode ──
            allocateMode:        false,
            alloc:               {},            // "lineId:vendorId" -> qty
            generating:          false,
            // ── Award approval ──
            showReject:          false,
            rejectReason:        "",
        });

        // Non-reactive lookup indexes (rebuilt once per data load) and the
        // entrance-animation timer (cleared on unmount).
        this._vendorById = new Map();
        this._lineStats = new Map();
        this._sortCacheKey = null;
        this._sortedCache = [];
        this._animTimer = null;

        onMounted(() => {
            this._loadData();
        });
        onWillUnmount(() => {
            if (this._animTimer) { clearTimeout(this._animTimer); this._animTimer = null; }
        });
    }

    // ─── Data Loading ─────────────────────────────────────────────────────────

    get rfqId() {
        // Prefer action.params (preserved when the action is restored from a
        // breadcrumb / URL); fall back to context for the initial open.
        return this.props.action.params?.rfq_id ?? this.props.action.context?.rfq_id;
    }

    async _loadData() {
        if (!this.rfqId) {
            this.state.error = "No RFQ ID provided in context.";
            this.state.loading = false;
            return;
        }
        try {
            const data = await this.orm.call(
                "ebshel.purchase.rfq",
                "get_comparison_data",
                [[this.rfqId]]
            );
            this.state.data = data;
            this._buildIndex();
            // Trigger entrance animation on next tick
            if (this._animTimer) clearTimeout(this._animTimer);
            this._animTimer = setTimeout(() => { this.state.animateIn = true; }, 50);
        } catch (e) {
            this.state.error = e.message || "Failed to load comparison data.";
        } finally {
            this.state.loading = false;
        }
    }

    /** Build O(1) lookup indexes once per load so per-cell template helpers
     *  don't rescan every vendor on every render (was O(lines × vendors²)). */
    _buildIndex() {
        this._vendorById = new Map();
        this._lineStats = new Map();
        this._sortCacheKey = null;
        const d = this.state.data;
        if (!d) return;
        for (const v of d.vendors) this._vendorById.set(v.id, v);
        for (const line of d.lines) {
            const key = String(line.id);
            let best = Infinity, worst = 0;
            for (const v of d.vendors) {
                if (this.state.excluded[v.id]) continue;   // what-if: skip excluded
                const ld = v.lines[key];
                const p = ld && ld.price_unit ? ld.price_unit : 0;
                if (p > 0) {
                    if (p < best) best = p;
                    if (p > worst) worst = p;
                }
            }
            this._lineStats.set(key, { best: best === Infinity ? 0 : best, worst });
        }
    }

    // ─── What-if: exclude / disqualify a vendor from the analysis ──────────────

    isExcluded(vendorId) {
        return !!this.state.excluded[vendorId];
    }

    toggleExclude(vendorId) {
        const ex = { ...this.state.excluded };
        if (ex[vendorId]) {
            delete ex[vendorId];
        } else {
            ex[vendorId] = true;
        }
        this.state.excluded = ex;
        this._buildIndex();   // recompute best/worst over the remaining vendors
    }

    clearExclusions() {
        this.state.excluded = {};
        this._buildIndex();
    }

    get excludedCount() {
        return Object.keys(this.state.excluded).length;
    }

    /** Lowest grand-total among the vendors still in play (what-if). */
    get liveLowestTotal() {
        if (!this.state.data) return 0;
        let min = Infinity;
        for (const v of this.state.data.vendors) {
            if (this.state.excluded[v.id]) continue;
            if (v.total > 0 && v.total < min) min = v.total;
        }
        return min === Infinity ? 0 : min;
    }

    // ─── Sorted / Filtered Vendors ────────────────────────────────────────────

    get sortedVendors() {
        if (!this.state.data) return [];
        // Memoised: only re-sort when the sort key changes (data is static
        // between loads). Avoids re-sorting on every getter access per render.
        if (this._sortCacheKey !== this.state.sortBy) {
            const v = [...this.state.data.vendors];
            switch (this.state.sortBy) {
                case "price":    v.sort((a, b) => a.total - b.total); break;
                case "delivery": v.sort((a, b) => (a.delivery_days || 999) - (b.delivery_days || 999)); break;
                case "score":    v.sort((a, b) => b.score - a.score); break;
                default:         v.sort((a, b) => a.rank - b.rank);
            }
            this._sortedCache = v;
            this._sortCacheKey = this.state.sortBy;
        }
        return this._sortedCache;
    }

    /** Vendors actually shown after the name search. */
    get visibleVendors() {
        let v = this.sortedVendors;
        const q = (this.state.vendorSearch || "").trim().toLowerCase();
        if (q) v = v.filter(x => (x.vendor_name || "").toLowerCase().includes(q));
        return v;
    }

    get hiddenVendorCount() {
        if (!this.state.data) return 0;
        return this.state.data.vendors.length - this.visibleVendors.length;
    }

    get receivedVendors() {
        if (!this.state.data) return [];
        return this.state.data.vendors.filter(v => v.state === "received" || v.state === "selected");
    }

    onSearchVendor(ev) { this.state.vendorSearch = ev.target.value; }
    clearVendorSearch() { this.state.vendorSearch = ""; }

    // ─── Vendor spotlight (click a card to highlight it across the table) ──────

    toggleFocus(vendorId) {
        this.state.focusVendorId = this.state.focusVendorId === vendorId ? null : vendorId;
    }
    clearFocus() { this.state.focusVendorId = null; }

    /** True when a spotlight is active and this vendor is NOT the chosen one. */
    isDimmed(vendorId) {
        return this.state.focusVendorId !== null && this.state.focusVendorId !== vendorId;
    }
    isFocused(vendorId) {
        return this.state.focusVendorId === vendorId;
    }
    get focusedVendor() {
        if (this.state.focusVendorId === null) return null;
        return this._vendorById.get(this.state.focusVendorId) || null;
    }

    // ─── "vs average" good-deal signal ─────────────────────────────────────────

    /** Signed % a vendor's total sits relative to the average quote.
     *  Negative = cheaper than average (a good deal). null if not comparable. */
    vsAveragePct(vendor) {
        const avg = this.state.data?.avg_total || 0;
        if (!avg || !vendor.total) return null;
        return Math.round(((vendor.total - avg) / avg) * 100);
    }

    // ─── CSV export ─────────────────────────────────────────────────────────────

    onExportCsv() {
        const data = this.state.data;
        if (!data) return;
        const esc = (val) => {
            const s = (val === undefined || val === null) ? "" : String(val);
            return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
        };
        const vendors = this.sortedVendors;
        const rows = [];
        // Header
        rows.push(["Product", "Qty", "UoM", ...vendors.map(v => v.vendor_name)].map(esc).join(","));
        // One row per line with each vendor's unit price
        for (const line of data.lines) {
            const cells = [line.product_name, line.qty, line.uom];
            for (const v of vendors) {
                const ld = v.lines[line.id] || v.lines[String(line.id)];
                cells.push(ld && ld.price_unit ? ld.price_unit : "");
            }
            rows.push(cells.map(esc).join(","));
        }
        // Footer totals + meta
        rows.push([]);
        rows.push(["Grand Total", "", "", ...vendors.map(v => v.total)].map(esc).join(","));
        rows.push(["Delivery (days)", "", "", ...vendors.map(v => v.delivery_days || "")].map(esc).join(","));
        rows.push(["Coverage", "", "", ...vendors.map(v => `${v.coverage}%`)].map(esc).join(","));
        rows.push(["Rank", "", "", ...vendors.map(v => v.rank)].map(esc).join(","));

        const csv = rows.join("\n");
        const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = `comparison_${(data.rfq_name || "rfq").replace(/[^\w-]/g, "_")}.csv`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
        this.notification.add("Comparison exported to CSV.", { type: "success" });
    }

    /** The full vendor object behind the server-side recommendation, if any. */
    get recommendedVendor() {
        const reco = this.state.data?.recommendation;
        if (!reco) return null;
        return this._vendorById.get(reco.vendor_id) || null;
    }

    // ─── Multi-dimension radar (Price · Delivery · Coverage · Score) ───────────

    toggleRadar() { this.state.showRadar = !this.state.showRadar; }

    get radarAxes() { return ["Price", "Delivery", "Coverage", "Score"]; }

    /** Geometry + per-vendor polygons for the top vendors, normalised 0..1 on
     *  each axis (1 = best). Excluded vendors are left out. */
    get radarData() {
        const d = this.state.data;
        if (!d) return null;
        const active = d.vendors.filter(v => !this.state.excluded[v.id] && v.total > 0);
        if (active.length < 2) return null;

        const minT = Math.min(...active.map(v => v.total));
        const days = active.map(v => v.delivery_days || 0).filter(x => x > 0);
        const minD = days.length ? Math.min(...days) : 0;
        const maxScore = Math.max(...active.map(v => v.score), 1);
        const top = [...active].sort((a, b) => (a.rank || 999) - (b.rank || 999)).slice(0, 3);

        const axisVal = (v) => [
            v.total > 0 ? minT / v.total : 0,
            v.delivery_days > 0 ? minD / v.delivery_days : (minD ? 0.2 : 1),
            (v.coverage || 0) / 100,
            v.score / maxScore,
        ];

        const cx = 110, cy = 110, R = 78, N = 4;
        const ang = (i) => (-90 + i * (360 / N)) * Math.PI / 180;
        const pt = (val, i) => [cx + R * val * Math.cos(ang(i)), cy + R * val * Math.sin(ang(i))];
        const poly = (vals) => vals.map((val, i) =>
            pt(Math.max(0.04, val), i).map(n => n.toFixed(1)).join(",")).join(" ");

        const rings = [0.25, 0.5, 0.75, 1].map(f =>
            Array.from({ length: N }, (_, i) => pt(f, i).map(n => n.toFixed(1)).join(",")).join(" "));
        const axisLines = Array.from({ length: N }, (_, i) => {
            const [x, y] = pt(1, i);
            return { x: x.toFixed(1), y: y.toFixed(1) };
        });
        const axisLabels = this.radarAxes.map((lbl, i) => {
            const [x, y] = pt(1.22, i);
            return { lbl, x: x.toFixed(1), y: y.toFixed(1) };
        });
        const colors = ["#4f46e5", "#16a34a", "#d97706"];
        const polys = top.map((v, idx) => ({
            id: v.id,
            name: v.vendor_name,
            color: colors[idx] || "#64748b",
            points: poly(axisVal(v)),
        }));
        return { rings, axisLines, axisLabels, polys, cx, cy };
    }

    /** Synthesised sourcing recommendation: single-source vs split, with the
     *  saving each strategy unlocks. A clear, at-a-glance decision aid. */
    get sourcingDecision() {
        const d = this.state.data;
        if (!d || !d.recommendation) return null;
        const split = d.split_savings || 0;
        const best = d.best_total || 0;
        // Split is worth recommending only if it beats single-sourcing by >1%.
        const splitWorthwhile = best > 0 && split > 0 && (split / best) > 0.01;
        if (splitWorthwhile) {
            return {
                mode: "split",
                title: "Split across vendors",
                detail: `Award each line to its cheapest vendor to spend ${this.formatMoney(d.split_total)} ` +
                        `— ${this.formatMoney(split)} less than single-sourcing.`,
                cta: "Allocate & Order",
            };
        }
        return {
            mode: "single",
            title: `Single-source from ${d.recommendation.vendor_name}`,
            detail: `Best overall at ${this.formatMoney(d.recommendation.total)}` +
                    (d.savings > 0 ? ` — saves ${this.formatMoney(d.savings)} vs the highest quote.` : "."),
            cta: "Award this vendor",
        };
    }

    onDecisionCta() {
        const dec = this.sourcingDecision;
        if (!dec) return;
        if (dec.mode === "split") {
            if (!this.state.allocateMode) this.toggleAllocate();
            this.autoAssignBest();
        } else if (this.state.data.recommendation) {
            this.onStartAward(this.state.data.recommendation.vendor_id);
        }
    }

    // ─── Price Analysis Helpers (O(1) via prebuilt indexes) ────────────────────

    getBestPrice(lineId) {
        return this._lineStats.get(String(lineId))?.best || 0;
    }

    getWorstPrice(lineId) {
        return this._lineStats.get(String(lineId))?.worst || 0;
    }

    getLineData(lineId, vendorId) {
        const vendor = this._vendorById.get(vendorId);
        return vendor ? (vendor.lines[String(lineId)] || null) : null;
    }

    /** Returns 0 (best/green) → 1 (worst/red) for heat-map coloring. */
    getCellHeat(lineId, vendorId) {
        const price = this.getLineData(lineId, vendorId)?.price_unit || 0;
        if (!price) return null;
        const { best, worst } = this._lineStats.get(String(lineId)) || { best: 0, worst: 0 };
        if (worst === best) return 0;
        return (price - best) / (worst - best);
    }

    /** Inline CSS style for heat-map cell background. */
    getCellStyle(lineId, vendorId) {
        const heat = this.getCellHeat(lineId, vendorId);
        if (heat === null) return "";
        // Green (120°) → Yellow (60°) → Red (0°)
        const hue = Math.round(120 - heat * 120);
        const sat = 65;
        const lit = heat === 0 ? 88 : (88 - heat * 15);
        return `background-color: hsl(${hue}deg, ${sat}%, ${lit}%)`;
    }

    /** '+X.X%' badge text vs best price, or null if this IS the best price. */
    getPriceDelta(lineId, vendorId) {
        const price = this.getLineData(lineId, vendorId)?.price_unit || 0;
        if (!price) return null;
        const best = this.getBestPrice(lineId);
        if (!best || price <= best) return null;
        return `+${((price - best) / best * 100).toFixed(1)}%`;
    }

    isBestPrice(lineId, vendorId) {
        const price = this.getLineData(lineId, vendorId)?.price_unit || 0;
        if (!price) return false;
        return price === this.getBestPrice(lineId);
    }

    // ─── Allocate & Order (split a line's qty across vendors) ──────────────────

    get canAllocate() {
        return !!this.state.data && !this.state.data.has_po
            && !["po_created", "cancelled"].includes(this.state.data.state);
    }

    toggleAllocate() {
        this.state.allocateMode = !this.state.allocateMode;
        if (!this.state.allocateMode) this.state.alloc = {};
    }

    _allocKey(lineId, vendorId) { return `${lineId}:${vendorId}`; }

    getAlloc(lineId, vendorId) {
        return this.state.alloc[this._allocKey(lineId, vendorId)] || 0;
    }

    setAlloc(lineId, vendorId, value) {
        let qty = parseFloat(value);
        if (isNaN(qty) || qty < 0) qty = 0;
        this.state.alloc = {
            ...this.state.alloc,
            [this._allocKey(lineId, vendorId)]: qty,
        };
    }

    /** Net unit price for a cell (after discount), 0 if no quote. */
    cellUnitPrice(lineId, vendorId) {
        const ld = this.getLineData(lineId, vendorId);
        if (!ld || !ld.price_unit) return 0;
        return ld.price_unit * (1 - (ld.discount || 0) / 100);
    }

    cellAmount(lineId, vendorId) {
        return this.cellUnitPrice(lineId, vendorId) * this.getAlloc(lineId, vendorId);
    }

    lineRequested(line) { return line.qty || 0; }

    lineAssigned(lineId) {
        return this.state.data.vendors.reduce(
            (sum, v) => sum + this.getAlloc(lineId, v.id), 0);
    }

    lineRemaining(line) {
        return Math.round((this.lineRequested(line) - this.lineAssigned(line.id)) * 1e6) / 1e6;
    }

    isLineOver(line) {
        return this.lineAssigned(line.id) > this.lineRequested(line) + 1e-6;
    }

    /** Total order value a vendor would get from current allocation. */
    vendorOrderTotal(vendorId) {
        return (this.state.data.lines || []).reduce(
            (sum, line) => sum + this.cellAmount(line.id, vendorId), 0);
    }

    get hasAnyAllocation() {
        return Object.values(this.state.alloc).some(q => q > 0);
    }

    get hasOverAllocation() {
        return (this.state.data?.lines || []).some(l => this.isLineOver(l));
    }

    get allocatedVendorCount() {
        const ids = new Set();
        for (const [key, qty] of Object.entries(this.state.alloc)) {
            if (qty > 0) ids.add(Number(key.split(":")[1]));
        }
        return ids.size;
    }

    get canGenerate() {
        return this.hasAnyAllocation && !this.hasOverAllocation && !this.state.generating;
    }

    /** One-click: give each line's full requested qty to its cheapest vendor.
     *  New (unregistered) vendors are eligible — they get auto-registered when
     *  the POs are generated. */
    autoAssignBest() {
        const alloc = {};
        for (const line of this.state.data.lines) {
            let best = null, bestPrice = Infinity;
            for (const v of this.state.data.vendors) {
                const p = this.cellUnitPrice(line.id, v.id);
                if (p > 0 && p < bestPrice) { bestPrice = p; best = v; }
            }
            if (best) alloc[this._allocKey(line.id, best.id)] = line.qty;
        }
        this.state.alloc = alloc;
    }

    /** True when the current allocation would create/register at least one new vendor. */
    get allocatesNewVendor() {
        for (const [key, qty] of Object.entries(this.state.alloc)) {
            if (qty > 0) {
                const vid = Number(key.split(":")[1]);
                const v = this.state.data.vendors.find(x => x.id === vid);
                if (v && v.is_new_vendor) return true;
            }
        }
        return false;
    }

    clearAllocation() { this.state.alloc = {}; }

    async onGeneratePOs() {
        if (!this.canGenerate) return;
        this.state.generating = true;
        try {
            const allocations = [];
            for (const [key, qty] of Object.entries(this.state.alloc)) {
                if (qty > 0) {
                    const [lineId, vendorId] = key.split(":").map(Number);
                    allocations.push({ line_id: lineId, vendor_id: vendorId, qty });
                }
            }
            const res = await this.orm.call(
                "ebshel.purchase.rfq",
                "action_generate_split_pos",
                [[this.rfqId]],
                { allocations }
            );
            // Lower-level user → split award submitted for manager approval.
            if (res && res.status === "pending_approval") {
                this.state.allocateMode = false;
                this.notification.add(res.message || "Split award submitted for approval.",
                    { type: "warning" });
                await this._loadData();
                this.state.generating = false;
                return;
            }
            const poIds = (res && res.po_ids) || [];
            const poNames = (res && res.po_names) || [];
            this.notification.add(
                `${poIds.length} purchase order(s) created: ${poNames.join(", ")}`,
                { type: "success" }
            );
            if (!poIds.length) { this.state.generating = false; return; }
            const multi = poIds.length > 1;
            await this.actionService.doAction({
                type: "ir.actions.act_window",
                res_model: "purchase.order",
                name: "Purchase Orders",
                view_mode: multi ? "list,form" : "form",
                views: multi ? [[false, "list"], [false, "form"]] : [[false, "form"]],
                res_id: multi ? false : poIds[0],
                domain: multi ? [["id", "in", poIds]] : false,
            });
        } catch (e) {
            this.notification.add(e.message || "Could not generate purchase orders.", {
                type: "danger", sticky: true,
            });
        } finally {
            this.state.generating = false;
        }
    }

    // ─── Visual Scaling Helpers ───────────────────────────────────────────────

    get maxTotal() {
        if (!this.state.data) return 1;
        let m = 1;
        for (const v of this.state.data.vendors) if (v.total > m) m = v.total;
        return m;
    }

    get maxScore() {
        if (!this.state.data) return 1;
        let m = 1;
        for (const v of this.state.data.vendors) if (v.score > m) m = v.score;
        return m;
    }

    getBarWidth(total) {
        return `${Math.max(4, (total / this.maxTotal) * 100).toFixed(1)}%`;
    }

    getScoreWidth(score) {
        if (!this.state.data) return "0%";
        return `${Math.min(100, (score / this.maxScore) * 100).toFixed(1)}%`;
    }

    getScoreColor(rank) {
        if (rank === 1) return "#16a34a";
        if (rank === 2) return "#2563eb";
        if (rank === 3) return "#7c3aed";
        return "#94a3b8";
    }

    getRankLabel(rank) {
        const medals = { 1: "🥇", 2: "🥈", 3: "🥉" };
        return medals[rank] || `#${rank}`;
    }

    getVendorCardClass(vendor) {
        const parts = ["cmp-scorecard"];
        if (vendor.is_winner)                           parts.push("cmp-scorecard--winner");
        if (this.state.hoveredVendorId === vendor.id)   parts.push("cmp-scorecard--hovered");
        if (this.state.awardVendorId === vendor.id)     parts.push("cmp-scorecard--awarding");
        if (vendor.rank === 1)                          parts.push("cmp-scorecard--best");
        if (vendor.is_new_vendor)                       parts.push("cmp-scorecard--new");
        if (this.state.animateIn)                       parts.push("cmp-scorecard--visible");
        if (this.isFocused(vendor.id))                  parts.push("cmp-scorecard--focused");
        if (this.isDimmed(vendor.id))                   parts.push("cmp-scorecard--dimmed");
        if (this.isExcluded(vendor.id))                 parts.push("cmp-scorecard--excluded");
        return parts.join(" ");
    }

    getVendorColClass(vendor) {
        const parts = ["cmp-vendor-col"];
        if (vendor.is_winner)                           parts.push("cmp-vendor-col--winner");
        if (this.state.hoveredVendorId === vendor.id)   parts.push("cmp-vendor-col--hovered");
        if (vendor.rank === 1)                          parts.push("cmp-vendor-col--best");
        if (this.isFocused(vendor.id))                  parts.push("cmp-vendor-col--focused");
        if (this.isDimmed(vendor.id))                   parts.push("cmp-vendor-col--dimmed");
        return parts.join(" ");
    }

    getTotalCellClass(vendor) {
        const parts = ["cmp-grand-total-cell"];
        if (vendor.rank === 1)                          parts.push("cmp-grand-total-cell--best");
        if (vendor.is_winner)                           parts.push("cmp-grand-total-cell--winner");
        if (this.state.hoveredVendorId === vendor.id)   parts.push("cmp-vendor-col--hovered");
        if (this.isFocused(vendor.id))                  parts.push("cmp-vendor-col--focused");
        if (this.isDimmed(vendor.id))                   parts.push("cmp-vendor-col--dimmed");
        return parts.join(" ");
    }

    // ─── Formatting ───────────────────────────────────────────────────────────

    formatMoney(amount) {
        if (!this.state.data || amount === undefined || amount === null) return "—";
        const sym  = this.state.data.currency_symbol;
        const pos  = this.state.data.currency_position;
        const fmt  = new Intl.NumberFormat(undefined, {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        }).format(amount || 0);
        return pos === "before" ? `${sym} ${fmt}` : `${fmt} ${sym}`;
    }

    formatSavings() {
        return this.formatMoney(this.state.data?.savings || 0);
    }

    // ─── Event Handlers ───────────────────────────────────────────────────────

    onSortBy(sortBy) {
        this.state.sortBy = sortBy;
    }

    onHoverVendor(vendorId) {
        this.state.hoveredVendorId = vendorId;
    }

    onLeaveVendor() {
        this.state.hoveredVendorId = null;
    }

    onStartAward(vendorId) {
        if (this.state.awardVendorId === vendorId) {
            // toggle off
            this.state.awardVendorId = null;
            this.state.justification = "";
        } else {
            this.state.awardVendorId = vendorId;
            this.state.justification = "";
        }
    }

    onCancelAward() {
        this.state.awardVendorId = null;
        this.state.justification = "";
    }

    async onConfirmAward(createPo = false) {
        if (this.state.awardVendorId === null || this.state.submitting) return;
        // Justification is optional — fall back to a sensible default so the
        // action always proceeds (no silent "nothing happened").
        const justification = (this.state.justification || "").trim()
            || "Selected as the most competitive quotation.";
        const vendorId = this.state.awardVendorId;
        this.state.submitting = true;
        try {
            const result = await this.orm.call(
                "ebshel.purchase.rfq",
                "action_award_winner",
                [[this.rfqId]],
                { quotation_id: vendorId, justification, create_po: createPo }
            );
            this.state.awardVendorId = null;
            this.state.justification = "";
            this.state.animateIn = false;

            // New, unregistered vendor → open the registration wizard.
            if (result && result.status === "need_vendor") {
                await this.actionService.doAction(result.action, {
                    onClose: () => this._loadData(),
                });
                return;
            }
            // Awarded + PO created in one step → open the PO.
            if (result && result.status === "po_created") {
                this.notification.add(`Vendor awarded — Purchase Order ${result.po_name} created.`,
                    { type: "success" });
                await this.actionService.doAction({
                    type: "ir.actions.act_window",
                    res_model: "purchase.order",
                    res_id: result.po_id,
                    view_mode: "form",
                    views: [[false, "form"]],
                });
                return;
            }
            // Lower-level user → award submitted for manager approval.
            if (result && result.status === "pending_approval") {
                this.notification.add(result.message || "Award submitted for approval.",
                    { type: "warning" });
                await this._loadData();
                return;
            }
            this.notification.add("Vendor awarded successfully!", { type: "success" });
            await this._loadData();
        } catch (e) {
            this.notification.add(e.message || "Failed to award vendor.", {
                type: "danger",
                sticky: true,
            });
        } finally {
            this.state.submitting = false;
        }
    }

    onBackToRFQ() {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "ebshel.purchase.rfq",
            view_mode: "form",
            res_id: this.rfqId,
            views: [[false, "form"]],
        });
    }

    async onCreatePO() {
        await this.actionService.doAction({
            name: "Create Purchase Order",
            type: "ir.actions.act_window",
            res_model: "ebshel.pr.select.winner.wizard",
            view_mode: "form",
            target: "new",
            context: { default_rfq_id: this.rfqId },
        });
        await this._loadData();
    }

    async approveAward() {
        this.state.submitting = true;
        try {
            const res = await this.orm.call(
                "ebshel.purchase.rfq", "action_approve_award", [[this.rfqId]]);
            const names = (res && res.po_names) || [];
            this.notification.add(
                `Award approved — purchase order(s) created: ${names.join(", ")}`,
                { type: "success" });
            const ids = (res && res.po_ids) || [];
            if (ids.length) {
                const multi = ids.length > 1;
                await this.actionService.doAction({
                    type: "ir.actions.act_window",
                    res_model: "purchase.order",
                    name: "Purchase Orders",
                    view_mode: multi ? "list,form" : "form",
                    views: multi ? [[false, "list"], [false, "form"]] : [[false, "form"]],
                    res_id: multi ? false : ids[0],
                    domain: multi ? [["id", "in", ids]] : false,
                });
            } else {
                await this._loadData();
            }
        } catch (e) {
            this.notification.add(e.message || "Could not approve the award.",
                { type: "danger", sticky: true });
        } finally {
            this.state.submitting = false;
        }
    }

    async rejectAward() {
        const reason = (this.state.rejectReason || "").trim();
        this.state.submitting = true;
        try {
            await this.orm.call(
                "ebshel.purchase.rfq", "action_reject_award", [[this.rfqId]],
                { reason });
            this.state.rejectReason = "";
            this.state.showReject = false;
            this.notification.add("Award proposal rejected.", { type: "info" });
            await this._loadData();
        } catch (e) {
            this.notification.add(e.message || "Could not reject the award.",
                { type: "danger", sticky: true });
        } finally {
            this.state.submitting = false;
        }
    }

    toggleReject() {
        this.state.showReject = !this.state.showReject;
    }

    async onViewPO() {
        // Delegate to the backend action so a split award (several POs) opens the
        // full list, not just the first PO. Fall back to the single PO id only if
        // the server hands back nothing.
        const action = await this.orm.call(
            "ebshel.purchase.rfq", "action_view_purchase_order", [[this.rfqId]]);
        if (action && action.type) {
            await this.actionService.doAction(action);
        } else if (this.state.data?.po_id) {
            await this.actionService.doAction({
                type: "ir.actions.act_window",
                res_model: "purchase.order",
                view_mode: "form",
                res_id: this.state.data.po_id,
                views: [[false, "form"]],
            });
        }
    }

    async onPrint() {
        // Generate the professional B&W comparison PDF (not a screenshot of the
        // window). The server returns a proper report action to download.
        try {
            const action = await this.orm.call(
                "ebshel.purchase.rfq", "action_print_comparison", [[this.rfqId]]);
            await this.actionService.doAction(action);
        } catch (e) {
            this.notification.add(e.message || "Could not generate the comparison PDF.",
                { type: "danger", sticky: true });
        }
    }

    async onRefresh() {
        this.state.animateIn = false;
        this.state.loading = true;
        await this._loadData();
    }
}

registry.category("actions").add("ebshel_rfq_comparison", RFQComparisonView);
