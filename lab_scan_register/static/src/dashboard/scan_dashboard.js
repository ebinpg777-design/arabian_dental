/** @odoo-module **/
/**
 * The Scan Register dashboard: what the doctors sent, where it stands now, how
 * long each step takes, the printing room - plates, machines, the queue and when
 * it will be printed - and what needs somebody. Every figure comes from one server
 * call (lab.scan.get_dashboard) together with the domain behind it, so a click
 * opens exactly the records that make the number - no second calculation living
 * here to drift from the real one.
 *
 * Today redraws itself every minute, so the screen can stay up in the printing room.
 */
import { Component, onMounted, onWillStart, onWillUnmount, proxy, useProps } from "@odoo/owl";
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
const REFRESH_MS = 60000;
const ORDER = "lab.scan.print.order";

export class ScanDashboard extends Component {
    static template = "lab_scan_register.ScanDashboard";
    props = useProps();

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.periods = PERIODS;
        this.sourceIcon = SOURCE_ICON;
        this.state = proxy({ period: "month", data: null, loading: true, showClear: false, updated: null });
        this.loadSeq = 0;
        onWillStart(() => this.load());
        onMounted(() => {
            this.timer = setInterval(() => {
                if (this.state.period === "today" && !document.hidden && !this.state.loading) {
                    this.load(true);
                }
            }, REFRESH_MS);
        });
        onWillUnmount(() => clearInterval(this.timer));
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
        } catch (error) {
            if (!quiet) {
                throw error;
            }
            // a failed background refresh waits for the next minute, not a dialog a minute
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
            this.load();
        }
    }

    // ------------------------------------------------------------------ opening
    open(domain, name, model = "lab.scan") {
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
    get trendMax() {
        return Math.max(...(this.state.data?.trend || []).map((p) => p.total), 0);
    }
    get sourceTotal() {
        return (this.state.data?.sources || []).reduce((sum, s) => sum + s.count, 0);
    }
    flowCount(key) {
        const step = (this.state.data?.plate_flow || []).find((f) => f.key === key);
        return step ? step.count : 0;
    }
    get alerts() {
        return (this.state.data?.attention || []).filter((a) => a.count);
    }
    get clearChecks() {
        return (this.state.data?.attention || []).filter((a) => !a.count);
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
