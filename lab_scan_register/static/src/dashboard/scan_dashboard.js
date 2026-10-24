/** @odoo-module **/
/**
 * The Scan Register dashboard: the scan manager's morning screen (client, 2026-10-23:
 * "make it more attractive and useful; scroll is not working; visible to the scan
 * manager and the administrator").
 *
 * What needs somebody first, then the printing room this period - plates, models
 * printed and lost (a model is an arch), the re-plate backlog, what the failures cost,
 * ready on time - then the room now: the plates from Plating to Printed, each machine,
 * the timeline, the backlog to put on a plate and the queue; then where the failures
 * cluster, and the scans themselves. Every figure comes from one server call
 * (lab.scan.get_dashboard) together with the domain behind it, so a click opens
 * exactly the records that make the number - no second calculation living here.
 *
 * The board scrolls in its own body, under a pinned head: core locks the action
 * itself (overflow hidden, scrollbar hidden on a desktop), so the body is the one
 * scroller, full width - the gutters scroll and the bar shows. It redraws itself on
 * every period (every minute on Today, every three otherwise) while the tab is in
 * front, and after any dialog it opened.
 */
import { Component, onMounted, onWillStart, onWillUnmount, proxy, signal, useProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const PERIODS = [
    ["today", "Today"],
    ["week", "This week"],
    ["month", "This month"],
    ["last_month", "Last month"],
    ["quarter", "Last 90 days"],
];
const SOURCE_ICON = { whatsapp: "fa-whatsapp", email: "fa-envelope-o", transfer: "fa-cloud-download" };
const ARCH_CHIP = { both: "U+L", upper: "U", lower: "L" };
const TONES = ["danger", "warning", "info"];
const REFRESH_MS = 60000;
const PERIOD_KEY = "lab_scan_dashboard.period";
const ORDER = "lab.scan.print.order";

function storedPeriod() {
    try {
        const saved = window.localStorage.getItem(PERIOD_KEY);
        return PERIODS.some((p) => p[0] === saved) ? saved : "month";
    } catch {
        return "month";
    }
}

export class ScanDashboard extends Component {
    static template = "lab_scan_register.ScanDashboard";
    props = useProps();

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.periods = PERIODS;
        this.sourceIcon = SOURCE_ICON;
        this.scrollRef = signal.ref();
        this.state = proxy({
            period: storedPeriod(), data: null, loading: true, showClear: false, updated: null,
            picked: {},
        });
        this.loadSeq = 0;
        this.onVisible = () => {
            if (!document.hidden && !this.state.loading) {
                this.load(true);
            }
        };
        onWillStart(() => this.load());
        onMounted(() => {
            // the keys page the board at once: Page Down, Space, the arrows
            this.scrollRef()?.focus({ preventScroll: true });
            this.schedule();
            document.addEventListener("visibilitychange", this.onVisible);
        });
        onWillUnmount(() => {
            clearTimeout(this.timer);
            document.removeEventListener("visibilitychange", this.onVisible);
        });
    }

    /** The next quiet redraw, at the pace the server sets for the period. */
    schedule() {
        clearTimeout(this.timer);
        const seconds = this.state.data?.refresh_seconds;
        this.timer = setTimeout(() => {
            if (!document.hidden && !this.state.loading) {
                this.load(true);
            }
            this.schedule();
        }, seconds ? seconds * 1000 : REFRESH_MS);
    }

    /** Only the newest answer is drawn: a refresh coming back after the period
     * changed would otherwise put the old figures back. */
    async load(quiet = false) {
        const ticket = ++this.loadSeq;
        if (!quiet) {
            this.state.loading = true;
        }
        try {
            const data = await this.orm.call("lab.scan", "get_dashboard", [this.state.period]);
            if (ticket !== this.loadSeq) {
                return;
            }
            this.state.data = data;
            this.state.updated = luxon.DateTime.local().toFormat("HH:mm");
            // a ticked row that left the backlog is no longer ticked
            const rows = new Set(((data.replate_backlog || {}).rows || []).map((r) => r.id));
            for (const id of Object.keys(this.state.picked)) {
                if (!rows.has(Number(id))) {
                    delete this.state.picked[id];
                }
            }
        } catch (error) {
            if (!quiet) {
                throw error;
            }
            // a failed background refresh waits for the next one, not a dialog
            console.warn("Scan dashboard: refresh failed", error);
        } finally {
            if (ticket === this.loadSeq) {
                this.state.loading = false;
            }
        }
    }

    setPeriod(period) {
        if (period !== this.state.period) {
            this.state.period = period;
            try {
                window.localStorage.setItem(PERIOD_KEY, period);
            } catch {
                // a private window: the period is simply not remembered
            }
            this.load().then(() => this.schedule());
        }
    }

    // ------------------------------------------------------------------ opening
    open(domain, name, model = "lab.scan") {
        if (!domain) {
            return;
        }
        // views spelled out: a client-built action is not expanded from view_mode
        this.action.doAction({
            type: "ir.actions.act_window",
            name,
            res_model: model,
            domain,
            views: [[false, "list"], [false, "form"]],
            context: { create: false },
        });
    }
    openRecord(model, id) {
        if (!id) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: model,
            res_id: id,
            views: [[false, "form"]],
        });
    }
    openPlate(id) {
        this.openRecord(ORDER, id);
    }
    openFull() {
        this.action.doAction("lab_scan_register.action_scan_analysis");
    }
    openFailedModels() {
        this.action.doAction("lab_scan_register.action_scan_print_order_line_failed");
    }
    /** A dialog the server built (call_kw returns it unexpanded): its views added,
     * and the board redrawn when it closes. */
    async runDialog(action) {
        if (!action) {
            return;
        }
        await this.action.doAction(
            { ...action, views: action.views || [[false, "form"]] },
            { onClose: () => this.load(true) }
        );
    }
    /** The New Print Order dialog on these failed models, waiting for a plate. */
    async putOnPlate(ids) {
        if (!ids.length) {
            return;
        }
        const action = await this.orm.call("lab.scan.line", "action_put_on_plate", [ids]);
        await this.runDialog(action);
    }
    /** Some models came off a running plate: the board, in A Model Failed. */
    async modelFailed(plateId) {
        const action = await this.orm.call(ORDER, "action_model_failed", [[plateId]]);
        await this.runDialog(action);
    }

    // ------------------------------------------------------------------ the backlog
    togglePick(id) {
        if (this.state.picked[id]) {
            delete this.state.picked[id];
        } else {
            this.state.picked[id] = true;
        }
    }
    get pickedIds() {
        return Object.keys(this.state.picked).map(Number);
    }
    get allPicked() {
        const rows = this.backlog.rows || [];
        return rows.length > 0 && rows.every((r) => this.state.picked[r.id]);
    }
    toggleAll() {
        const rows = this.backlog.rows || [];
        const all = this.allPicked;
        for (const row of rows) {
            if (all) {
                delete this.state.picked[row.id];
            } else {
                this.state.picked[row.id] = true;
            }
        }
    }
    get backlog() {
        return this.state.data?.replate_backlog || { rows: [], buckets: [] };
    }
    archChip(key) {
        return ARCH_CHIP[key] || "";
    }

    // ------------------------------------------------------------------ figures
    delta(current, previous) {
        if (!previous) {
            return null;
        }
        return Math.round(((current - previous) * 100) / previous);
    }
    /** Percentage points, for a rate compared with a rate. */
    points(current, previous) {
        if (current === null || previous === null || current === undefined || previous === undefined) {
            return null;
        }
        return current - previous;
    }
    duration(hours) {
        if (hours === null || hours === undefined) {
            return "—";
        }
        return hours < 48 ? `${hours.toFixed(1)} h` : `${(hours / 24).toFixed(1)} d`;
    }
    /** 2h 05m, from hours. */
    hm(hours) {
        if (hours === null || hours === undefined) {
            return "—";
        }
        const minutes = Math.max(0, Math.round(hours * 60));
        const h = Math.floor(minutes / 60);
        const m = minutes % 60;
        return h ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m`;
    }
    share(value, values) {
        // a nought is an empty bar, not a dot; anything else is at least visible
        const max = Math.max(...values, 0);
        return max && value ? Math.max(3, Math.round((value * 100) / max)) : 0;
    }
    pct(part, whole) {
        return whole ? Math.round((part * 100) / whole) : 0;
    }

    // ------------------------------------------------------------------ time
    when(utcString) {
        return utcString ? luxon.DateTime.fromSQL(utcString, { zone: "utc" }).toLocal() : null;
    }
    clock(utcString) {
        // "06:40" on the browser's clock - the lab's - for a UTC datetime string
        const when = this.when(utcString);
        return when && when.isValid ? when.toFormat("HH:mm") : "";
    }
    /** 14:30 today, "Thu 09:10" this week, "3 Oct" before. */
    stamp(utcString) {
        const when = this.when(utcString);
        if (!when || !when.isValid) {
            return "—";
        }
        const now = luxon.DateTime.local();
        if (when.hasSame(now, "day")) {
            return when.toFormat("HH:mm");
        }
        if (Math.abs(now.diff(when, "days").days) < 6) {
            return when.toFormat("ccc HH:mm");
        }
        return when.toFormat("d LLL");
    }
    /** How long since, as 3h 20m. */
    since(utcString) {
        const when = this.when(utcString);
        return when && when.isValid ? this.hm(luxon.DateTime.local().diff(when, "hours").hours) : "";
    }
    day(isoDate) {
        const when = luxon.DateTime.fromISO(isoDate);
        return when.isValid ? when.toFormat("ccc d LLL") : isoDate;
    }
    get rangeText() {
        const d = this.state.data;
        if (!d) {
            return "";
        }
        if (d.is_today) {
            return luxon.DateTime.fromISO(d.start).toFormat("cccc d LLLL");
        }
        return `${this.day(d.start)} → ${this.day(d.end)}`;
    }

    // ------------------------------------------------------------------ panels
    get att() {
        return Object.fromEntries((this.state.data?.attention || []).map((a) => [a.key, a]));
    }
    get alerts() {
        const rows = (this.state.data?.attention || []).filter((a) => a.count);
        return rows.sort((a, b) => TONES.indexOf(a.tone) - TONES.indexOf(b.tone));
    }
    get clearChecks() {
        return (this.state.data?.attention || []).filter((a) => !a.count);
    }
    get trendMax() {
        return Math.max(...(this.state.data?.trend || []).map((p) => p.total), 0);
    }
    get sourceTotal() {
        return (this.state.data?.sources || []).reduce((sum, s) => sum + s.count, 0);
    }
    flowStep(key) {
        return (this.state.data?.plate_flow || []).find((f) => f.key === key) || {};
    }
    flowCount(key) {
        return this.flowStep(key).count || 0;
    }
    get quality() {
        return this.state.data?.quality || {};
    }
    get printTrend() {
        return this.state.data?.print_trend || [];
    }
    get printTrendMax() {
        return Math.max(...this.printTrend.map((p) => p.ok + p.failed), 0);
    }
    /** The failure rate as a line over the columns: an SVG polyline, 0-100 both ways. */
    get rateLine() {
        const points = this.printTrend;
        const n = points.length;
        if (!n) {
            return "";
        }
        return points
            .map((p, i) => (p.rate === null || p.rate === undefined ? null : `${((i + 0.5) * 100) / n},${100 - Math.min(100, p.rate)}`))
            .filter(Boolean)
            .join(" ");
    }
    get matrixMax() {
        const rows = this.quality.matrix?.rows || [];
        return Math.max(0, ...rows.flatMap((r) => r.cells.map((c) => c.count)));
    }
    heatShare(count) {
        const max = this.matrixMax;
        return max && count ? Math.max(12, Math.round((count * 100) / max)) : 0;
    }
    ring(pct) {
        const r = 15.5;
        const c = 2 * Math.PI * r;
        const p = Math.max(0, Math.min(100, pct || 0));
        return { r, c, off: c * (1 - p / 100) };
    }
    machineState(pr) {
        if (pr.state === "maintenance") {
            return ["maintenance", "Maintenance"];
        }
        if (pr.state === "retired") {
            return ["retired", "Retired"];
        }
        return pr.running_order ? ["busy", "Printing"] : ["free", "Free"];
    }

    // ------------------------------------------------------------------ timeline
    get axis() {
        const t = this.state.data.timeline;
        const hours = [];
        for (let m = t.lo; m <= t.hi; m += 60) {
            hours.push(m);
        }
        return { lo: t.lo, hi: t.hi, span: Math.max(t.hi - t.lo, 60), hours };
    }
    x(m) {
        const a = this.axis;
        return ((Math.max(a.lo, Math.min(a.hi, m)) - a.lo) / a.span) * 100;
    }
    w(a, b) {
        return Math.max(0.4, this.x(b) - this.x(a));
    }
    hhmm(minutes) {
        const m = Math.max(0, Math.round(minutes));
        return `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
    }
    segTitle(seg) {
        const kinds = {
            done: "printed",
            failed: "failed",
            cancel: "cancelled",
            printing: "printing now",
            projected: "still to print (the file's estimate)",
            planned: "planned on this machine",
            suggested: "plated and ready - would go here",
            patients: "patients waiting, as a plate of the usual size",
        };
        const end = seg.tomorrow ? "tomorrow" : this.hhmm(seg.b);
        const lines = [`${seg.name} · ${kinds[seg.kind] || seg.kind}`, `${this.hhmm(seg.a)} – ${end}`];
        if (seg.models) {
            lines.push(`${seg.models} models` + (seg.patients ? ` · ${seg.patients} patients` : ""));
        }
        if (seg.failed) {
            lines.push(`${seg.failed} failed`);
        }
        return lines.join("\n");
    }
    heat(cell) {
        // a day's printing hours against the 24 a machine could print
        return Math.min(100, Math.round((cell.hours * 100) / 24));
    }
}

registry.category("actions").add("lab_scan_dashboard", ScanDashboard);
