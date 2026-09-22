/** @odoo-module **/
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { Component, useState, onWillStart, onWillUpdateProps } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { ControlPanel } from "@web/search/control_panel/control_panel";
import { DomainSelector } from "@web/core/domain_selector/domain_selector";
import { user } from "@web/core/user";
import { Domain } from "@web/core/domain";

/**
 * A domain as the Python-literal string the domain editor and the server both
 * read. Older templates stored JSON (true/false/null), which is not Python:
 * convert it rather than let the server fail to read it.
 */
function toDomainString(domain) {
    if (Array.isArray(domain)) return new Domain(domain).toString();
    const text = (domain || "").trim();
    if (!text) return "[]";
    try {
        const parsed = JSON.parse(text);
        if (Array.isArray(parsed)) return new Domain(parsed).toString();
    } catch {
        // Not JSON: already a Python literal (possibly with uid, context_today()...).
    }
    return text;
}

class ExportDialog extends Component {
    static template = "excel_report_builder.ExportDialog";
    static components = { Dialog, DomainSelector };

    static props = {
        model: String,
        ids: { type: Array, optional: true },
        initialDomain: { type: String, optional: true },
        listViewFields: { type: Array, optional: true },
        close: Function,
    };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.fieldPath = useState([]);
        this.state = useState({
            availableFields: [],
            groupFields: [],
            selectedFields: [],
            previewData: [],
            fieldSearchQuery: "",
            domain: toDomainString(this.props.initialDomain),
            templates: [],
            selectedTemplateId: null,
            existingTemplateSelected: false,
            fieldType: "standard",
            selectedGroupFields: [],
            headerBgColor: "#e3e2de",
            headerFontColor: "#000000",
            filename: "",
            freezeHeader: true,
            alternateRows: false,
            showTotals: false,
            sheetName: "",
            exportTitle: "",
            sortField: "",
            sortDir: "asc",
            exportLimit: "",
            showRecordCount: false,
            addSummarySheet: false,
            showFormulaInput: false,
            newFormulaLabel: "",
            newFormulaExpr: "",
            formulaError: "",
            showSaveInput: false,
            newTemplateName: "",
            recordCount: null,
            isCountLoading: false,
            editingHeader: { fieldName: null, value: "" },
            // Charting
            chartType: "",
            chartMeasure: "",
            // Excel AutoFilter
            autoFilter: false,
            // Scheduled delivery
            scheduleEnabled: false,
            scheduleInterval: "weekly",
            scheduleEmailTo: "",
        });

