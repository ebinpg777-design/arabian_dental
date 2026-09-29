/** @odoo-module **/

import { Component, onWillStart, onMounted, onWillUnmount, useEffect, useState, useRef } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { DateTimeInput } from "@web/core/datetime/datetime_input";
import { deserializeDate, serializeDate } from "@web/core/l10n/dates";

/**
 * The report viewer: one screen for every dynamic financial report.
 *
 * The server owns the numbers and their text; this component owns what the
 * reader is looking at - the filters, which lines are open, which figure is
 * being explained, the notes, the trends. Every change of filter is one call
 * that returns the whole report again; opening a line is one call that
 * returns its children. Nothing is computed here that the PDF or the workbook
 * could compute differently.
 */
// lines drawn at once: a statement of every customer is thousands of rows, and drawing
// them all held the screen for seconds
const PAGE_OF_LINES = 300;

export class FinReportViewer extends Component {
    static template = "ebshel_account_reports.Viewer";
    static components = { DateTimeInput };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.tableRef = useRef("table");
        const params = (this.props.action && this.props.action.params) || {};
        this.reportKey = params.report_key;
        this.state = useState({
            loading: true,
            error: null,
            report: null,
            options: params.options || {},
            columns: [],
            lines: [],
            choices: { journals: [], analytic: [], partners: [], presets: [], comparison_modes: [], account_types: [] },
            annotations: {},
            savedViews: [],
            currency: null,
            canDesign: false,
            canAnnotate: false,
            menu: null,                 // which toolbar popover is open
            search: "",
            trends: {},
            explain: null,              // {title, accounts, partners, months}
            modal: null,                // {kind, ...}
            partnerQuery: "",
            partnerHits: [],
            busy: false,
            focus: null,                // keyboard cursor: line id
            unitLabel: "",
            periodText: "",
            reports: [],
            items: {},                  // line id -> the journal items opened under it
            movers: null,               // the "what changed" panel
            chart: false,
            selected: {},               // "line|col" -> value, for the totals bar
            help: false,
            full: false,
            display: this.loadDisplay(),
            draft: {},                  // the More filters panel, before Apply
            exporter: null,             // the export panel: {format, scope, orientation, filters, notes, preview}
            refreshing: false,          // a reload under a report already on the screen
            limit: PAGE_OF_LINES,       // how many lines are drawn; the rest come as the reader scrolls
        });
        this.loadToken = 0;
        this.countToken = 0;
        this.onDocClick = (ev) => {
            if (this.state.menu && !ev.target.closest(".o_efr_pop, .o_efr_popbtn")) {
                this.state.menu = null;
            }
        };
        this.onKey = (ev) => this.keyboard(ev);
        // once a popover is drawn, it is pulled back inside the window
        useEffect((menu) => {
            if (menu) {
                this.fitPopover();
            }
        }, () => [this.state.menu]);
        onWillStart(() => this.load());
        onMounted(() => {
            document.addEventListener("click", this.onDocClick);
            document.addEventListener("keydown", this.onKey);
        });
        onWillUnmount(() => {
            document.removeEventListener("click", this.onDocClick);
            document.removeEventListener("keydown", this.onKey);
        });
    }

    // ------------------------------------------------------------------ loading
    async load() {
        const token = ++this.loadToken;
        if (this.state.report && this.state.lines.length) {
            this.state.refreshing = true;
        } else {
            this.state.loading = true;
        }
        this.state.error = null;
        try {
            const data = this.state.report
                ? await this.orm.call("ebshel.fin.report", "get_report_data", [[this.state.report.id]], { options: this.state.options })
                : await this.orm.call("ebshel.fin.report", "open_by_key", [this.reportKey], { options: this.state.options });
            if (token !== this.loadToken) {
                return;
            }
            this.state.report = data.report;
            this.state.options = data.options;
            this.state.columns = data.columns;
            this.state.lines = data.lines;
            this.state.choices = data.choices;
            this.state.annotations = data.annotations || {};
            this.state.savedViews = data.saved_views || [];
            this.state.currency = data.currency;
            this.state.canDesign = data.can_design;
            this.state.canAnnotate = data.can_annotate;
            this.state.trends = {};
            this.state.explain = null;
            this.state.unitLabel = data.unit_label || "";
            this.state.periodText = data.period_label || "";
            this.state.reports = data.reports || [];
            this.state.items = {};
            this.state.selected = {};
            this.state.limit = PAGE_OF_LINES;
            if (this.state.movers) {
                this.loadMovers();
            }
            if (this.state.options.trend) {
                this.loadTrends();
            }
        } catch (error) {
            this.state.error = (error && error.data && error.data.message) || String(error);
        } finally {
            if (token === this.loadToken) {
                this.state.loading = false;
                this.state.refreshing = false;
            }
        }
    }

    reload(patch) {
        this.state.options = { ...this.state.options, ...patch };
        this.state.menu = null;
        return this.load();
    }

    get rid() {
        return this.state.report ? [this.state.report.id] : null;
    }

    get amountColumns() {
        return this.state.columns.filter((c) => c.type !== "growth");
    }

    get isSingle() {
        return this.state.report && this.state.report.date_mode === "single";
    }

    get dateFormat() {
        return "dd/MM/yyyy";
    }

    deserialize(iso) {
        return deserializeDate(iso);
    }

    // ------------------------------------------------------------------ filters
    dateValue(key) {
        const iso = this.state.options.date && this.state.options.date[key];
        return iso ? deserializeDate(iso) : false;
    }

    onDate(key, value) {
        if (!value || !value.isValid) {
            return;
        }
        const date = { ...this.state.options.date, [key]: serializeDate(value), preset: "custom" };
        this.reload({ date });
    }

    setPreset(preset) {
        this.reload({ date: { ...this.state.options.date, preset } });
    }

    shift(steps) {
        const d = this.state.options.date;
        const from = deserializeDate(d.from), to = deserializeDate(d.to);
        const whole = from.day === 1 && to.hasSame(to.endOf("month"), "day");
        let nf, nt;
        if (whole) {
            const months = (to.year - from.year) * 12 + to.month - from.month + 1;
            nf = from.plus({ months: months * steps });
            nt = nf.plus({ months }).minus({ days: 1 });
        } else {
            const span = to.diff(from, "days").days + 1;
            nf = from.plus({ days: span * steps });
            nt = to.plus({ days: span * steps });
        }
        this.reload({ date: { preset: "custom", from: serializeDate(nf), to: serializeDate(nt) } });
    }

    setComparison(mode) {
        this.reload({ comparison: { ...this.state.options.comparison, mode } });
    }

    setPeriods(n) {
        const periods = Math.max(1, Math.min(12, parseInt(n) || 1));
        this.reload({ comparison: { ...this.state.options.comparison, periods } });
    }

    toggleIn(key, id) {
        const current = this.state.options[key] || [];
        const next = current.includes(id) ? current.filter((i) => i !== id) : [...current, id];
        this.state.options = { ...this.state.options, [key]: next };
    }

    applyMenu() {
        this.load();
        this.state.menu = null;
    }

    clearKey(key) {
        this.reload({ [key]: [] });
    }

    toggleFlag(key) {
        this.reload({ [key]: !this.state.options[key] });
    }

    setExtra(key, value) {
        this.reload({ [key]: value });
    }

    toggleExtraMulti(key, value) {
        const current = this.state.options[key] || [];
        const next = current.includes(value) ? current.filter((v) => v !== value) : [...current, value];
        this.reload({ [key]: next });
    }

    onAccountsQuery(ev) {
        if (ev.key === "Enter") {
            this.reload({ accounts_query: ev.target.value });
        }
    }

    async searchPartners(ev) {
        this.state.partnerQuery = ev.target.value;
        if (this.state.partnerQuery.length < 2) {
            this.state.partnerHits = [];
            return;
        }
        const hits = await this.orm.call("res.partner", "name_search", [this.state.partnerQuery], { limit: 8 });
        this.state.partnerHits = hits.map(([id, name]) => ({ id, name }));
    }

    addPartner(hit) {
        if (!(this.state.options.partners || []).includes(hit.id)) {
            this.state.choices.partners = [...this.state.choices.partners, hit];
            this.reload({ partners: [...(this.state.options.partners || []), hit.id] });
        }
        this.state.partnerQuery = "";
        this.state.partnerHits = [];
    }

    removePartner(id) {
        this.reload({ partners: (this.state.options.partners || []).filter((i) => i !== id) });
    }

    partnerName(id) {
        const p = this.state.choices.partners.find((x) => x.id === id);
        return p ? p.name : "#" + id;
    }

    openMenu(name) {
        this.state.menu = this.state.menu === name ? null : name;
    }

    /** A popover hangs from its button; near the right edge it is pulled back into the window. */
    fitPopover() {
        const el = document.querySelector(".o_efr .o_efr_pop");
        if (!el || window.innerWidth < 768) {
            return;                     // on a phone a popover is a sheet, laid out by the stylesheet
        }
        el.style.transform = "";
        const box = el.getBoundingClientRect();
        const over = box.right - (window.innerWidth - 8);
        if (over > 0) {
            el.style.transform = `translateX(${-Math.min(over, Math.max(0, box.left - 8))}px)`;
        } else if (box.left < 8) {
            el.style.transform = `translateX(${8 - box.left}px)`;
        }
    }

    get periodLabel() {
        return this.state.periodText || (this.state.columns.length ? this.state.columns[0].label : "");
    }

    presetLabel(key) {
        const found = (this.state.choices.presets || []).find((p) => p[0] === key);
        return found ? found[1] : key;
    }

    comparisonLabel() {
        const mode = this.state.options.comparison && this.state.options.comparison.mode;
        const found = (this.state.choices.comparison_modes || []).find((m) => m[0] === mode);
        return found ? found[1] : _t("Compare");
    }

    // ------------------------------------------------------------------ lines
    /** The lines on the screen now: the first page of what the search leaves, grown by scrolling. */
    get shownLines() {
        const all = this.visibleLines;
        return all.length > this.state.limit ? all.slice(0, this.state.limit) : all;
    }

    /** Nothing but a total of nothing: the report has nothing to say. */
    get isEmpty() {
        return !this.visibleLines.some((l) => l.kind !== "total");
    }

    get linesLeft() {
        return Math.max(0, this.visibleLines.length - this.state.limit);
    }

    showMoreLines() {
        this.state.limit += PAGE_OF_LINES;
    }

    onTableScroll(ev) {
        const el = ev.target;
        if (this.linesLeft && el.scrollTop + el.clientHeight > el.scrollHeight - 600) {
            this.showMoreLines();
        }
    }

    cellClass(line, cell, index) {
        const cls = ["o_efr_cell", "o_efr_c_" + cell.display];
        if (cell.class) cls.push("o_efr_" + cell.class);
        if (cell.drill) cls.push("o_efr_drill");
        if (cell.value < 0) cls.push("o_efr_neg");
        if (cell.value === 0 && cell.display === "amount" && !line.bold) cls.push("o_efr_zero");
        if (this.isSelected(line, index)) cls.push("o_efr_sel");
        return cls.join(" ");
    }

    get visibleLines() {
        const q = this.state.search.trim().toLowerCase();
        if (!q) {
            return this.state.lines;
        }
        const byId = new Map(this.state.lines.map((l) => [l.id, l]));
        const keep = new Set();
        for (const line of this.state.lines) {
            if (line.name.toLowerCase().includes(q)) {
                let cur = line;
                while (cur) {
                    keep.add(cur.id);
                    cur = cur.parent_id ? byId.get(cur.parent_id) : null;
                }
            }
        }
        for (const line of this.state.lines) {
            if (line.parent_id && keep.has(line.parent_id) && byId.get(line.parent_id).name.toLowerCase().includes(q)) {
                keep.add(line.id);
            }
        }
        return this.state.lines.filter((l) => keep.has(l.id));
    }

    async toggle(line) {
        if (!line.unfoldable) {
            return;
        }
        if (line.unfolded) {
            const ids = new Set([line.id]);
            const rest = [];
            for (const l of this.state.lines) {
                if (l.parent_id && ids.has(l.parent_id)) {
                    ids.add(l.id);
                } else {
                    rest.push(l);
                }
            }
            this.state.lines = rest;
            line.unfolded = false;
            this.state.options.expanded = (this.state.options.expanded || []).filter((i) => i !== line.id);
            return;
        }
        const res = await this.orm.call("ebshel.fin.report", "expand_line", [this.rid], {
            options: this.state.options, line_id: line.id, offset: 0,
        });
        const at = this.state.lines.indexOf(line);
        const extra = res.has_more
            ? [...res.lines, { id: line.id + ":more", parent_id: line.id, name: _t("Load more…"), level: line.level + 1,
                              kind: "more", columns: [], unfoldable: false, offset: res.lines.length - 1 }]
            : res.lines;
        this.state.lines.splice(at + 1, 0, ...extra);
        line.unfolded = true;
        this.state.options.expanded = [...(this.state.options.expanded || []), line.id];
        if (this.state.options.trend) {
            this.loadTrends(res.lines.map((l) => l.id));
        }
    }

    async loadMore(more) {
        const parent = this.state.lines.find((l) => l.id === more.parent_id);
        const res = await this.orm.call("ebshel.fin.report", "expand_line", [this.rid], {
            options: this.state.options, line_id: more.parent_id, offset: more.offset,
        });
        const at = this.state.lines.indexOf(more);
        const rows = res.lines.filter((l) => l.kind !== "initial");
        this.state.lines.splice(at, 1, ...rows);
        if (res.has_more) {
            this.state.lines.splice(at + rows.length, 0, { ...more, offset: more.offset + rows.length });
        }
        if (parent && this.state.options.trend) {
            this.loadTrends(rows.map((l) => l.id));
        }
    }

    unfoldAll() {
        this.reload({ unfold_all: !this.state.options.unfold_all, expanded: [] });
    }

    /**
     * A click on a figure opens the journal items behind it, in place.
     * Shift+click adds the figure to the totals bar; Ctrl/Cmd+click opens the list view.
     */
    async drill(line, col, ev) {
        ev.stopPropagation();
        const index = line.columns.indexOf(col);
        if (ev.shiftKey) {
            this.select(line, col, index);
            return;
        }
        if (!col.drill) {
            return;
        }
        const colKey = this.state.columns[index].key;
        if (ev.ctrlKey || ev.metaKey) {
            return this.openList(line, colKey);
        }
        return this.openItems(line, colKey);
    }

    async openList(line, colKey) {
        const action = await this.orm.call("ebshel.fin.report", "get_drill_action", [this.rid], {
            options: this.state.options, line_id: line.id, column_key: colKey,
        });
        if (action) {
            this.action.doAction(action);
        }
    }

    // ------------------------------------------------------------------ journal items, in place
    canOpenItems(line) {
        return !["more", "move_line", "open_item", "initial", "header"].includes(line.kind)
            && (line.columns || []).some((c) => c.drill);
    }

    firstDrillKey(line) {
        const index = (line.columns || []).findIndex((c) => c.drill);
        return index >= 0 ? this.state.columns[index].key : null;
    }

    itemColumns(line) {
        return this.state.columns.filter((c, i) => line.columns[i] && line.columns[i].drill);
    }

    async toggleItems(line, ev) {
        if (ev) {
            ev.stopPropagation();
        }
        const open = this.state.items[line.id];
        if (open) {
            delete this.state.items[line.id];
            return;
        }
        const key = this.firstDrillKey(line);
        if (key) {
            await this.openItems(line, key);
        }
    }

    async openItems(line, colKey) {
        const current = this.state.items[line.id];
        if (current && current.colKey === colKey) {
            delete this.state.items[line.id];
            return;
        }
        this.state.items[line.id] = { colKey, rows: [], total: 0, loading: true, search: "", order: "date desc",
                                      sums: {}, title: line.name, has_more: false };
        await this.fetchItems(line, false);
    }

    async fetchItems(line, more) {
        const it = this.state.items[line.id];
        if (!it) {
            return;
        }
        it.loading = true;
        try {
            const res = await this.orm.call("ebshel.fin.report", "get_items", [this.rid], {
                options: this.state.options, line_id: line.id, column_key: it.colKey,
                offset: more ? it.rows.length : 0, limit: 40, search: it.search, order: it.order,
            });
            const live = this.state.items[line.id];
            if (!live) {
                return;
            }
            if (res.other_model) {
                delete this.state.items[line.id];
                return this.openList(line, it.colKey);
            }
            live.rows = more ? [...live.rows, ...res.rows] : res.rows;
            live.total = res.total;
            live.sums = res.sums;
            live.has_more = res.has_more;
            live.title = res.title || line.name;
        } finally {
            if (this.state.items[line.id]) {
                this.state.items[line.id].loading = false;
            }
        }
    }

    onItemsSearch(line, ev) {
        if (ev.key === "Enter") {
            this.state.items[line.id].search = ev.target.value;
            this.fetchItems(line, false);
        }
    }

    setItemsOrder(line, ev) {
        this.state.items[line.id].order = ev.target.value;
        this.fetchItems(line, false);
    }

    setItemsColumn(line, colKey) {
        const it = this.state.items[line.id];
        if (it.colKey !== colKey) {
            it.colKey = colKey;
            this.fetchItems(line, false);
        }
    }

    openEntry(row, ev) {
        if (ev) {
            ev.stopPropagation();
        }
        this.action.doAction({ type: "ir.actions.act_window", res_model: "account.move", res_id: row.move_id,
                               views: [[false, "form"]], target: "current" });
    }

    closeItems(line) {
        delete this.state.items[line.id];
    }

    openRecord(line, ev) {
        ev.stopPropagation();
        if (!line.model || !line.res_id) {
            return;
        }
        this.action.doAction({ type: "ir.actions.act_window", res_model: line.model, res_id: line.res_id,
                               views: [[false, "form"]], target: "current" });
    }

    async explain(line, col, ev) {
        ev.stopPropagation();
        const colKey = this.state.columns[line.columns.indexOf(col)].key;
        this.state.movers = null;
        this.state.explain = { loading: true, title: line.name };
        const data = await this.orm.call("ebshel.fin.report", "explain_cell", [this.rid], {
            options: this.state.options, line_id: line.id, column_key: colKey,
        });
        if (!data || !Object.keys(data).length) {
            this.state.explain = null;
            this.notification.add(_t("Nothing to break down for this line."), { type: "info" });
            return;
        }
        this.state.explain = { loading: false, ...data };
    }

    barWidth(item, items) {
        const top = Math.max(...items.map((i) => Math.abs(i.value)), 1);
        return Math.round(Math.abs(item.value) / top * 100);
    }

    // ------------------------------------------------------------------ trends
    async loadTrends(ids) {
        const want = (ids || this.state.lines.map((l) => l.id)).filter(
            (id) => ["sum", "account", "partner", "line"].includes((this.state.lines.find((l) => l.id === id) || {}).kind)
                || id.startsWith("ln:") || id.startsWith("ac:"));
        if (!want.length) {
            return;
        }
        const trends = await this.orm.call("ebshel.fin.report", "get_trends", [this.rid], {
            options: this.state.options, line_ids: want.slice(0, 200),
        });
        this.state.trends = { ...this.state.trends, ...trends };
    }

    spark(points) {
        // An SVG path of bars, 12 months: up for positive, down for negative.
        const w = 4, gap = 2, h = 18, mid = 9;
        const top = Math.max(...points.map((p) => Math.abs(p.value)), 1);
        return points.map((p, i) => {
            const bh = Math.max(1, Math.round(Math.abs(p.value) / top * mid));
            const y = p.value >= 0 ? mid - bh : mid;
            return { x: i * (w + gap), y, h: bh, w, neg: p.value < 0, title: `${p.label}: ${p.value}` };
        });
    }

    // ------------------------------------------------------------------ notes
    notesFor(line) {
        return this.state.annotations[line.id] || [];
    }

    openNote(line, ev) {
        ev.stopPropagation();
        this.state.modal = { kind: "note", line, text: "" };
    }

    async saveNote() {
        const m = this.state.modal;
        if (!m.text.trim()) {
            return;
        }
        await this.orm.call("ebshel.fin.report.annotation", "add_note", [this.rid[0], m.line.id, m.text.trim()], {
            line_name: m.line.name,
        });
        this.state.modal = null;
        await this.refreshNotes();
    }

    async refreshNotes() {
        const data = await this.orm.call("ebshel.fin.report", "get_report_data", [this.rid], { options: this.state.options });
        this.state.annotations = data.annotations || {};
        return this.state.annotations;
    }

    async deleteNote(note, ev) {
        ev.stopPropagation();
        await this.orm.unlink("ebshel.fin.report.annotation", [note.id]);
        await this.refreshNotes();
    }

    // ------------------------------------------------------------------ views & exports
    openSave() {
        this.state.modal = { kind: "save", name: "", shared: false, is_default: false };
    }

    async saveView() {
        const m = this.state.modal;
        if (!m.name.trim()) {
            return;
        }
        await this.orm.call("ebshel.fin.report.view", "save_view", [this.rid[0], m.name.trim(), this.state.options], {
            shared: m.shared, is_default: m.is_default,
        });
        this.state.modal = null;
        this.notification.add(_t("View saved."), { type: "success" });
        await this.load();
    }

    applyView(view) {
        this.state.menu = null;
        this.state.options = { ...this.state.options, ...view.options, expanded: [] };
        this.load();
    }

    async deleteView(view, ev) {
        ev.stopPropagation();
        await this.orm.unlink("ebshel.fin.report.view", [view.id]);
        this.state.savedViews = this.state.savedViews.filter((v) => v.id !== view.id);
    }

    // ------------------------------------------------------------------ out of the screen
    exportPrefs() {
        const base = { format: "xlsx", scope: "screen", orientation: "auto", filters: true, notes: true };
        try {
            const kept = JSON.parse(window.localStorage.getItem("ebshel_fin_export") || "{}");
            const prefs = { ...base, ...kept };
            if (!["xlsx", "pdf", "csv", "copy"].includes(prefs.format)) prefs.format = base.format;
            if (!["screen", "summary", "full"].includes(prefs.scope)) prefs.scope = base.scope;
            if (!["auto", "portrait", "landscape"].includes(prefs.orientation)) prefs.orientation = base.orientation;
            return prefs;
        } catch {
            return base;
        }
    }

    openExport(format) {
        this.state.menu = null;
        const prefs = this.exportPrefs();
        this.state.exporter = { ...prefs, format: format || prefs.format, preview: null, counting: false, busy: false };
        this.countExport();
    }

    closeExport() {
        if (this.state.exporter && !this.state.exporter.busy) {
            this.state.exporter = null;
        }
    }

    setExport(key, value) {
        const x = this.state.exporter;
        x[key] = value;
        try {
            window.localStorage.setItem("ebshel_fin_export", JSON.stringify(
                { format: x.format, scope: x.scope, orientation: x.orientation, filters: x.filters, notes: x.notes }));
        } catch {
            // private mode: the choice lasts as long as the panel
        }
        if (key === "scope" || key === "orientation") {
            this.countExport();
        }
    }

    get exportLayout() {
        const x = this.state.exporter;
        return { scope: x.scope, orientation: x.orientation, filters: x.filters, notes: x.notes };
    }

    async countExport() {
        const token = ++this.countToken;
        this.state.exporter.counting = true;
        try {
            const res = await this.orm.call("ebshel.fin.report", "get_export_preview", [this.rid],
                                            { options: this.state.options, layout: this.exportLayout });
            if (token === this.countToken && this.state.exporter) {
                this.state.exporter.preview = res;
            }
        } finally {
            if (token === this.countToken && this.state.exporter) {
                this.state.exporter.counting = false;
            }
        }
    }

    get exportFormats() {
        return [
            { key: "xlsx", tone: "green", icon: "fa-file-excel-o", name: _t("Excel"), text: _t("To work in, and to print") },
            { key: "pdf", tone: "red", icon: "fa-file-pdf-o", name: _t("PDF"), text: _t("To read and to send") },
            { key: "csv", tone: "blue", icon: "fa-file-text-o", name: _t("CSV"), text: _t("Plain rows, for another program") },
            { key: "copy", tone: "amber", icon: "fa-clipboard", name: _t("Copy"), text: _t("To paste into a sheet") },
        ];
    }

    get exportScopes() {
        return [
            { key: "screen", icon: "fa-desktop", name: _t("As on the screen"), text: _t("What you have unfolded") },
            { key: "summary", icon: "fa-compress", name: _t("Summary"), text: _t("Every line folded") },
            { key: "full", icon: "fa-expand", name: _t("Everything"), text: _t("Down to the entries") },
        ];
    }

    /** What the chosen export comes to, in words. */
    get exportSize() {
        const x = this.state.exporter, p = x.preview;
        if (!p) {
            return "";
        }
        const lines = p.lines.toLocaleString();
        if (x.format !== "pdf") {
            return p.lines === 1 ? _t("1 line") : _t("%s lines", lines);
        }
        const side = p.landscape ? _t("landscape") : _t("portrait");
        const shown = p.cut ? p.cap.toLocaleString() : lines;
        return p.pages === 1 ? _t("%(n)s lines · 1 page, %(side)s", { n: shown, side })
            : _t("%(n)s lines · about %(p)s pages, %(side)s", { n: shown, p: p.pages, side });
    }

    get exportTone() {
        return this.exportFormats.find((f) => f.key === this.state.exporter.format).tone;
    }

    get exportButton() {
        const names = { xlsx: _t("Download the workbook"), pdf: _t("Make the PDF"), csv: _t("Download the CSV"), copy: _t("Copy the rows") };
        return names[this.state.exporter.format];
    }

    saveFile(name, blob) {
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = name;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 2000);
    }

    saveBase64(file) {
        const bytes = Uint8Array.from(atob(file.content), (c) => c.charCodeAt(0));
        this.saveFile(file.filename, new Blob([bytes], { type: file.mimetype }));
    }

    async runExport() {
        const x = this.state.exporter;
        if (!x || x.busy) {
            return;
        }
        x.busy = true;
        const args = { options: this.state.options, layout: this.exportLayout };
        try {
            if (x.format === "xlsx") {
                this.saveBase64(await this.orm.call("ebshel.fin.report", "export_xlsx", [this.rid], args));
            } else if (x.format === "pdf") {
                this.saveBase64(await this.orm.call("ebshel.fin.report", "export_pdf", [this.rid], args));
            } else {
                const res = await this.orm.call("ebshel.fin.report", "export_rows", [this.rid], args);
                if (x.format === "csv") {
                    const csv = res.rows.map((r) => r.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(",")).join("\r\n");
                    this.saveFile(res.filename + ".csv", new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" }));
                } else {
                    const clean = (v) => String(v).replace(/[\t\n\r]+/g, " ");
                    const text = res.rows.map((r, i) => ["  ".repeat(res.levels[i] || 0) + clean(r[0]), ...r.slice(1).map(clean)].join("\t")).join("\n");
                    try {
                        await navigator.clipboard.writeText(text);
                        this.notification.add(_t("%s rows copied. Paste them into a sheet.", res.rows.length - 1), { type: "success" });
                    } catch {
                        this.notification.add(_t("The browser did not allow copying; take the CSV instead."), { type: "warning" });
                        return;
                    }
                }
            }
            this.state.exporter = null;
        } finally {
            if (this.state.exporter) {
                this.state.exporter.busy = false;
            }
        }
    }

    /** The journal items open under a line, as a sheet of their own. */
    async downloadItems(line) {
        const it = this.state.items[line.id];
        if (!it || it.saving) {
            return;
        }
        it.saving = true;
        try {
            const file = await this.orm.call("ebshel.fin.report", "export_items_xlsx", [this.rid], {
                options: this.state.options, line_id: line.id, column_key: it.colKey, search: it.search, order: it.order,
            });
            this.saveBase64(file);
            if (file.total > file.items) {
                this.notification.add(_t("The sheet holds the first %(n)s of %(t)s items.", { n: file.items, t: file.total }), { type: "warning" });
            }
        } finally {
            if (this.state.items[line.id]) {
                this.state.items[line.id].saving = false;
            }
        }
    }

    schedule() {
        this.action.doAction({
            type: "ir.actions.act_window", res_model: "ebshel.fin.report.schedule", views: [[false, "form"]],
            target: "new", context: { default_report_id: this.rid[0], default_name: this.state.report.name },
        });
    }

    design() {
        this.action.doAction({
            type: "ir.actions.act_window", res_model: "ebshel.fin.report", res_id: this.rid[0],
            views: [[false, "form"]], target: "current",
        });
    }

    async sendStatements() {
        if (!window.confirm(_t("Email a PDF statement to every partner on this screen who has an email address?"))) {
            return;
        }
        this.state.busy = true;
        try {
            const res = await this.orm.call("ebshel.fin.report", "action_send_statements", [this.rid], { options: this.state.options });
            this.notification.add(_t("%s statement(s) sent, %s partner(s) without an email skipped.", res.sent, res.skipped), { type: "success" });
        } finally {
            this.state.busy = false;
        }
    }

    // ------------------------------------------------------------------ how it looks
    loadDisplay() {
        const base = { density: "cozy", bars: false, zebra: true, signs: true };
        try {
            return { ...base, ...JSON.parse(window.localStorage.getItem("ebshel_fin_display") || "{}") };
        } catch {
            return base;
        }
    }

    setDisplay(key, value) {
        this.state.display[key] = value;
        try {
            window.localStorage.setItem("ebshel_fin_display", JSON.stringify(this.state.display));
        } catch {
            // private mode: the choice lasts as long as the page
        }
    }

    get rootClass() {
        const d = this.state.display;
        return ["o_efr", "o_action", "d-flex", "flex-column", "o_efr_" + d.density, d.zebra ? "o_efr_zebra" : "",
                d.signs ? "o_efr_signs" : "", this.state.full ? "o_efr_full" : "",
                this.state.refreshing ? "o_efr_refreshing" : ""].join(" ");
    }

    get barTops() {
        // the largest leaf figure of each column: what a full bar stands for
        const tops = [];
        for (const line of this.visibleLines) {
            if (line.bold || !line.columns) {
                continue;
            }
            line.columns.forEach((c, i) => {
                if (c.display === "amount" && c.value) {
                    tops[i] = Math.max(tops[i] || 0, Math.abs(c.value));
                }
            });
        }
        return tops;
    }

    barStyle(line, cell, index) {
        if (!this.state.display.bars || line.bold || cell.display !== "amount" || !cell.value) {
            return "";
        }
        const top = this.barTops[index] || 0;
        if (!top) {
            return "";
        }
        const pct = Math.max(2, Math.round((Math.abs(cell.value) / top) * 100));
        const colour = cell.value < 0 ? "rgba(220, 38, 38, .14)" : "rgba(37, 99, 235, .14)";
        return `background-image: linear-gradient(to left, ${colour} ${pct}%, transparent ${pct}%);`;
    }

    get shareBase() {
        const code = this.state.report && this.state.report.share_code;
        return code ? this.state.lines.find((l) => l.code === code) : null;
    }

    shareText(line, cell, index) {
        if (!this.state.options.share || cell.display !== "amount" || cell.value === null || cell.value === undefined) {
            return "";
        }
        const base = this.shareBase;
        const b = base && base.columns[index] && base.columns[index].value;
        if (!b) {
            return "";
        }
        return ((cell.value / b) * 100).toFixed(1) + "%";
    }

    setUnit(unit) {
        this.reload({ unit });
    }

    toggleFull() {
        this.state.full = !this.state.full;
    }

    // ------------------------------------------------------------------ periods, quickly
    byMonth(count) {
        const d = this.state.options.date;
        const to = deserializeDate(d.to);
        const from = to.startOf("month");
        const end = to.endOf("month");
        this.reload({
            date: { preset: "custom", from: serializeDate(from), to: serializeDate(end) },
            comparison: { mode: "previous", periods: count - 1, from: null, to: null }, growth: false,
        });
    }

    // ------------------------------------------------------------------ the wider filters
    openMore() {
        const o = this.state.options;
        this.state.draft = {
            journal_types: [...(o.journal_types || [])], partner_categories: [...(o.partner_categories || [])],
            salespeople: [...(o.salespeople || [])], teams: [...(o.teams || [])],
            product_categories: [...(o.product_categories || [])], label: o.label || "",
            amount_min: o.amount_min === null || o.amount_min === undefined ? "" : o.amount_min,
            amount_max: o.amount_max === null || o.amount_max === undefined ? "" : o.amount_max,
            unreconciled: !!o.unreconciled,
        };
        this.openMenu("filters");
    }

    draftToggle(key, id) {
        const list = this.state.draft[key];
        const i = list.indexOf(id);
        if (i >= 0) {
            list.splice(i, 1);
        } else {
            list.push(id);
        }
    }

    applyMore() {
        const d = this.state.draft;
        this.reload({
            journal_types: d.journal_types, partner_categories: d.partner_categories, salespeople: d.salespeople,
            teams: d.teams, product_categories: d.product_categories, label: d.label,
            amount_min: d.amount_min === "" ? null : Number(d.amount_min),
            amount_max: d.amount_max === "" ? null : Number(d.amount_max), unreconciled: d.unreconciled,
        });
    }

    get moreCount() {
        const o = this.state.options;
        let n = 0;
        for (const key of ["journal_types", "partner_categories", "salespeople", "teams", "product_categories"]) {
            n += (o[key] || []).length ? 1 : 0;
        }
        n += o.label ? 1 : 0;
        n += o.amount_min !== null && o.amount_min !== undefined ? 1 : 0;
        n += o.amount_max !== null && o.amount_max !== undefined ? 1 : 0;
        n += o.unreconciled ? 1 : 0;
        return n;
    }

    nameIn(list, id) {
        const found = (list || []).find((x) => (Array.isArray(x) ? x[0] : x.id) === id);
        return found ? (Array.isArray(found) ? found[1] : found.name) : "#" + id;
    }

    /** Every filter that narrows the report, as a chip that can be taken off. */
    get activeChips() {
        const o = this.state.options, c = this.state.choices, chips = [];
        const many = (key, label, list, icon) => {
            const ids = o[key] || [];
            if (ids.length) {
                const names = ids.slice(0, 2).map((id) => this.nameIn(list, id)).join(", ");
                chips.push({ key, icon, tone: key, text: `${label}: ${names}${ids.length > 2 ? " +" + (ids.length - 2) : ""}`, clear: { [key]: [] } });
            }
        };
        many("journals", _t("Journals"), c.journals, "fa-book");
        many("journal_types", _t("Journal type"), c.journal_types, "fa-tags");
        many("analytic", _t("Analytic"), c.analytic, "fa-sitemap");
        many("partners", _t("Partners"), c.partners, "fa-user");
        many("partner_categories", _t("Partner tag"), c.partner_categories, "fa-tag");
        many("salespeople", _t("Salesperson"), c.salespeople, "fa-id-badge");
        many("teams", _t("Sales team"), c.teams, "fa-users");
        many("product_categories", _t("Product category"), c.product_categories, "fa-cubes");
        if (o.accounts_query) {
            chips.push({ key: "accounts_query", icon: "fa-search", tone: "accounts", text: _t("Account: %s", o.accounts_query), clear: { accounts_query: "" } });
        }
        if (o.label) {
            chips.push({ key: "label", icon: "fa-quote-left", tone: "label", text: _t("Label contains: %s", o.label), clear: { label: "" } });
        }
        if (o.amount_min !== null && o.amount_min !== undefined || o.amount_max !== null && o.amount_max !== undefined) {
            chips.push({ key: "amount", icon: "fa-sliders", tone: "amount",
                         text: _t("Amount: %(a)s to %(b)s", { a: o.amount_min ?? 0, b: o.amount_max ?? "∞" }), clear: { amount_min: null, amount_max: null } });
        }
        if (o.unreconciled) {
            chips.push({ key: "unreconciled", icon: "fa-chain-broken", tone: "unreconciled", text: _t("Unreconciled only"), clear: { unreconciled: false } });
        }
        if (o.posted_only === false) {
            chips.push({ key: "posted_only", icon: "fa-pencil-square-o", tone: "draft", text: _t("Draft entries included"), clear: { posted_only: true } });
        }
        if (o.unit && o.unit !== 1) {
            chips.push({ key: "unit", icon: "fa-compress", tone: "unit", text: _t("In %s", this.state.unitLabel), clear: { unit: 1 } });
        }
        if (o.share) {
            chips.push({ key: "share", icon: "fa-percent", tone: "share", text: _t("Share of %s", this.shareBase ? this.shareBase.name : ""), clear: { share: false } });
        }
        return chips;
    }

    clearChip(chip) {
        this.reload(chip.clear);
    }

    clearAll() {
        const patch = {};
        for (const chip of this.activeChips) {
            Object.assign(patch, chip.clear);
        }
        this.reload(patch);
    }

    // ------------------------------------------------------------------ another report
    switchReport(report) {
        this.state.menu = null;
        if (report.key === this.state.report.key) {
            return;
        }
        this.reportKey = report.key;
        const keep = { date: { ...this.state.options.date }, unit: this.state.options.unit };
        this.state.report = null;
        this.state.options = keep;
        this.state.search = "";
        this.state.movers = null;
        this.state.chart = false;
        this.state.explain = null;
        this.state.lines = [];
        this.load();
    }

    get reportGroups() {
        const families = [
            [_t("Statements"), ["statement"]],
            [_t("Ledgers"), ["general_ledger", "trial_balance", "journal", "day_book", "cash_book"]],
            [_t("Partners"), ["partner_ledger", "aged", "statement_of_account"]],
            [_t("Tax and analysis"), ["tax", "analytic"]],
        ];
        const used = new Set();
        const groups = families.map(([label, kinds]) => {
            const items = this.state.reports.filter((r) => kinds.includes(r.kind));
            items.forEach((r) => used.add(r.key));
            return { label, items };
        });
        groups.push({ label: _t("More"), items: this.state.reports.filter((r) => !used.has(r.key)) });
        return groups.filter((g) => g.items.length);
    }

    // ------------------------------------------------------------------ what changed
    async toggleMovers() {
        if (this.state.movers) {
            this.state.movers = null;
            return;
        }
        this.state.explain = null;
        this.state.movers = { loading: true, rows: [] };
        await this.loadMovers();
    }

    async loadMovers() {
        const data = await this.orm.call("ebshel.fin.report", "get_movers", [this.rid], { options: this.state.options, limit: 10 });
        if (this.state.movers) {
            this.state.movers = { loading: false, ...data };
        }
    }

    moverWidth(row) {
        const top = Math.max(...this.state.movers.rows.map((r) => Math.abs(r.change)), 1);
        return Math.max(3, Math.round((Math.abs(row.change) / top) * 100));
    }

    openMover(row) {
        const m = this.state.movers;
        this.action.doAction({
            type: "ir.actions.act_window", name: row.name, res_model: "account.move.line",
            views: [[false, "list"], [false, "pivot"]], domain: [...m.domain_now, ["account_id", "=", row.account_id]],
            context: { create: false },
        });
    }

    // ------------------------------------------------------------------ the chart
    get chartData() {
        const cols = this.state.columns.map((c, i) => ({ ...c, index: i })).filter((c) => c.type === "amount").slice(0, 3);
        const lines = this.state.lines.filter((l) => (l.level || 0) <= 1 && l.columns && l.columns.length
            && l.columns[0].display === "amount" && l.columns[0].value !== null && l.kind !== "header"
            && !["more", "move_line", "open_item", "initial"].includes(l.kind)).slice(0, 14);
        if (!lines.length || !cols.length) {
            return null;
        }
        const top = Math.max(...lines.flatMap((l) => cols.map((c) => Math.abs((l.columns[c.index] || {}).value || 0))), 1);
        const tones = ["#2563eb", "#f59e0b", "#0d9488"];
        return {
            cols: cols.map((c, i) => ({ label: c.label, tone: tones[i] })),
            rows: lines.map((l) => ({
                id: l.id, name: l.name, bold: l.bold,
                bars: cols.map((c, i) => {
                    const cell = l.columns[c.index] || {};
                    const v = cell.value || 0;
                    return { width: Math.max(v ? 1 : 0, Math.round((Math.abs(v) / top) * 100)), tone: v < 0 ? "#dc2626" : tones[i], text: cell.text || "" };
                }),
            })),
        };
    }

    // ------------------------------------------------------------------ adding figures up
    /** Shift+click picks figures; the browser would also sweep a text selection across the page. */
    keepText(ev) {
        if (ev.shiftKey) {
            ev.preventDefault();
        }
    }

    select(line, cell, index) {
        if (cell.value === null || cell.value === undefined) {
            return;
        }
        const key = line.id + "|" + index;
        if (key in this.state.selected) {
            delete this.state.selected[key];
        } else {
            this.state.selected[key] = { value: cell.value, display: cell.display };
        }
    }

    isSelected(line, index) {
        return (line.id + "|" + index) in this.state.selected;
    }

    get selection() {
        const values = Object.values(this.state.selected).map((s) => s.value);
        if (!values.length) {
            return null;
        }
        const sum = values.reduce((t, v) => t + v, 0);
        return { count: values.length, sum: this.number(sum), avg: this.number(sum / values.length),
                 min: this.number(Math.min(...values)), max: this.number(Math.max(...values)) };
    }

    number(v) {
        const c = this.state.currency || {};
        const unit = this.state.options.unit || 1;
        const scaled = Math.abs(v / unit) < 0.005 ? 0 : v / unit;
        const text = scaled.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        if (unit !== 1) {
            return text;
        }
        return c.position === "before" ? `${c.symbol || ""} ${text}` : `${text} ${c.symbol || ""}`;
    }

    clearSelection() {
        this.state.selected = {};
    }

    // ------------------------------------------------------------------ keyboard
    keyboard(ev) {
        if (ev.target && /INPUT|TEXTAREA|SELECT/.test(ev.target.tagName)) {
            return;
        }
        if (ev.key === "Escape") {
            this.closeExport();
            this.state.menu = null;
            this.state.help = false;
            this.state.explain = null;
            this.state.movers = null;
            this.clearSelection();
            return;
        }
        if (ev.key === "?") {
            this.state.help = !this.state.help;
            return;
        }
        if (ev.key === "/") {
            const input = document.querySelector(".o_efr_search input");
            if (input) {
                ev.preventDefault();
                input.focus();
            }
            return;
        }
        if (ev.key === "i" || ev.key === "e") {
            const line = this.visibleLines.find((l) => l.id === this.state.focus);
            if (line && ev.key === "i" && this.canOpenItems(line)) {
                this.toggleItems(line);
            } else if (line && ev.key === "e") {
                const col = (line.columns || []).find((c) => c.drill);
                if (col) {
                    this.explain(line, col, ev);
                }
            }
            return;
        }
        if (!["ArrowDown", "ArrowUp", "ArrowRight", "ArrowLeft", "Enter"].includes(ev.key)) {
            return;
        }
        const lines = this.shownLines;
        if (!lines.length) {
            return;
        }
        let idx = lines.findIndex((l) => l.id === this.state.focus);
        if (ev.key === "ArrowDown") {
            idx = Math.min(lines.length - 1, idx + 1);
        } else if (ev.key === "ArrowUp") {
            idx = Math.max(0, idx - 1);
        } else if (idx >= 0) {
            const line = lines[idx];
            if (ev.key === "ArrowRight" && line.unfoldable && !line.unfolded) {
                this.toggle(line);
            } else if (ev.key === "ArrowLeft" && line.unfoldable && line.unfolded) {
                this.toggle(line);
            } else if (ev.key === "Enter") {
                const col = line.columns.find((c) => c.drill);
                if (col) {
                    this.drill(line, col, ev);
                }
            }
            ev.preventDefault();
            return;
        }
        if (idx >= 0) {
            this.state.focus = lines[idx].id;
            ev.preventDefault();
            const el = this.tableRef.el && this.tableRef.el.querySelector(`[data-line="${CSS.escape(lines[idx].id)}"]`);
            if (el) {
                el.scrollIntoView({ block: "nearest" });
            }
        }
    }

    rowClass(line) {
        const cls = ["o_efr_row", "o_efr_k_" + (line.kind || "line"), "o_efr_l" + Math.min(line.level || 0, 6)];
        if (this.state.items[line.id]) cls.push("o_efr_has_items");
        if (line.bold) cls.push("o_efr_bold");
        if (line.unfoldable) cls.push("o_efr_foldable");
        if (line.unfolded) cls.push("o_efr_open");
        if (line.class) cls.push("o_efr_" + line.class);
        if (this.state.focus === line.id) cls.push("o_efr_focus");
        return cls.join(" ");
    }
}

registry.category("actions").add("ebshel_fin_report", FinReportViewer);
