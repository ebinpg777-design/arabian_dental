/** @odoo-module **/

import { Component, onWillStart, onMounted, onWillUnmount, useState, useRef } from "@odoo/owl";
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
        });
        this.loadToken = 0;
        this.onDocClick = (ev) => {
            if (this.state.menu && !ev.target.closest(".o_efr_pop, .o_efr_popbtn")) {
                this.state.menu = null;
            }
        };
        this.onKey = (ev) => this.keyboard(ev);
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
        this.state.loading = true;
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
            if (this.state.options.trend) {
                this.loadTrends();
            }
        } catch (error) {
            this.state.error = (error && error.data && error.data.message) || String(error);
        } finally {
            if (token === this.loadToken) {
                this.state.loading = false;
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

    get periodLabel() {
        return this.state.columns.length ? this.state.columns[0].label : "";
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

    async drill(line, col, ev) {
        ev.stopPropagation();
        if (!col.drill) {
            return;
        }
        const colKey = this.state.columns[line.columns.indexOf(col)].key;
        const action = await this.orm.call("ebshel.fin.report", "get_drill_action", [this.rid], {
            options: this.state.options, line_id: line.id, column_key: colKey,
        });
        if (action) {
            this.action.doAction(action);
        }
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

    async exportXlsx() {
        this.state.busy = true;
        try {
            const file = await this.orm.call("ebshel.fin.report", "export_xlsx", [this.rid], { options: this.state.options });
            const bytes = Uint8Array.from(atob(file.content), (c) => c.charCodeAt(0));
            const url = URL.createObjectURL(new Blob([bytes], { type: file.mimetype }));
            const a = document.createElement("a");
            a.href = url;
            a.download = file.filename;
            document.body.appendChild(a);
            a.click();
            a.remove();
            setTimeout(() => URL.revokeObjectURL(url), 2000);
        } finally {
            this.state.busy = false;
        }
    }

    async exportPdf() {
        const action = await this.orm.call("ebshel.fin.report", "get_pdf_action", [this.rid], { options: this.state.options });
        this.action.doAction(action);
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

    // ------------------------------------------------------------------ keyboard
    keyboard(ev) {
        if (ev.target && /INPUT|TEXTAREA|SELECT/.test(ev.target.tagName)) {
            return;
        }
        if (!["ArrowDown", "ArrowUp", "ArrowRight", "ArrowLeft", "Enter"].includes(ev.key)) {
            return;
        }
        const lines = this.visibleLines;
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
        if (line.bold) cls.push("o_efr_bold");
        if (line.unfoldable) cls.push("o_efr_foldable");
        if (line.unfolded) cls.push("o_efr_open");
        if (line.class) cls.push("o_efr_" + line.class);
        if (this.state.focus === line.id) cls.push("o_efr_focus");
        return cls.join(" ");
    }
}

registry.category("actions").add("ebshel_fin_report", FinReportViewer);