        onWillStart(async () => {
            await this.loadFields(this.props.model);
            await this.loadTemplates();
            await this.fetchRecordCount();
        });
    }

    // ── Computed grouped-mode helpers ────────────────────────────────────────

    get effectiveGroupBy() {
        return this.state.selectedGroupFields
            .map(f => f.period ? `${f.name}:${f.period}` : f.name)
            .join(',');
    }

    // ── Report type ──────────────────────────────────────────────────────────

    async toggleFieldType(type) {
        const prevType = this.state.fieldType;
        this.state.fieldType = this.state.fieldType === type ? "standard" : type;

        if (prevType === "group" && this.state.fieldType !== "group") {
            // Restore all dimension fields to available pool
            for (const gf of [...this.state.selectedGroupFields]) {
                this.state.selectedFields = this.state.selectedFields.filter(f => f.name !== gf.name);
                this.state.availableFields.push({ name: gf.name, string: gf.string, type: gf.type });
            }
            this.state.selectedFields = this.state.selectedFields.filter(f => f.name !== '__count');
            this.state.selectedGroupFields = [];
            this.state.showRecordCount = false;
            await this.loadFields(this.props.model);
            const selectedNames = new Set(this.state.selectedFields.map(f => f.name));
            this.state.availableFields = this.state.availableFields.filter(f => !selectedNames.has(f.name));
        }

        await this.updatePreview();
    }

    // ── Multi group-by field management ──────────────────────────────────────

    async onAddGroupByField(ev) {
        const name = ev.target.value;
        if (!name) return;
        ev.target.value = "";
        const fieldObj = this.state.groupFields.find(f => f.name === name);
        if (!fieldObj) return;
        await this.addGroupByField(fieldObj);
    }

    async addGroupByField(field) {
        if (this.state.selectedGroupFields.some(f => f.name === field.name)) return;
        const period = ['date', 'datetime'].includes(field.type) ? 'month' : '';
        this.state.selectedGroupFields.push({ name: field.name, string: field.string, type: field.type, period });
        const already = this.state.selectedFields.some(f => f.name === field.name);
        if (!already) {
            this.state.selectedFields.unshift({ ...field, agg_type: null });
            this.state.availableFields = this.state.availableFields.filter(f => f.name !== field.name);
        }
        await this.updatePreview();
    }

    async removeGroupByField(index) {
        const gf = this.state.selectedGroupFields[index];
        if (!gf) return;
        this.state.selectedGroupFields.splice(index, 1);
        this.state.selectedFields = this.state.selectedFields.filter(f => f.name !== gf.name);
        this.state.availableFields.push({ name: gf.name, string: gf.string, type: gf.type });
        await this.updatePreview();
    }

    onGroupByPeriodChange(index, period) {
        const gf = this.state.selectedGroupFields[index];
        if (gf) gf.period = period;
        this.updatePreview();
    }

    toggleRecordCount() {
        this.state.showRecordCount = !this.state.showRecordCount;
        if (this.state.showRecordCount) {
            const already = this.state.selectedFields.some(f => f.name === '__count');
            if (!already) {
                this.state.selectedFields.push({ name: '__count', string: '# Records', type: 'integer', agg_type: 'count' });
            }
        } else {
            this.state.selectedFields = this.state.selectedFields.filter(f => f.name !== '__count');
        }
        this.updatePreview();
    }

    // ── Color / filename / toggles ───────────────────────────────────────────

    onColorChange(type, ev) {
        this.state[type] = ev.target.value;
    }

    onFilenameChange(ev) { this.state.filename = ev.target.value; }
    onSheetNameChange(ev) { this.state.sheetName = ev.target.value; }
    onExportLimitChange(ev) { this.state.exportLimit = ev.target.value; }
    onSortFieldChange(ev) { this.state.sortField = ev.target.value; }
    toggleSortDir() { this.state.sortDir = this.state.sortDir === 'asc' ? 'desc' : 'asc'; }

    // ── Chart ────────────────────────────────────────────────────────────────

    onChartTypeChange(ev) { this.state.chartType = ev.target.value; }
    onChartMeasureChange(ev) { this.state.chartMeasure = ev.target.value; }

    // ── AutoFilter ───────────────────────────────────────────────────────────

    onAutoFilterToggle(ev) { this.state.autoFilter = ev.target.checked; }

    // ── Analytic column transforms ─────────────────────────────────────────────

    onTransformChange(index, ev) {
        const f = this.state.selectedFields[index];
        if (f) f.transform = ev.target.value;
        this.updatePreview();
    }

    // ── Schedule ─────────────────────────────────────────────────────────────

    onScheduleToggle(ev) { this.state.scheduleEnabled = ev.target.checked; }
    onScheduleIntervalChange(ev) { this.state.scheduleInterval = ev.target.value; }
    onScheduleEmailChange(ev) { this.state.scheduleEmailTo = ev.target.value; }

    // ── Per-column conditional formatting ──────────────────────────────────────

    onCondFormatChange(index, ev) {
        const f = this.state.selectedFields[index];
        if (!f) return;
        f.cond_format = ev.target.value;
        // Drop the legacy boolean flag — cond_format now drives everything.
        if ("heatmap" in f) delete f.heatmap;
        if (f.cond_format === 'threshold') {
            f.cond_op = f.cond_op || '>';
            f.cond_value = f.cond_value || '0';
            f.cond_color = f.cond_color || '#FFC7CE';
        } else if (f.cond_format === 'top' || f.cond_format === 'bottom') {
            f.cond_value = f.cond_value || '10';
            f.cond_color = f.cond_color || (f.cond_format === 'top' ? '#C6EFCE' : '#FFC7CE');
        }
    }
    onCondOpChange(index, ev) {
        const f = this.state.selectedFields[index];
        if (f) f.cond_op = ev.target.value;
    }
    onCondValueChange(index, ev) {
        const f = this.state.selectedFields[index];
        if (f) f.cond_value = ev.target.value;
    }
    onCondColorChange(index, ev) {
        const f = this.state.selectedFields[index];
        if (f) f.cond_color = ev.target.value;
    }

    // ── Templates ────────────────────────────────────────────────────────────

    async loadTemplates() {
        this.state.templates = await this.orm.call(
            "dynamic.export.template", "get_templates", [this.props.model]
        );
    }

    async onTemplateChange(ev) {
        const val = ev.target.value;
        if (!val) {
            // "-- New Report --" selected — reset to blank state without clearing fields
            this.state.selectedTemplateId = null;
            this.state.existingTemplateSelected = false;
            this.state.showSaveInput = false;
            return;
        }
        const templateId = parseInt(val);
        const tpl = this.state.templates.find(t => t.id === templateId);
        if (!tpl) return;

        this.state.selectedTemplateId = tpl.id;
        this.state.existingTemplateSelected = true;
        this.state.showSaveInput = false;

        // Reset field navigation to root
        this.fieldPath.length = 0;
        await this.loadFields(this.props.model);

        const loadedFields = JSON.parse(tpl.fields_json || "[]");
        this.state.selectedFields = loadedFields;
        this.state.domain = toDomainString(tpl.domain);
        this.state.fieldType = tpl.field_type || "standard";
        // Parse multi-groupby: comma-separated "field1,field2:month,field3"
        const gbSpecs = (tpl.groupby || "").split(',').filter(Boolean);
        this.state.selectedGroupFields = gbSpecs.map(spec => {
            const colonIdx = spec.indexOf(':');
            const name = colonIdx >= 0 ? spec.substring(0, colonIdx) : spec;
            const period = colonIdx >= 0 ? spec.substring(colonIdx + 1) : '';
            const fieldObj = this.state.groupFields.find(f => f.name === name);
            return fieldObj
                ? { name: fieldObj.name, string: fieldObj.string, type: fieldObj.type, period }
                : { name, string: name, type: 'char', period };
        });
        // Restore showRecordCount from fields_json
        this.state.showRecordCount = (JSON.parse(tpl.fields_json || "[]")).some(f => f.name === '__count');
        this.state.filename = tpl.filename || "";
        this.state.sheetName = tpl.sheet_name || "";
        this.state.freezeHeader = tpl.freeze_header !== false;
        this.state.alternateRows = !!tpl.alternate_rows;
        this.state.showTotals = !!tpl.show_totals;
        this.state.sortField = tpl.sort_field || "";
        this.state.sortDir = tpl.sort_dir || "asc";
        this.state.exportLimit = tpl.export_limit ? String(tpl.export_limit) : "";
        this.state.exportTitle = tpl.export_title || "";
        if (tpl.header_bg_color) this.state.headerBgColor = tpl.header_bg_color;
        if (tpl.header_font_color) this.state.headerFontColor = tpl.header_font_color;

        // Chart
        this.state.chartType = tpl.chart_type || "";
        this.state.chartMeasure = tpl.chart_measure || "";
        this.state.autoFilter = !!tpl.auto_filter;
        // Schedule
        this.state.scheduleEnabled = !!tpl.schedule_enabled;
        this.state.scheduleInterval = tpl.schedule_interval || "weekly";
        this.state.scheduleEmailTo = tpl.schedule_email_to || "";

        const selectedNames = new Set(loadedFields.map(f => f.name));
        this.state.availableFields = this.state.availableFields.filter(f => !selectedNames.has(f.name));

        await this.updatePreview();
        await this.fetchRecordCount();
    }

    showSaveNewInput() {
        this.state.showSaveInput = true;
        this.state.newTemplateName = "";
    }

    cancelSaveInput() {
        this.state.showSaveInput = false;
        this.state.newTemplateName = "";
    }

    /** Chart / autofilter / schedule columns persisted alongside a template. */
    _extraTemplateVals() {
        return {
            chart_type: this.state.chartType || "",
            chart_measure: this.state.chartMeasure || "",
            auto_filter: this.state.autoFilter,
            schedule_enabled: this.state.scheduleEnabled,
            schedule_interval: this.state.scheduleInterval || "weekly",
            schedule_email_to: this.state.scheduleEmailTo || "",
        };
    }

    /** A scheduled report must have at least one recipient to be deliverable. */
    _validateSchedule() {
        if (this.state.scheduleEnabled && !(this.state.scheduleEmailTo || "").trim()) {
            this.notification.add(
                "Add at least one recipient email to schedule this report.",
                { type: "warning" },
            );
            return false;
        }
        return true;
    }

    async confirmSaveNew() {
        const name = (this.state.newTemplateName || "").trim();
        if (!name) {
            this.notification.add("Please enter a template name.", { type: "warning" });
            return false;
        }
        if (this.state.selectedFields.length === 0) {
            this.notification.add("Add at least one column before saving.", { type: "warning" });
            return false;
        }
        if (!this._validateSchedule()) return false;
        const ids = await this.orm.create("dynamic.export.template", [{
            name,
            res_model: this.props.model,
            fields_json: JSON.stringify(this.state.selectedFields),
            domain: this.state.domain,
            field_type: this.state.fieldType,
            groupby: this.effectiveGroupBy,
            filename: this.state.filename,
            sheet_name: this.state.sheetName || "Data",
            freeze_header: this.state.freezeHeader,
            alternate_rows: this.state.alternateRows,
            show_totals: this.state.showTotals,
            header_bg_color: this.state.headerBgColor,
            header_font_color: this.state.headerFontColor,
            sort_field: this.state.sortField,
            sort_dir: this.state.sortDir,
            export_limit: parseInt(this.state.exportLimit) || 0,
            export_title: this.state.exportTitle || '',
            ...this._extraTemplateVals(),
        }]);
        // Treat the newly-created record as the active template so a follow-up
        // export updates it rather than creating a duplicate.
        if (Array.isArray(ids) && ids.length) {
            this.state.selectedTemplateId = ids[0];
            this.state.existingTemplateSelected = true;
        }
        this.notification.add(`Template "${name}" saved.`, { title: "Saved", type: "success" });
        this.state.showSaveInput = false;
        this.state.newTemplateName = "";
        await this.loadTemplates();
        return true;
    }

    async updateTemplate() {
        const tpl = this.state.templates.find(t => t.id === this.state.selectedTemplateId);
        if (!tpl) return false;
        if (!this._validateSchedule()) return false;
        await this.orm.write("dynamic.export.template", [tpl.id], {
            fields_json: JSON.stringify(this.state.selectedFields),
            domain: this.state.domain,
            field_type: this.state.fieldType,
            groupby: this.effectiveGroupBy,
            filename: this.state.filename,
            sheet_name: this.state.sheetName || "Data",
            freeze_header: this.state.freezeHeader,
            alternate_rows: this.state.alternateRows,
            show_totals: this.state.showTotals,
            header_bg_color: this.state.headerBgColor,
            header_font_color: this.state.headerFontColor,
            sort_field: this.state.sortField,
            sort_dir: this.state.sortDir,
            export_limit: parseInt(this.state.exportLimit) || 0,
            export_title: this.state.exportTitle || '',
            ...this._extraTemplateVals(),
        });
        this.notification.add(`Template "${tpl.name}" updated.`, { type: "success" });
        await this.loadTemplates();
        return true;
    }

    /**
     * A schedule only fires from a saved template (the cron iterates templates),
     * so persist before exporting. Returns false if the save is blocked, e.g.
     * a new template still needs a name — in which case export is aborted.
     */
    async _persistScheduleTemplate() {
        if (!this._validateSchedule()) return false;
        if (this.state.existingTemplateSelected && this.state.selectedTemplateId) {
            return await this.updateTemplate();
        }
        if (!(this.state.newTemplateName || "").trim()) {
            this.state.showSaveInput = true;
            this.notification.add(
                "Enter a template name and save — scheduled reports must be saved as a template.",
                { title: "Save required", type: "warning" },
            );
            return false;
        }
        return await this.confirmSaveNew();
    }

    async deleteTemplate() {
        const tpl = this.state.templates.find(t => t.id === this.state.selectedTemplateId);
        if (!tpl) return;
        if (!confirm(`Delete template "${tpl.name}"? This cannot be undone.`)) return;
        const ok = await this.orm.call("dynamic.export.template", "delete_template", [tpl.id]);
        if (ok) {
            this.notification.add(`Template "${tpl.name}" deleted.`, { type: "success" });
            this.state.selectedTemplateId = null;
            this.state.existingTemplateSelected = false;
            await this.loadTemplates();
        } else {
            this.notification.add("Cannot delete: you do not own this template.", { type: "danger" });
        }
    }

    // ── Field navigation ─────────────────────────────────────────────────────

    async loadFields(modelName, prefix = "") {
        const fields = await this.orm.call("report.dynamic_xlsx_export", "get_model_fields", [modelName]);
        const mapped = fields.map(f => ({
            ...f,
            real_name: f.name,
            name: prefix ? `${prefix}${f.name}` : f.name,
            string: prefix ? `${prefix.replace(/\/$/, '').split('/').pop()} → ${f.string}` : f.string,
        }));
        this.state.availableFields = mapped;
        if (!prefix) {
            // one2many fields cannot be used as group-by dimensions in read_group
            this.state.groupFields = mapped.filter(f => f.type !== 'one2many');
        }
    }

    async onExpandRelation(field) {
        this.state.fieldSearchQuery = "";
        this.fieldPath.push({ model: field.relation_model, prefix: `${field.name}/`, label: field.string });
        await this.loadFields(field.relation_model, `${field.name}/`);
    }

    async goBack() {
        this.state.fieldSearchQuery = "";
        this.fieldPath.pop();
        const prev = this.fieldPath[this.fieldPath.length - 1];
        if (prev) {
            await this.loadFields(prev.model, prev.prefix);
        } else {
            await this.loadFields(this.props.model);
        }
    }

    get filteredFields() {
        let fields = this.state.availableFields || [];
        if (this.state.fieldType === "group" && this.state.selectedGroupFields.length > 0) {
            const numeric = new Set(["monetary", "integer", "float"]);
            // Only stored fields can be aggregated by read_group — non-stored computed fields fail at the DB level
            fields = fields.filter(f => numeric.has(f.type) && f.store !== false);
        }
        const q = (this.state.fieldSearchQuery || "").trim().toLowerCase();
        if (!q) return fields;
        return fields.filter(f =>
            f.string?.toLowerCase().includes(q) || f.name?.toLowerCase().includes(q)
        );
    }

    onFieldSearch(ev) {
        this.state.fieldSearchQuery = ev.target.value;
    }

    // ── Domain ───────────────────────────────────────────────────────────────

    async onDomainChange(domain) {
        this.state.domain = domain;
        await Promise.all([this.updatePreview(), this.fetchRecordCount()]);
    }

    /**
     * The current filter as a domain list, evaluated like the server does.
     * Throws on an unreadable domain: callers show "no preview" rather than
     * silently previewing the whole model.
     */
    _parseDomain() {
        return new Domain(toDomainString(this.state.domain)).toList(user.context);
    }

    async fetchRecordCount() {
        this.state.isCountLoading = true;
        try {
            const dom = this._parseDomain();
            if (this.props.ids?.length > 0) dom.push(['id', 'in', this.props.ids]);
            this.state.recordCount = await this.orm.call(this.props.model, "search_count", [dom]);
        } catch {
            this.state.recordCount = null;
        } finally {
            this.state.isCountLoading = false;
        }
    }

    // ── Preview ──────────────────────────────────────────────────────────────

    async updatePreview() {
        if (this.state.selectedFields.length === 0) {
            this.state.previewData = [];
            return;
        }
        try {
            const dom = this._parseDomain();
            if (this.props.ids?.length > 0) dom.push(['id', 'in', this.props.ids]);

            if (this.state.fieldType === "group" && this.state.selectedGroupFields.length > 0) {
                const gbList = this.state.selectedGroupFields.map(f =>
                    f.period ? `${f.name}:${f.period}` : f.name
                );
                const baseFieldNames = new Set(this.state.selectedGroupFields.map(f => f.name));
                const measures = this.state.selectedFields.filter(
                    f => !baseFieldNames.has(f.name) && f.name !== '__count'
                );
                const aggs = measures.map(f => `${f.name}:${f.agg_type || 'sum'}`);

                const groups = await this.orm.call(this.props.model, "read_group", [dom, aggs, gbList], {
                    lazy: false,
                    limit: 5,
                });
                this.state.previewData = (groups || []).map(group => {
                    const row = { id: Math.random() };

                    // All group-by dimension values
                    for (const gf of this.state.selectedGroupFields) {
                        const gbSpec = gf.period ? `${gf.name}:${gf.period}` : gf.name;
                        const raw = group[gbSpec] ?? group[gf.name];
                        if (Array.isArray(raw))      row[gf.name] = raw[1] ?? raw[0] ?? "—";
                        else if (raw === false || raw == null) row[gf.name] = "Undefined / Empty";
                        else                         row[gf.name] = raw;
                    }

                    // Record count
                    row['__count'] = group.__count ?? 0;

                    // Measure fields
                    measures.forEach(f => {
                        const key = `${f.name}:${f.agg_type || 'sum'}`;
                        row[f.name] = group[key] !== undefined ? group[key]
                                    : group[f.name] !== undefined ? group[f.name] : 0;
                    });
                    return row;
                });
            } else {
                const spec = this._buildSpecification(this.state.selectedFields);
                const orderClause = this.state.sortField
                    ? `${this.state.sortField} ${this.state.sortDir}`
                    : undefined;
                const result = await this.orm.call(this.props.model, "web_search_read", [], {
                    domain: dom,
                    specification: spec,
                    limit: 5,
                    ...(orderClause ? { order: orderClause } : {}),
                });
                this.state.previewData = result.records || [];
            }
        } catch (err) {
            console.error("Preview error:", err);
            this.state.previewData = [];
        }
    }

    _buildSpecification(fields) {
        const spec = {};
        for (const f of fields) {
            if (f.name === '__count') continue;
            if (f.type === 'computed') {
                // Pull in the fields the formula references so the preview can
                // resolve them even when they aren't selected as columns.
                for (const ref of this._formulaFieldRefs(f.formula)) {
                    this._addToSpec(spec, ref.split('/'), 0, '');
                }
                continue;
            }
            this._addToSpec(spec, f.name.split('/'), 0, f.type);
        }
        return spec;
    }

    /** Extract {field} tokens from a formula string, returning bare field paths. */
    _formulaFieldRefs(formula) {
        const refs = [];
        const re = /\{([^}]+)\}/g;
        let m;
        while ((m = re.exec(formula || "")) !== null) {
            refs.push(m[1].trim());
        }
        return refs;
    }

    _addToSpec(spec, parts, idx, fieldType) {
        const part = parts[idx];
        if (idx === parts.length - 1) {
            if (!spec[part]) {
                spec[part] = ['many2one', 'many2many', 'one2many'].includes(fieldType)
                    ? { fields: { display_name: {} } } : {};
            }
        } else {
            if (!spec[part]) spec[part] = { fields: {} };
            else if (!spec[part].fields) spec[part].fields = {};
            this._addToSpec(spec[part].fields, parts, idx + 1, fieldType);
        }
    }

    // ── Column management ────────────────────────────────────────────────────

    async addToExport(field) {
        this.state.selectedFields.push({ ...field, agg_type: "sum" });
        this.state.availableFields = this.state.availableFields.filter(f => f.name !== field.name);
        await this.updatePreview();
    }

    async removeFromExport(field) {
        // If removing a dimension field, also remove it from selectedGroupFields
        const gbIdx = this.state.selectedGroupFields.findIndex(f => f.name === field.name);
        if (gbIdx >= 0) {
            this.state.selectedGroupFields.splice(gbIdx, 1);
        }
        // pseudo/computed fields don't go back into the available pool
        if (field.name === '__count') {
            this.state.showRecordCount = false;
        } else if (field.type !== 'computed') {
            this.state.availableFields.push(field);
        }
        this.state.selectedFields = this.state.selectedFields.filter(f => f.name !== field.name);
        await this.updatePreview();
    }

    async moveOrder(index, direction) {
        const arr = this.state.selectedFields;
        const target = index + direction;
        if (target >= 0 && target < arr.length) {
            [arr[index], arr[target]] = [arr[target], arr[index]];
            await this.updatePreview();
        }
    }

    async onAggregationChange(index, ev) {
        const f = this.state.selectedFields[index];
        if (f) {
            f.agg_type = ev.target.value;
            await this.updatePreview();
        }
    }

    startEditHeader(field) {
        this.state.editingHeader = { fieldName: field.name, value: field.string };
    }

    confirmEditHeader() {
        const { fieldName, value } = this.state.editingHeader;
        const trimmed = (value || "").trim();
        if (trimmed) {
            const f = this.state.selectedFields.find(sf => sf.name === fieldName);
            if (f) f.string = trimmed;
        }
        this.state.editingHeader = { fieldName: null, value: "" };
    }

    cancelEditHeader() {
        this.state.editingHeader = { fieldName: null, value: "" };
    }

    onHeaderKeydown(ev) {
        if (ev.key === 'Enter') this.confirmEditHeader();
        if (ev.key === 'Escape') this.cancelEditHeader();
    }

    // ── Preview value helper ─────────────────────────────────────────────────

    getValue(record, fieldPath) {
        if (!record || !fieldPath) return '';

        // Computed formula column
        const computedField = this.state.selectedFields.find(
            sf => sf.name === fieldPath && sf.type === 'computed'
        );
        if (computedField) {
            const result = this._computeFormula(computedField.formula, record);
            return result !== '' ? String(result) : '';
        }

        // Flat grouped preview rows
        if (this.state.fieldType === "group" && this.state.selectedGroupFields.length > 0) {
            if (fieldPath in record) {
                const v = record[fieldPath];
                return v !== null && v !== undefined ? String(v) : '';
            }
        }

        return this._getNestedValue(record, fieldPath.split('/'));
    }

    /**
     * Traverse a field path through a record object, handling arrays from
     * one2many / many2many mid-path by mapping across all items.
     */
    _getNestedValue(cur, parts) {
        for (let i = 0; i < parts.length; i++) {
            if (cur === null || cur === undefined || cur === false) return '';

            // Array = o2m or m2m result; map remaining path across each item
            if (Array.isArray(cur)) {
                const remaining = parts.slice(i);
                return cur
                    .map(item => this._getNestedValue(item, remaining))
                    .filter(v => v !== '')
                    .join(', ');
            }

            if (typeof cur !== 'object') return String(cur);
            if (!(parts[i] in cur)) return '';
            cur = cur[parts[i]];
        }

        if (cur === null || cur === undefined || cur === false) return '';
        if (Array.isArray(cur)) {
            return cur
                .map(item => typeof item === 'object'
                    ? (item.display_name || item.name || '')
                    : String(item))
                .filter(v => v !== '')
                .join(', ');
        }
        if (typeof cur === 'object') {
            return cur.display_name || cur.name || JSON.stringify(cur);
        }
        return String(cur);
    }

    // ── Field type badge metadata ────────────────────────────────────────────

    getFieldTypeBadge(type) {
        const map = {
            char:     { label: 'Text',  cls: 'erd-badge-text' },
            text:     { label: 'Text',  cls: 'erd-badge-text' },
            integer:  { label: 'Int',   cls: 'erd-badge-num' },
            float:    { label: 'Float', cls: 'erd-badge-num' },
            monetary: { label: '€',     cls: 'erd-badge-money' },
            boolean:  { label: 'Bool',  cls: 'erd-badge-bool' },
            date:     { label: 'Date',  cls: 'erd-badge-date' },
            datetime: { label: 'DT',    cls: 'erd-badge-date' },
            selection:{ label: 'Sel',   cls: 'erd-badge-sel' },
            many2one: { label: 'M2O',      cls: 'erd-badge-rel' },
            many2many:{ label: 'M2M',      cls: 'erd-badge-rel2' },
            one2many: { label: 'O2M',      cls: 'erd-badge-o2m' },
            computed:  { label: 'f(x)',    cls: 'erd-badge-formula' },
        };
        return map[type] || { label: type || '?', cls: 'erd-badge-text' };
    }

    // ── Computed formula columns ─────────────────────────────────────────────

    toggleFormulaInput() {
        this.state.showFormulaInput = !this.state.showFormulaInput;
        this.state.newFormulaLabel = "";
        this.state.newFormulaExpr = "";
        this.state.formulaError = "";
    }

    insertFieldToken(fieldName) {
        this.state.newFormulaExpr = (this.state.newFormulaExpr || '') + `{${fieldName}}`;
    }

    async confirmAddFormula() {
        const label   = (this.state.newFormulaLabel || "").trim();
        const formula = (this.state.newFormulaExpr  || "").trim();
        if (!label)   { this.state.formulaError = "Column label is required."; return; }
        if (!formula) { this.state.formulaError = "Formula expression is required."; return; }
        const name = `__computed_${Date.now()}`;
        this.state.selectedFields.push({ name, string: label, type: 'computed', formula, agg_type: 'sum' });
        this.state.showFormulaInput = false;
        this.state.newFormulaLabel = "";
        this.state.newFormulaExpr = "";
        this.state.formulaError = "";
        await this.updatePreview();
    }

    /**
     * Evaluate a formula string like "{price_unit} * {product_uom_qty}"
     * against a preview record object.
     */
    _computeFormula(formula, record) {
        try {
            const expr = (formula || '').replace(/\{([^}]+)\}/g, (_, ref) => {
                const raw = this._getNestedValue(record, ref.split('/'));
                const n = parseFloat(String(raw ?? 0).replace(/[^0-9.\-]/g, ''));
                return isNaN(n) ? '0' : `(${n})`;
            });
            // A formula can come from a shared template: only plain arithmetic
            // may reach new Function, never arbitrary script.
            if (!/^[0-9eE\s.+\-*\/%()]*$/.test(expr)) return '';
            // eslint-disable-next-line no-new-func
            const result = new Function('"use strict"; return (' + expr + ')')();
            return typeof result === 'number' && isFinite(result)
                ? parseFloat(result.toFixed(10))   // avoid floating-point noise
                : '';
        } catch {
            return '';
        }
    }

    // ── Per-column options (format + heatmap) ────────────────────────────────

    onNumberFormatChange(index, ev) {
        const f = this.state.selectedFields[index];
        if (f) f.num_format = ev.target.value;
    }

    // ── Quick add / clear ────────────────────────────────────────────────────

    async quickAddFields(filter) {
        const numericTypes = new Set(['integer', 'float', 'monetary']);
        const dateTypes    = new Set(['date', 'datetime']);
        const textTypes    = new Set(['char', 'text', 'selection']);
        let toAdd;

        if (filter === 'listview') {
            const listNames = new Set((this.props.listViewFields || []).map(f => f.name));
            const alreadySelected = new Set(this.state.selectedFields.map(f => f.name));
            // Resolve full field objects from groupFields (all base model fields with type info)
            toAdd = this.state.groupFields.filter(f => listNames.has(f.name) && !alreadySelected.has(f.name));
            // Also remove from availableFields if present there
        } else if (filter === 'numeric') toAdd = this.state.availableFields.filter(f => numericTypes.has(f.type));
        else if (filter === 'date')      toAdd = this.state.availableFields.filter(f => dateTypes.has(f.type));
        else if (filter === 'text')      toAdd = this.state.availableFields.filter(f => textTypes.has(f.type));
        else                             toAdd = [...this.state.availableFields];

        if (toAdd.length === 0) {
            this.notification.add("No matching fields to add.", { type: "info" });
            return;
        }
        const addedNames = new Set(toAdd.map(f => f.name));
        for (const f of toAdd) this.state.selectedFields.push({ ...f, agg_type: "sum" });
        this.state.availableFields = this.state.availableFields.filter(f => !addedNames.has(f.name));
        await this.updatePreview();
    }

    async duplicateTemplate() {
        const tpl = this.state.templates.find(t => t.id === this.state.selectedTemplateId);
        if (!tpl) return;
        const newName = `${tpl.name} (Copy)`;
        await this.orm.create("dynamic.export.template", [{
            name: newName,
            res_model: this.props.model,
            fields_json: JSON.stringify(this.state.selectedFields),
            domain: this.state.domain,
            field_type: this.state.fieldType,
            groupby: this.effectiveGroupBy,
            filename: this.state.filename,
            sheet_name: this.state.sheetName || "Data",
            freeze_header: this.state.freezeHeader,
            alternate_rows: this.state.alternateRows,
            show_totals: this.state.showTotals,
            header_bg_color: this.state.headerBgColor,
            header_font_color: this.state.headerFontColor,
            sort_field: this.state.sortField,
            sort_dir: this.state.sortDir,
            export_limit: parseInt(this.state.exportLimit) || 0,
            export_title: this.state.exportTitle || '',
            ...this._extraTemplateVals(),
            is_shared: false,
        }]);
        this.notification.add(`Template "${newName}" created.`, { title: "Duplicated", type: "success" });
        await this.loadTemplates();
    }

    getPreviewStat(fieldName, fieldType) {
        const numericTypes = new Set(['integer', 'float', 'monetary']);
        if (!numericTypes.has(fieldType) || !this.state.previewData.length) return '';
        const vals = this.state.previewData
            .map(r => {
                const v = this.getValue(r, fieldName);
                return parseFloat(String(v).replace(/[^0-9.\-]/g, ''));
            })
            .filter(v => !isNaN(v) && isFinite(v));
        if (!vals.length) return '';
        const sum = vals.reduce((a, b) => a + b, 0);
        const avg = sum / vals.length;
        const fmt = n => Number.isInteger(n) ? n : n.toFixed(2);
        return `Σ ${fmt(sum)}  ⌀ ${fmt(avg)}`;
    }

    async clearAllColumns() {
        for (const f of this.state.selectedFields) {
            if (f.type !== 'computed' && f.name !== '__count') {
                this.state.availableFields.push(f);
            }
        }
        this.state.selectedFields = [];
        this.state.selectedGroupFields = [];
        this.state.showRecordCount = false;
        this.state.previewData = [];
    }

    // ── Generate / export ────────────────────────────────────────────────────

    _buildParams(extra = {}) {
        return new URLSearchParams({
            model: this.props.model,
            fields: JSON.stringify(this.state.selectedFields),
            ids: (this.props.ids || []).join(","),
            domain: this.state.domain || "[]",
            field_type: this.state.fieldType,
            groupby: this.effectiveGroupBy,   // includes :period if date grouping
            filename: this.state.filename,
            sort_field: this.state.sortField,
            sort_dir: this.state.sortDir,
            limit: this.state.exportLimit || "0",
            chart_type: this.state.chartType || "",
            chart_measure: this.state.chartMeasure || "",
            auto_filter: this.state.autoFilter ? "1" : "0",
            ...extra,
        });
    }

    get canExport() {
        return this.state.selectedFields.length > 0;
    }

    async generate() {
        if (!this.canExport) return;
        // A configured schedule must be persisted as a template before export.
        if (this.state.scheduleEnabled && !(await this._persistScheduleTemplate())) return;
        const params = this._buildParams({
            bg_color: this.state.headerBgColor,
            font_color: this.state.headerFontColor,
            sheet_name: this.state.sheetName || "Data",
            freeze_header: this.state.freezeHeader ? '1' : '0',
            alternate_rows: this.state.alternateRows ? '1' : '0',
            show_totals: this.state.showTotals ? '1' : '0',
            export_title: this.state.exportTitle || '',
            add_summary_sheet: this.state.addSummarySheet ? '1' : '0',
        });
        window.location.href = `/web/export/dynamic_xlsx?${params}`;
        this.props.close();
    }

    async generateCsv() {
        if (this.state.selectedFields.length === 0) return;
        if (this.state.scheduleEnabled && !(await this._persistScheduleTemplate())) return;
        window.location.href = `/web/export/dynamic_csv?${this._buildParams()}`;
        this.props.close();
    }
}

