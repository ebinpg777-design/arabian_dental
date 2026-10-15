/** @odoo-module **/

import { Component, onMounted, onWillStart, onWillUnmount, proxy, usePlugin, useProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { ActionPlugin } from "@web/webclient/actions/action_plugin";

const REFRESH_MS = 60000;
const LATE_MIN = 15;

/**
 * The technician day: every person a row, every slot a cell.
 *
 *   Gantt     the day as bars, one lane per job so "seven at once" reads as seven
 *             bars: the roster's shift along the top of the lane with the lunch
 *             hatched, attendance behind, jobs on top coloured by station and
 *             labelled with the sale order and the patient, every idle period of
 *             five minutes or more as a block (orange when work waited at the
 *             person's stations), the accept and hand-on marks, and a time-use bar
 *             per person (on jobs / idle while work waited / free / lunch).
 *   Heatmap   how full each slot was (hands on a job), how many jobs were handed on
 *             in it, and whether the person stood idle while work waited.
 *   Floor     the whole floor per slot: people busy, jobs handed on, work waiting.
 *
 * Today redraws itself every minute. (The "Right now" panel that sat above it was
 * taken off the screen; who stands free while work waits is in the insights.)
 *
 * The rows are the technicians named on a work centre. Minutes arrive counted from
 * the lab's local midnight, so nothing here does timezone arithmetic.
 */
export class LabTechDay extends Component {
    static template = "lab_workcenter_scan.TechDay";
    props = useProps();

    setup() {
        this.orm = useService("orm");
        this.action = usePlugin(ActionPlugin);
        const params = (this.props.action && this.props.action.params) || {};
        this.state = proxy({
            day: params.day || null, slot: 60, view: "line", stations: [], people: "roster", search: "",
            data: null, loading: true, drawer: null, stationMenu: false, sort: "activity", showAbsent: false,
        });
        this.loadSeq = 0;
        onWillStart(() => this.load());
        // today redraws itself every minute, so the screen can stay up on the floor
        onMounted(() => {
            this.timer = setInterval(() => {
                if (this.state.data && this.state.data.is_today && !document.hidden && !this.state.loading) {
                    this.load(true);
                }
            }, REFRESH_MS);
        });
        onWillUnmount(() => clearInterval(this.timer));
    }

    /**
     * Every request carries a ticket; only the newest answer is drawn. A refresh
     * that comes back after the lead changed the day, the slot, the stations or the
     * people would otherwise put the old view back.
     */
    async load(quiet = false) {
        const ticket = ++this.loadSeq;
        if (!quiet) {
            this.state.loading = true;
        }
        try {
            const data = await this.orm.call("lab.tech.day", "get_day", [], {
                day: this.state.day, slot: this.state.slot, station_ids: this.state.stations, people: this.state.people,
            });
            if (ticket !== this.loadSeq) {
                return;
            }
            this.state.data = data;
            this.state.day = data.day;
        } catch (error) {
            if (!quiet) {
                throw error;
            }
            // a background refresh that fails - the session expired overnight on the
            // floor screen, the server restarting - waits for the next minute rather
            // than opening an error dialog every minute
            console.warn("Technician day: refresh failed", error);
        } finally {
            if (ticket === this.loadSeq) {
                this.state.loading = false;
            }
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
    setPeople(p) {
        if (this.state.people !== p) {
            this.state.people = p;
            this.load();
        }
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
        const s = (this.state.data.stations || []).find((x) => x.id === Number(id));
        return s ? s.name : "";
    }
    stationNames(ids) {
        return (ids || []).map((id) => this.stationName(id)).filter(Boolean).join(", ");
    }
    hue(id) {
        const s = (this.state.data.stations || []).find((x) => x.id === Number(id));
        return s ? s.hue : 210;
    }
    stationColor(id, light) {
        return light ? `hsl(${this.hue(id)}, 55%, 85%)` : `hsl(${this.hue(id)}, 55%, 42%)`;
    }
    rowOf(uid) {
        return (this.state.data.rows || []).find((r) => r.id === uid) || null;
    }
    shiftText(shift) {
        if (!shift) {
            return "";
        }
        const lunch = shift.breaks.map((b) => `${this.hm(b[0])}–${this.hm(b[1])}`).join(", ");
        return `${this.hm(shift.a)}–${this.hm(shift.b)}` + (lunch ? ` · lunch ${lunch}` : "");
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
        if (insight.people) {
            this.setPeople(insight.people);
            return;
        }
        if (!insight.user_id) {
            return;
        }
        const row = this.rowOf(insight.user_id);
        if (row) {
            this.openPerson(row);
        }
    }
    get absentShown() {
        const a = this.state.data.absent || [];
        return this.state.showAbsent || a.length <= 8 ? a : a.slice(0, 8);
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
            rows = [...rows].sort((a, b) => b.idle_wait - a.idle_wait || b.idle - a.idle);
        } else if (key === "idle_total") {
            rows = [...rows].sort((a, b) => b.idle - a.idle);
        } else if (key === "late") {
            rows = [...rows].sort((a, b) => b.late - a.late || b.early - a.early);
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
    isLate(r) {
        return r.late >= LATE_MIN;
    }
    isEarly(r) {
        return r.early >= LATE_MIN;
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
     * One lane per job. The work order's Start -> End and the Time Tracking runs
     * inside it are ONE job, drawn as one bar with the runs inset; a running job
     * carries its End Date (when it is due) as a dashed outline ahead of it, or as
     * hatching over the part past it. Carried-in work collapses into a single hatched
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
                j.due = j.bench && j.open && j.bench.due !== null && j.bench.due !== undefined ? j.bench.due : null;
                j.clockBy = [...new Set(j.runs.map((t) => t.by).filter((by) => by && by !== r.id))];
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
        const from = s.started || this.hm(j.a);
        const to = j.open ? "running" : s.finished || this.hm(j.b);
        lines.push(`${this.stationName(s.station_id)} · ${from} → ${to} (${this.dur(j.b - j.a)})`);
        if (j.due !== null) {
            lines.push(j.due > j.b
                ? `due ${s.due_label} - ${this.dur(j.due - j.b)} to go`
                : `past its End Date ${s.due_label} by ${this.dur(j.b - j.due)}`);
        }
        if (j.inner.length) {
            lines.push(`time tracked ${this.dur(j.timed)} in ${j.inner.length} run(s)`);
        }
        if (s.expected) {
            lines.push(`costed ${this.dur(s.expected)}`);
        }
        if (j.rush) {
            lines.push(s.priority.toUpperCase());
        }
        return lines.join("\n");
    }
    idleTitle(r, p) {
        const lines = [`${r.name} idle ${this.hm(p.a)}–${this.hm(p.b)} (${this.dur(p.b - p.a)})`];
        lines.push(p.wait ? `work waited at their stations for ${this.dur(p.wait)} of it` : "nothing waiting at their stations");
        lines.push("click for what was waiting");
        return lines.join("\n");
    }
    shiftTitle(r) {
        const s = r.shift;
        const lines = [`On the roster ${this.shiftText(s)}`];
        if (this.isLate(r)) {
            lines.push(`started ${this.hm(r.first_present)}: ${this.dur(r.late)} late`);
        }
        if (this.isEarly(r)) {
            lines.push(`left ${this.hm(r.last_present)}: ${this.dur(r.early)} early`);
        }
        return lines.join("\n");
    }
    /** How the person's presence was spent: on jobs, idle while work waited, free, at lunch, the rest (blocked). */
    use(r) {
        const brk = r.break_min || 0;
        const present = Math.max(r.present, r.busy + r.idle + brk);
        const other = Math.max(0, present - r.busy - r.idle - brk);
        const pct = (m) => (present ? (m / present) * 100 : 0);
        return {
            present, busy: r.busy, wait: r.idle_wait, free: r.idle_free, brk, other,
            pb: pct(r.busy), pw: pct(r.idle_wait), pf: pct(r.idle_free), pk: pct(brk), po: pct(other),
        };
    }
    useTitle(r) {
        const u = this.use(r);
        const parts = [`On jobs ${this.dur(u.busy)}`, `idle while work waited ${this.dur(u.wait)}`, `free, nothing waiting ${this.dur(u.free)}`];
        if (u.brk) {
            parts.push(`lunch ${this.dur(u.brk)}`);
        }
        if (u.other) {
            parts.push(`blocked ${this.dur(u.other)}`);
        }
        parts.push(`present ${this.dur(u.present)}`);
        return parts.join(" · ");
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
            if (at.b < 1440 && !at.open) {
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
        for (const p of row.idle_periods) {
            if (p.b - p.a >= 30) {
                story.push({ m: p.a, icon: "fa-pause", text: `Idle ${this.dur(p.b - p.a)}` + (p.wait ? ` while work waited ${this.dur(p.wait)} of it` : ""), cls: p.wait ? "warn" : "muted", idle: p });
            }
        }
        if (this.isLate(row)) {
            story.push({ m: row.first_present, icon: "fa-clock-o", text: `Started ${this.dur(row.late)} after the roster's ${this.hm(row.shift.a)}`, cls: "warn" });
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
    /** An idle block: what waited at the person's stations meanwhile, and the jobs either side of it. */
    openIdle(row, p) {
        const d = this.state.data;
        const waited = [];
        for (const wc of row.stations) {
            for (const j of d.queue[wc] || []) {
                if (j.a < p.b && j.b > p.a) {
                    waited.push({ ...j, station_id: wc, overlap: Math.min(j.b, p.b) - Math.max(j.a, p.a) });
                }
            }
        }
        waited.sort((u, v) => v.overlap - u.overlap || u.a - v.a);
        const worked = row.segments.filter((s) => s.kind !== "carried");
        const before = worked.filter((s) => s.b <= p.a).sort((u, v) => v.b - u.b)[0] || null;
        const after = worked.filter((s) => s.a >= p.b).sort((u, v) => u.a - v.a)[0] || null;
        this.state.drawer = { kind: "idle", row, period: p, waited, before, after };
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
    openStoryItem(st) {
        if (st.idle) {
            this.openIdle(this.state.drawer.row, st.idle);
        } else if (st.wo) {
            this.openJob(st.wo);
        }
    }

    // ------------------------------------------------------------ export
    exportCsv() {
        const d = this.state.data;
        const head = ["Technician", "Stations", "Shift", "Checked in", "Checked out", "Late (min)", "Busy (min)", "Present (min)", "Utilisation %",
            "Handed on", "Picked up", "Idle (min)", "Idle while work waited (min)", "Idle, nothing waiting (min)", "Lunch (min)", "Longest idle (min)", "Earned (min)"];
        for (const m of d.slots) {
            head.push(`${this.hm(m)} busy`, `${this.hm(m)} handed`);
        }
        const lines = [head];
        for (const r of this.rows) {
            const line = [r.name, this.stationNames(r.stations), this.shiftText(r.shift), this.hm(r.first_present), this.hm(r.last_present), r.late,
                r.busy, r.present, r.utilisation ?? "", r.handed, r.accepted, r.idle, r.idle_wait, r.idle_free, r.break_min,
                Math.max(0, ...r.idle_periods.map((p) => p.b - p.a)), r.earned];
            for (const c of r.cells) {
                line.push(c.busy, c.handed);
            }
            lines.push(line);
        }
        for (const a of d.absent || []) {
            lines.push([a.name, this.stationNames(a.stations), this.shiftText(a.shift), "not in"]);
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
