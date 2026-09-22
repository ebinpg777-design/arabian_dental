/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

/**
 * Production reports, in two halves.
 *
 *   RIGHT NOW   the backlog: where the open work is standing, how many hours of it,
 *               how old it is. Read from the routing and the order dates, so it is
 *               never blank.
 *   WHAT WE DID weeks, people, stations, redo, pace - read from the board's own
 *               stamps, so it is zero until the floor scans.
 *
 * It opens on "right now" deliberately. The other half is the more interesting
 * screen once the board is in use, but a page that opens on four zeroes teaches its
 * reader to stop opening it. (client, 2026-08-28)
 */
export class LabMrpReport extends Component {
    static template = "lab_workcenter_scan.MrpReport";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({
            weeks: 8, data: null, loading: true, half: "now",
            // The targets half: its own period, its own payload, its own spinner -
            // read on first visit, not with the page. (client, 2026-09-09)
            targets: null, targetsBusy: false, tFrom: null, tTo: null, tSpan: "week",
        });
        onWillStart(() => this.load());
    }

    // ------------------------------------------------------------ charts
    /**
     * A donut as stroked circles: each segment is one circle with a dash the
     * length of its share, offset to where the previous one ended. No arc
     * arithmetic, and a single 100% segment is simply a full ring.
     * Returns [{key, label, value, pct, dash, offset, cls}].
     */
    donut(items, valueKey, labelKey = "name") {
        const C = 2 * Math.PI * 40;
        const total = items.reduce((a, r) => a + (r[valueKey] || 0), 0);
        let offset = 0;
        return items.map((r, i) => {
            const share = total ? (r[valueKey] || 0) / total : 0;
            const seg = {
                key: r.key || r.id || i,
                label: r[labelKey],
                value: r[valueKey] || 0,
                pct: Math.round(share * 1000) / 10,
                dash: `${(share * C).toFixed(2)} ${(C - share * C).toFixed(2)}`,
                offset: (-offset).toFixed(2),
                cls: r.cls || `o_mr_c${i % 8}`,
                row: r,
            };
            offset += share * C;
            return seg;
        });
    }

    /** A person's bar, split into their own work and what they finished for others. */
    personBar(p) {
        const peak = Math.max(1, ...this.state.data.people.map((r) => r.jobs));
        const own = Math.max(0, p.jobs - (p.finished_for_others || 0));
        return {
            own: (own / peak) * 100,
            others: ((p.finished_for_others || 0) / peak) * 100,
        };
    }

    /** The daily done-against-target bars share one scale. */
    dailyPct(n) {
        const peak = Math.max(1, (this.state.targets && this.state.targets.daily_peak) || 0);
        return Math.max(n ? 2 : 0, (n / peak) * 100);
    }

    /** The redo overlay's height, on the same scale as the finished bar. */
    redoPct(week) {
        const peak = Math.max(1, ...this.state.data.weeks.map((w) => w.jobs));
        return Math.max(2, Math.round((week.redos * 100) / peak));
    }

    /** Activity bars share one scale across the fortnight. */
    actPct(n) {
        return Math.max(n ? 4 : 0,
                        Math.round((n * 100) / Math.max(1, this.state.data.activity.peak)));
    }

    /** A technician's weeks as a tiny polyline. */
    sparkPoints(values) {
        const vals = values || [];
        if (!vals.length) {
            return "";
        }
        const max = Math.max(...vals, 1);
        const step = vals.length > 1 ? 80 / (vals.length - 1) : 0;
        return vals
            .map((v, i) => `${(i * step).toFixed(1)},${(20 - (v / max) * 18).toFixed(1)}`)
            .join(" ");
    }

    /** The period as plain text - the lab runs on WhatsApp. */
    async copySummary() {
        const d = this.state.data;
        const lines = [
            `Production — since ${d.from}`,
            `Finished: ${d.totals.jobs} operations by ${d.totals.people} people` +
                ` (${d.totals.hands_on} h hands-on)`,
            `Started again: ${d.redo.total}` +
                (d.first_pass !== null ? ` — right first time ${d.first_pass}%` : ""),
            `Live in the lab now: ${d.backlog.jobs} cases, ${d.backlog.hours} h queued`,
        ];
        for (const p of d.people.slice(0, 8)) {
            lines.push(`- ${p.name}: ${p.jobs} finished, ${p.per_job} min/job`);
        }
        await navigator.clipboard.writeText(lines.join("\n"));
        this.notification.add("Summary copied. Paste it anywhere.",
                              { type: "success" });
    }

    async load(weeks) {
        this.state.loading = true;
        if (weeks) {
            this.state.weeks = weeks;
        }
        try {
            this.state.data = await this.orm.call("lab.mrp.report", "get_mrp_report", [
                this.state.weeks,
            ]);
        } finally {
            this.state.loading = false;
        }
    }

    show(half) {
        this.state.half = half;
        if (half === "targets" && !this.state.targets && !this.state.targetsBusy) {
            this.loadTargets();
        }
    }

    // ------------------------------------------------------------------ targets
    async loadTargets(from, to) {
        this.state.targetsBusy = true;
        try {
            const data = await this.orm.call("lab.mrp.report", "get_targets",
                                             [from || this.state.tFrom || false,
                                              to || this.state.tTo || false]);
            this.state.targets = data;
            this.state.tFrom = data.from;
            this.state.tTo = data.to;
        } finally {
            this.state.targetsBusy = false;
        }
    }

    /** ISO date arithmetic without a Date object at midnight in the wrong zone. */
    shiftIso(iso, days) {
        const [y, m, d] = iso.split("-").map((n) => +n);
        const moved = new Date(y, m - 1, d + days);
        const pad = (n) => String(n).padStart(2, "0");
        return `${moved.getFullYear()}-${pad(moved.getMonth() + 1)}-${pad(moved.getDate())}`;
    }

    /** A week or a month back or forward, keeping the same span. */
    stepTargets(direction) {
        const t = this.state.targets;
        if (!t) {
            return;
        }
        const span = t.days.length;
        const from = this.shiftIso(t.from, direction * span);
        this.loadTargets(from, this.shiftIso(from, span - 1));
    }

    /** This week (Monday to Sunday) or this month, from today. */
    spanTargets(span) {
        this.state.tSpan = span;
        const now = new Date();
        const pad = (n) => String(n).padStart(2, "0");
        const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
        if (span === "month") {
            const first = new Date(now.getFullYear(), now.getMonth(), 1);
            const last = new Date(now.getFullYear(), now.getMonth() + 1, 0);
            this.loadTargets(iso(first), iso(last));
        } else {
            const monday = new Date(now);
            monday.setDate(now.getDate() - ((now.getDay() + 6) % 7));
            const sunday = new Date(monday);
            sunday.setDate(monday.getDate() + 6);
            this.loadTargets(iso(monday), iso(sunday));
        }
    }

    /** One person's week, as the work orders it was made of. */
    async openPersonCases(person) {
        const t = this.state.targets;
        const action = await this.orm.call("lab.mrp.report", "open_person_cases",
                                           [person.id, t.from, t.to]);
        this.action.doAction(action);
    }

    /** "Mon 7 · 9 of 8 · met" for the cell's tooltip. */
    cellTitle(day, cell) {
        if (cell.state === "future") {
            return `${day.label} · target ${cell.target}`;
        }
        if (cell.state === "none") {
            return `${day.label} · nothing`;
        }
        if (cell.state === "free") {
            return `${day.label} · ${cell.done} done, no target set`;
        }
        return `${day.label} · ${cell.done} of ${cell.target} · ${cell.state}`;
    }

    // ------------------------------------------------------------------ formatting
    /** Minutes read as hours once they stop being a handful. */
    hours(minutes) {
        const m = minutes || 0;
        return m >= 90 ? `${(m / 60).toFixed(1)} h` : `${m.toFixed(0)} min`;
    }

    /** Hours as days of work, which is the unit a promise is made in. */
    workdays(hours) {
        const days = (hours || 0) / 8;
        if (!days) {
            return "";
        }
        return days >= 10 ? `${Math.round(days)} days` : `${days.toFixed(1)} days`;
    }

    get backlog() {
        return (this.state.data && this.state.data.backlog) || null;
    }

    get promises() {
        return (this.state.data && this.state.data.promises) || null;
    }

    get radar() {
        return (this.state.data && this.state.data.radar) || null;
    }

    get rot() {
        return (this.state.data && this.state.data.rot) || null;
    }

    get doctors() {
        return (this.state.data && this.state.data.doctors) || [];
    }

    promiseCount(key) {
        const rows = (this.promises && this.promises.rows) || [];
        const row = rows.find((r) => r.key === key);
        return row ? row.count : 0;
    }

    /** Thousands, so a queue of 1,005 does not read as 1005. */
    fmt(n) {
        return (n || 0).toLocaleString();
    }

    async drillPromise(bucket) {
        const action = await this.orm.call("lab.mrp.report", "open_promise", [bucket]);
        this.action.doAction(action);
    }

    async drillDoctor(partnerId) {
        const action = await this.orm.call("lab.mrp.report", "open_doctor", [partnerId]);
        this.action.doAction(action);
    }

    /** Days as the lab says them: "11 d", "1.5 d", or a dash. */
    days(value) {
        if (value === null || value === undefined) {
            return "—";
        }
        return value >= 10 ? `${Math.round(value)} d` : `${Number(value).toFixed(1)} d`;
    }

    // ------------------------------------------------------------------ drills
    async drill(week, userId) {
        const action = await this.orm.call("lab.mrp.report", "open_operations", [
            week || false,
            userId || false,
        ]);
        this.action.doAction(action);
    }

    async drillBacklog(workcenterId, age) {
        const action = await this.orm.call("lab.mrp.report", "open_backlog", [
            workcenterId || false,
            age || false,
        ]);
        this.action.doAction(action);
    }

    async drillRedo(reasonId) {
        const action = await this.orm.call("lab.mrp.report", "open_redo", [
            reasonId || false,
            this.state.weeks,
        ]);
        this.action.doAction(action);
    }

    openCase(id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "mrp.production",
            res_id: id,
            views: [[false, "form"]],
        });
    }
}

registry.category("actions").add("lab_mrp_report", LabMrpReport);