// ── ControlPanel patch ───────────────────────────────────────────────────────

patch(ControlPanel.prototype, {
    setup() {
        super.setup();
        this.dialog = useService("dialog");
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.savedTemplates = [];
        this.isExcelReportUser = false;
        this.allowedModels = [];
        onWillStart(async () => {
            try {
                this.isExcelReportUser = await user.hasGroup(
                    "excel_report_builder.group_excel_report_builder_user");
                if (this.isExcelReportUser) {
                    const resModel = this.env.searchModel?.resModel;
                    await Promise.all([
                        this.orm.call("excel.report.builder.config", "get_allowed_models", [])
                            .then(r => { this.allowedModels = r || []; }),
                        resModel ? this._loadExportTemplates(resModel) : Promise.resolve(),
                    ]);
                }
            } catch (e) {
                // Component destroyed during navigation (e.g. access revoked → redirect).
                // Swallow silently; state stays at safe defaults (isExcelReportUser=false).
                if (!e.message?.includes("Component is destroyed")) throw e;
            }
        });
        onWillUpdateProps(async () => {
            try {
                if (!this.env.searchModel) return;
                await this._loadExportTemplates(this.env.searchModel.resModel);
            } catch (e) {
                if (!e.message?.includes("Component is destroyed")) throw e;
            }
        });
    },

    get isNewViewContext() {
        // noBreadcrumbs=true when the view is opened as a dialog/popup (target:'new').
        // Inline x2many lists inside forms use ListRenderer directly — they have no
        // ControlPanel — so this flag is the only reliable "new/dialog view" signal.
        return !!this.env.config?.noBreadcrumbs;
    },

    get isModelAllowed() {
        // Empty list = no restriction, button shows on all models
        if (!this.allowedModels.length) return true;
        const resModel = this.env.searchModel?.resModel;
        return resModel ? this.allowedModels.includes(resModel) : false;
    },

    async _loadExportTemplates(resModel) {
        if (!resModel) return;
        try {
            this.savedTemplates = await this.orm.call(
                "dynamic.export.template", "get_templates", [resModel]
            );
        } catch (err) {
            console.error("Failed to load export templates:", err);
            this.savedTemplates = [];
        }
    },

    onDynamicExport() {
        const resModel = this.env.searchModel?.resModel;
        if (!resModel) return;
        const selectedIds = this.env.searchModel.getSelectedResIds?.() || [];
        const rawDomain = this.env.searchModel.domain || [];
        // A Python literal (True/False/None), not JSON: the server evaluates it.
        const initialDomain = toDomainString(rawDomain);

        // Collect visible list-view columns from the rendered table headers
        let listViewFields = [];
        try {
            const headers = document.querySelectorAll('.o_list_table thead th[data-name]');
            listViewFields = Array.from(headers)
                .map(th => ({
                    name: th.dataset.name,
                    string: (th.querySelector('.o_column_sortable')?.textContent?.trim()
                             || th.textContent?.trim()
                             || th.dataset.name),
                }))
                .filter(f => f.name && f.name !== 'id');
        } catch (_) {}

        this.dialog.add(ExportDialog, { model: resModel, ids: selectedIds, initialDomain, listViewFields });
    },

    async onExecuteTemplateExport(template) {
        try {
            const action = await this.orm.call(
                "dynamic.export.template", "generate_report_from_action", [template.id]
            );
            if (action?.url) this.actionService.doAction(action);
        } catch (err) {
            console.error("Quick export failed:", err);
        }
    },
});
