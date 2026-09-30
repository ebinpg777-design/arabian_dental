/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

/**
 * The technician day: every person a row, every slot a cell.
 *
 *   Heatmap   how full each slot was (hands on a job), how many jobs were handed on
 *             in it, and whether the person stood idle while work waited at their
 *             stations - the one pattern a lead can fix today.
 *   Gantt     the same day as bars, one lane per job so "seven at once" reads as
 *             seven bars: attendance behind, jobs on top coloured by station and
 *             labelled with the sale order and the patient, the accept and hand-on
 *             marks, and a time-use bar per person (on jobs / idle while work
 *             waited / present with nothing on the bench).
 *   Floor     the whole floor per slot: people busy, jobs handed on, work waiting.
 *
 * Minutes arrive counted from the lab's local midnight, so nothing here does
 * timezone arithmetic.
 */
export class LabTechDay extends Component {
    static template = "lab_workcenter_scan.TechDay";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        const params = (this.props.action && this.props.action.params) || {};
        this.state = useState({
            day: params.day || null, slot: 60, view: "heat", stations: [], rostered: false, search: "",
            data: null, loading: true, drawer: null, stationMenu: false, sort: "activity",
        });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call("lab.tech.day", "get_day", [], {
                day: this.state.day, slot: this.state.slot, station_ids: this.state.stations, rostered: this.state.rostered,
            });
            this.state.day = this.state.data.day;
        } finally {
            this.state.loading = false;
        }
    }

    // ------------------------------------------------------------ navigation
    shiftDay(n) {
        const d = new Date(this.state.day + "T00:00:00");
        d.setDate(d.getDate() + n);
        this.state.day = this.iso(d);
        this.state.drawer = null;
        this.load();
    }
    goDay(iso) {
        this.state.day = iso;
        this.state.drawer = null;
        this.load();
    }
    today() {
        this.state.day = null;
        this.state.drawer = null;
        this.load();
    }
    iso(d) {
        return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
    }
    setSlot(s) {
        this.state.slot = s;
        this.load();
    }
    toggleStation(id) {
        const i = this.state.stations.indexOf(id);
        if (i >= 0) {
            this.state.stations.splice(i, 1);
        } else {
            this.state.stations.push(id);
        }
        this.load();
    }
    clearStations() {
        this.state.stations.splice(0);
        this.load();
    }
    toggleRostered() {
        this.state.rostered = !this.state.rostered;
        this.load();
    }

    // ------------------------------------------------------------ formatting
    hm(m) {
        if (m === null || m === undefined) {
            return "–";
        }
        const h = Math.floor(m / 60), mm = Math.round(m % 60);
        return `${String(h).padStart(2, "0")}:${String(mm).padStart(2, "0")}`;
    }
    dur(m) {
        if (!m) {
            return "0m";
        }
        const h = Math.floor(m / 60), mm = Math.round(m % 60);
        return h ? `${h}h ${String(mm).padStart(2, "0")}m` : `${mm}m`;
    }
    stationName(id) {
        const s = (this.state.data.stations || []).find((x) => x.id === id);
        return s ? s.name : "";
    }
    hue(id) {
        const s = (this.state.data.stations || []).find((x) => x.id === id);
        return s ? s.hue : 210;
    }
    stationColor(id, light) {
        return light ? `hsl(${this.hue(id)}, 55%, 85%)` : `hsl(${this.hue(id)}, 55%, 42%)`;
    }

    get stripTop() {
        return Math.max(...(this.state.data.strip || []).map((x) => x.jobs), 1);
    }
    slotLabel(m) {
        if (this.state.data.slot >= 60 || m % 60 === 0) {
            return this.hm(m);
        }
        return ":" + String(m % 60).padStart(2, "0");
    }
    openInsight(insight) {
        if (!insight.user_id) {
            return;
        }
        const row = this.state.data.rows.find((r) => r.id === insight.user_id);
        if (row) {
            this.openPerson(row);
        }
    }

    // ------------------------------------------------------------ rows
    get rows() {
        if (!this.state.data) {
            return [];
        }
        const q = this.state.search.trim().toLowerCase();
        let rows = this.state.data.rows.filter((r) => !q || r.name.toLowerCase().includes(q));
        const key = this.state.sort;
        if (key === "name") {
            rows = [...rows].sort((a, b) => a.name.localeCompare(b.name));
        } else if (key === "handed") {
            rows = [...rows].sort((a, b) => b.handed - a.handed);
        } else if (key === "idle") {
            rows = [...rows].sort((a, b) => b.idle_wait - a.idle_wait);
        } else if (key === "util") {
            rows = [...rows].sort((a, b) => (b.utilisation || 0) - (a.utilisation || 0));
        }
        return rows;
    }
    cellStyle(cell) {
        const slot = this.state.data.slot;
        const share = Math.min(1, cell.busy / slot);
        if (cell.state === "off") {
            return "";
        }
        if (cell.state === "blocked") {
            return "background:#fde2e1;";
        }
        if (cell.state === "idle_wait") {
            return "background: repeating-linear-gradient(135deg, #fde7c8 0 5px, #fff4e3 5px 10px);";
        }
        if (cell.state === "idle") {
            return "background:#eef6ee;";
        }
        if (cell.state === "held") {
            return "background: repeating-linear-gradient(45deg, #eef1f5 0 4px, #fff 4px 8px);";
        }
        const l = 92 - Math.round(share * 60);
        return `background:hsl(210, 55%, ${l}%); color:${share > 0.55 ? "#fff" : "#1f3a5f"};`;
    }
    cellTitle(row, cell) {
        const slot = this.state.data.slot;
        const parts = [`${row.name} · ${this.hm(cell.m)}–${this.hm(cell.m + slot)}`];
        parts.push(`on a job ${cell.busy} min of ${slot}`);
        if (cell.present) {
            parts.push(`present ${cell.present} min`);
        }
        if (cell.handed) {
            parts.push(`${cell.handed} handed on`);
        }
        if (cell.accepted) {
            parts.push(`${cell.accepted} picked up`);
        }
        if (cell.idle_wait) {
            parts.push(`idle ${cell.idle_wait} min while work waited`);
        }
        if (cell.parallel > 1) {
            parts.push(`${cell.parallel} jobs at once`);
        }
        if (cell.usual) {
            parts.push(`usual ${cell.usual} handed on`);
        }
        return parts.join("\n");
    }
    trend(row, cell) {
        // only against a real history: with no usual profile every slot would say "up"
        if (!row.usual_handed || (!cell.usual && !cell.handed)) {
            return "";
        }
        if (cell.handed >= cell.usual + 1) {
            return "up";
        }
        if (cell.usual >= 1 && cell.handed + 1 <= cell.usual) {
            return "down";
        }
        return "";
    }
    ring(pct) {
        const r = 11, c = 2 * Math.PI * r, p = Math.max(0, Math.min(100, pct || 0));
        return { r, c, off: c * (1 - p / 100), color: p >= 70 ? "#067647" : p >= 40 ? "#1f6f8b" : "#b45309" };
    }

    // ------------------------------------------------------------ timeline geometry
    get axis() {
        const d = this.state.data;
        const hours = [];
        for (let m = d.lo; m <= d.hi; m += 60) {
            hours.push(m);
        }
        return { lo: d.lo, hi: d.hi, span: Math.max(d.hi - d.lo, 60), hours };
    }
    x(m) {
        const a = this.axis;
        return ((Math.max(a.lo, Math.min(a.hi, m)) - a.lo) / a.span) * 100;
    }
    w(a, b) {
        return Math.max(0.35, this.x(b) - this.x(a));
    }

    // ------------------------------------------------------------ gantt
    /**
     * One lane per job. A bench span and the timer runs inside it are ONE job, drawn
     * as one bar with the runs inset; carried-in work collapses into a single hatched
     * band per person, because a hundred forgotten clocks are one fact, not a hundred
     * lanes. Everything else is packed greedily, earliest first.
     */
    get gantt() {
        if (!this.state.data) {
            return [];
        }
        return this.rows.map((r) => {
            const jobs = new Map();
            const carried = [];
            for (const s of r.segments) {
                if (s.kind === "carried") {
                    carried.push(s);
                    continue;
                }
                let j = jobs.get(s.wo);
                if (!j) {
                    j = { wo: s.wo, seg: s, bench: null, runs: [], a: s.a, b: s.b, open: false };
                    jobs.set(s.wo, j);
                }
                if (s.kind === "bench") {
                    j.bench = s;
                    j.seg = s;
                    j.open = j.open || s.open;
                } else {
                    j.runs.push(s);
                }
                j.a = Math.min(j.a, s.a);
                j.b = Math.max(j.b, s.b);
            }
            const list = [...jobs.values()].sort((p, q) => p.a - q.a || p.b - q.b);
            const ends = [];
            for (const j of list) {
                let lane = ends.findIndex((e) => e <= j.a);
                if (lane < 0) {
                    lane = ends.length;
                    ends.push(0);
                }
                ends[lane] = j.b;
                j.lane = lane;
                // a bench span carries its timer runs inset; a job only a timer put here
                // is drawn as its runs
                j.spans = j.bench ? [j.bench] : j.runs;
                j.inner = j.bench ? j.runs : [];
                j.timed = j.runs.filter((t) => t.kind === "timer").reduce((m, t) => m + (t.b - t.a), 0);
                j.rush = j.seg.priority === "emergency" || j.seg.priority === "urgent";
            }
            const laneOf = {};
            for (const j of list) {
                laneOf[j.wo] = j.lane;
            }
            const band = carried.length
                ? { n: new Set(carried.map((c) => c.wo)).size, a: Math.min(...carried.map((c) => c.a)), b: Math.max(...carried.map((c) => c.b)) }
                : null;
            return {
                row: r, jobs: list, band, bandLane: ends.length,
                lanes: Math.max(1, ends.length + (band ? 1 : 0)),
                events: r.events.map((e) => ({ ...e, lane: laneOf[e.wo] ?? ends.length })),
            };
        });
    }
    laneTop(lane) {
        return 6 + lane * 22;
    }
    trackHeight(g) {
        return this.laneTop(g.lanes) + 4;
    }
    barLabel(j) {
        const s = j.seg;
        const who = s.so || s.mo || "Job";
        return s.patient ? `${who} · ${s.patient}` : who;
    }
    barTitle(j) {
        const s = j.seg, d = this.state.data;
        const lines = [[s.so, s.mo].filter(Boolean).join(" · ") || "Job"];
        const who = [s.patient, d.partners[s.partner_id]].filter(Boolean).join(" · ");
        if (who) {
            lines.push(who);
        }
        lines.push(`${this.stationName(s.station_id)} · ${this.hm(j.a)}–${j.open ? "open" : this.hm(j.b)} (${this.dur(j.b - j.a)})`);
        if (j.inner.length) {
            lines.push(`timer ${this.dur(j.timed)} in ${j.inner.length} run(s)`);
        }
        if (s.expected) {
            lines.push(`costed ${this.dur(s.expected)}`);
        }
        if (j.rush) {
            lines.push(s.priority.toUpperCase());
        }
        return lines.join("\n");
    }
    /** How the person's presence was spent: on jobs, idle while work waited, present with nothing on the bench. */
    use(r) {
        const present = Math.max(r.present, r.busy + r.idle_wait);
        const free = Math.max(0, present - r.busy - r.idle_wait);
        const pct = (m) => (present ? (m / present) * 100 : 0);
        return { present, busy: r.busy, idle: r.idle_wait, free, pb: pct(r.busy), pi: pct(r.idle_wait), pf: pct(free) };
    }
    /** The stations that appear on the chart, so the colours are explained. */
    get stationLegend() {
        const seen = new Set();
        for (const r of this.rows) {
            for (const s of r.segments) {
                if (s.kind !== "carried") {
                    seen.add(s.station_id);
                }
            }
        }
        return (this.state.data.stations || []).filter((st) => seen.has(st.id)).map((st) => ({ id: st.id, name: st.name, color: this.stationColor(st.id) }));
    }

    // ------------------------------------------------------------ floor chart
    get floorChart() {
        const d = this.state.data;
        const f = d.floor;
        if (!f.length) {
            return null;
        }
        const W = 1000, H = 190, PT = 16, PB = 22;
        const n = f.length, bw = W / n;
        const topH = Math.max(...f.map((x) => x.handed), 1);
        const topP = Math.max(...f.map((x) => x.busy_people), 1);
        const topW = Math.max(...f.map((x) => x.waiting), 1);
        const y = (v, top) => PT + (H - PT - PB) * (1 - v / top);
        const bars = f.map((x, i) => ({ x: i * bw + bw * 0.18, w: bw * 0.64, y: y(x.handed, topH), h: H - PB - y(x.handed, topH), v: x.handed, m: x.m }));
        const people = f.map((x, i) => `${i * bw + bw / 2},${y(x.busy_people, topP)}`).join(" ");
        const waitArea = `0,${H - PB} ` + f.map((x, i) => `${i * bw + bw / 2},${y(x.waiting, topW)}`).join(" ") + ` ${W},${H - PB}`;
        const labels = f.map((x, i) => ({ x: i * bw + bw / 2, t: this.hm(x.m), show: n <= 16 || i % Math.ceil(n / 16) === 0 }));
        return { W, H, bars, people, waitArea, labels, topH, topP, topW };
    }

    // ------------------------------------------------------------ drawers
    openCell(row, cell) {
        const slot = this.state.data.slot;
        const a = cell.m, b = cell.m + slot;
        // one line per JOB: a bench span and the timer runs inside it are the same
        // job, and the panel used to list it once per piece (client, 2026-09-30)
        const byJob = new Map();
        for (const s of row.segments) {
            if (s.a >= b || s.b <= a) {
                continue;
            }
            const here = Math.min(s.b, b) - Math.max(s.a, a);
            const j = byJob.get(s.wo) || { seg: s, inSlot: 0, lo: s.a, hi: s.b };
            if (s.kind === "bench" || s.kind === "carried") {
                j.seg = s; // the bench span is what names the job
            }
            j.inSlot = Math.max(j.inSlot, here);
            j.lo = Math.min(j.lo, s.a);
            j.hi = Math.max(j.hi, s.b);
            byJob.set(s.wo, j);
        }
        const jobs = [...byJob.values()].map((j) => ({ ...j.seg, inSlot: j.inSlot, a: j.lo, b: j.hi })).sort((p, q) => p.a - q.a);
        const events = row.events.filter((e) => e.m >= a && e.m < b);
        this.state.drawer = { kind: "cell", row, cell, jobs, events, a, b };
    }
    openPerson(row) {
        const story = [];
        for (const at of row.attendance) {
            story.push({ m: at.a, icon: "fa-sign-in", text: "Checked in", cls: "muted" });
            if (at.b < 1440) {
                story.push({ m: at.b, icon: "fa-sign-out", text: "Checked out", cls: "muted" });
            }
        }
        for (const e of row.events) {
            const seg = row.segments.find((s) => s.wo === e.wo);
            const what = seg ? [seg.so, seg.mo].filter(Boolean).join(" · ") + (seg.patient ? " · " + seg.patient : "") : e.label;
            if (e.kind === "accept") {
                story.push({ m: e.m, icon: "fa-play", text: `Picked up ${what} at ${this.stationName(e.station_id)}`, cls: "", wo: e.wo });
            } else if (e.kind === "handed") {
                const mins = seg && seg.kind !== "carried" ? seg.b - seg.a : null;
                story.push({ m: e.m, icon: "fa-check", text: `Handed on ${what}` + (mins ? ` after ${this.dur(mins)}` : "") + (seg && seg.expected ? ` (costed ${this.dur(seg.expected)})` : ""), cls: "good", wo: e.wo });
            } else if (e.kind === "given") {
                story.push({ m: e.m, icon: "fa-inbox", text: `Given ${what || "a job"} at ${this.stationName(e.station_id)}`, cls: "muted", wo: e.wo });
            }
        }
        if (row.longest_gap) {
            story.push({ m: row.longest_gap.m, icon: "fa-pause", text: `Longest break from jobs: ${this.dur(row.longest_gap.len)}`, cls: "warn" });
        }
        story.sort((p, q) => p.m - q.m);
        const slot = this.state.data.slot;
        const top = Math.max(...row.cells.map((c) => Math.max(c.handed, c.usual)), 1);
        const profile = row.cells.map((c, i) => ({ i, m: c.m, today: c.handed, usual: c.usual, h1: (c.handed / top) * 100, h2: (c.usual / top) * 100 }));
        const byStation = {};
        for (const s of row.segments) {
            if (s.kind === "carried") {
                continue;
            }
            byStation[s.station_id] = (byStation[s.station_id] || 0) + (s.b - s.a);
        }
        const stationMix = Object.entries(byStation).map(([id, m]) => ({ id: Number(id), m })).sort((p, q) => q.m - p.m);
        const mixTotal = stationMix.reduce((t, s) => t + s.m, 0) || 1;
        this.state.drawer = { kind: "person", row, story, profile, slot, stationMix, mixTotal };
    }
    /** A bar on the Gantt: the case behind it, with the way to its orders. */
    openBar(row, j) {
        this.state.drawer = { kind: "job", row, job: j, seg: j.seg, doctor: this.state.data.partners[j.seg.partner_id] || "" };
    }
    closeDrawer() {
        this.state.drawer = null;
    }
    openJob(wo) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "mrp.workorder", res_id: wo, views: [[false, "form"]] });
    }
    openSale(id) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "sale.order", res_id: id, views: [[false, "form"]] });
    }
    openStationFilter() {
        this.state.stationMenu = !this.state.stationMenu;
    }

    // ------------------------------------------------------------ export
    exportCsv() {
        const d = this.state.data;
        const head = ["Technician", "Busy (min)", "Present (min)", "Utilisation %", "Handed on", "Picked up", "Idle while work waited (min)", "Earned (min)"];
        for (const m of d.slots) {
            head.push(`${this.hm(m)} busy`, `${this.hm(m)} handed`);
        }
        const lines = [head];
        for (const r of this.rows) {
            const line = [r.name, r.busy, r.present, r.utilisation ?? "", r.handed, r.accepted, r.idle_wait, r.earned];
            for (const c of r.cells) {
                line.push(c.busy, c.handed);
            }
            lines.push(line);
        }
        const csv = lines.map((l) => l.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(",")).join("\n");
        const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = `technician-day-${d.day}.csv`;
        a.click();
        URL.revokeObjectURL(a.href);
    }
}

registry.category("actions").add("lab_tech_day", LabTechDay);
