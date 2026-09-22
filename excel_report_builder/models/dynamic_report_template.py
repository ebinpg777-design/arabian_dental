# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebin P G
# License OPL-1. See LICENSE file for full copyright and licensing details.
import base64
import json
import logging
import urllib.parse
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import models, fields, api, _
from odoo.exceptions import AccessError, ValidationError

from .report_dynamic_xlsx_export import build_workbook, parse_domain, _sanitize_filename

_logger = logging.getLogger(__name__)


class DynamicExportTemplate(models.Model):
    _name = 'dynamic.export.template'
    _description = 'Dynamic Export Template'
    _order = 'name'

    name = fields.Char(string="Template Name", required=True)
    res_model = fields.Char(string="Model", required=True, index=True)
    domain = fields.Text(string="Domain", default="[]")
    fields_json = fields.Text(string="Fields JSON", required=True)
    user_id = fields.Many2one('res.users', string="Created By", default=lambda self: self.env.user)
    field_type = fields.Selection([
        ('standard', 'Standard'),
        ('group', 'Grouped'),
    ], string="Report Type", default='standard')
    groupby = fields.Char(string="Group By Field")
    description = fields.Text(string="Description")
    is_shared = fields.Boolean(
        string="Shared", default=False,
        help="When enabled, this template is visible to all users.",
    )
    # Export presentation
    filename = fields.Char(string="Export Filename", help="Custom filename without extension.")
    sheet_name = fields.Char(string="Sheet Name", default="Data")
    freeze_header = fields.Boolean(string="Freeze Header Row", default=True)
    alternate_rows = fields.Boolean(string="Alternate Row Colors", default=False)
    show_totals = fields.Boolean(string="Show Totals Row", default=False)
    auto_filter = fields.Boolean(string="Enable AutoFilter", default=False)
    header_bg_color = fields.Char(string="Header Background Color", default="#e3e2de")
    header_font_color = fields.Char(string="Header Font Color", default="#000000")
    # Data options
    sort_field = fields.Char(string="Sort By Field")
    sort_dir = fields.Selection([('asc', 'Ascending'), ('desc', 'Descending')], default='asc')
    export_limit = fields.Integer(string="Max Rows (0 = all)", default=0)
    export_title = fields.Char(string="Report Title", help="Merged title row written above the column headers.")
    # Charting
    chart_type = fields.Selection([
        ('', 'None'),
        ('column', 'Column'),
        ('bar', 'Bar'),
        ('line', 'Line'),
        ('area', 'Area'),
        ('pie', 'Pie'),
    ], string="Chart Type", default='')
    chart_measure = fields.Char(string="Chart Measure Field")
    # ── Scheduled delivery ───────────────────────────────────────────────────
    schedule_enabled = fields.Boolean(string="Scheduled Delivery", default=False)
    schedule_interval = fields.Selection([
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
    ], string="Frequency", default='weekly')
    schedule_email_to = fields.Char(
        string="Recipients",
        help="Comma-separated email addresses to deliver the report to.",
    )
    schedule_next = fields.Datetime(string="Next Run")
    schedule_last_sent = fields.Datetime(string="Last Sent", readonly=True)

    _TEMPLATE_FIELDS = [
        'id', 'name', 'fields_json', 'domain', 'field_type', 'groupby',
        'filename', 'sheet_name', 'freeze_header', 'alternate_rows', 'show_totals',
        'is_shared', 'header_bg_color', 'header_font_color', 'description', 'user_id',
        'sort_field', 'sort_dir', 'export_limit', 'export_title',
        'auto_filter', 'chart_type', 'chart_measure',
        'schedule_enabled', 'schedule_interval', 'schedule_email_to', 'schedule_next',
    ]

    @api.model
    def get_templates(self, res_model):
        domain = [
            ('res_model', '=', res_model),
            '|',
            ('user_id', '=', self.env.uid),
            ('is_shared', '=', True),
        ]
        return self.search_read(domain, self._TEMPLATE_FIELDS)

    @api.model
    def delete_template(self, template_id):
        template = self.browse(template_id)
        if not template.exists():
            return False
        if template.user_id.id == self.env.uid or self.env.user.has_group('base.group_system'):
            template.unlink()
            return True
        return False

    # ── Schedule housekeeping ──────────────────────────────────────────────────

    @api.constrains('schedule_enabled', 'schedule_email_to')
    def _check_schedule_recipients(self):
        for rec in self:
            if rec.schedule_enabled and not (rec.schedule_email_to or '').strip():
                raise ValidationError(_(
                    "Scheduled Email Delivery is enabled for '%s' but no recipients "
                    "are set. Add at least one recipient email."
                ) % rec.name)

    def _compute_next_run(self, base=None):
        """Return the next scheduled datetime from *base* (defaults to now)."""
        self.ensure_one()
        base = base or fields.Datetime.now()
        if self.schedule_interval == 'daily':
            return base + timedelta(days=1)
        if self.schedule_interval == 'monthly':
            return base + relativedelta(months=1)
        return base + timedelta(weeks=1)  # weekly default

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if rec.schedule_enabled and not rec.schedule_next:
                rec.schedule_next = rec._compute_next_run()
        return records

    def _is_template_admin(self):
        return self.env.su or self.env.user.has_group('base.group_system')

    def write(self, vals):
        # Record rules only check a template *before* it is written, so without
        # this a user could hand their template to somebody else - and the
        # scheduled delivery builds the export with its owner's rights.
        if 'user_id' in vals and vals['user_id'] != self.env.uid and not self._is_template_admin():
            raise AccessError(_("Only an administrator can change the owner of a report template."))
        res = super().write(vals)
        if 'schedule_enabled' in vals or 'schedule_interval' in vals:
            for rec in self:
                if rec.schedule_enabled and not rec.schedule_next:
                    rec.schedule_next = rec._compute_next_run()
        return res

    # ── Report parameter assembly ──────────────────────────────────────────────

    def _build_params(self):
        """Assemble the dict consumed by build_workbook() from this template."""
        self.ensure_one()
        try:
            fields_data = json.loads(self.fields_json) if self.fields_json else []
        except (json.JSONDecodeError, TypeError):
            fields_data = []
        final_domain = parse_domain(self.env, self.domain)
        return {
            'model': self.res_model,
            'field_list': fields_data,
            'field_type': self.field_type or 'standard',
            'groupby': self.groupby or '',
            'domain': final_domain,
            'sort_field': self.sort_field or '',
            'sort_dir': self.sort_dir or 'asc',
            'limit': self.export_limit or 0,
            'bg_color': self.header_bg_color or '#e3e2de',
            'font_color': self.header_font_color or '#000000',
            'export_title': self.export_title or '',
            'sheet_name': self.sheet_name or 'Data',
            'freeze_header': self.freeze_header,
            'alternate_rows': self.alternate_rows,
            'show_totals': self.show_totals,
            'auto_filter': self.auto_filter,
            'add_summary_sheet': False,
            'chart_type': self.chart_type or '',
            'chart_measure': self.chart_measure or '',
        }

    def _generate_xlsx_bytes(self):
        """Build the XLSX for this template server-side and return raw bytes."""
        self.ensure_one()
        return build_workbook(self.env, self._build_params())

    @api.model
    def generate_report_from_action(self, template_id):
        template = self.browse(template_id)
        if not template.exists():
            return False
        fields_data = json.loads(template.fields_json) if template.fields_json else []
        # Validate here so a broken filter is reported instead of exporting
        # everything; the controller applies it with the user's own rights.
        parse_domain(self.env, template.domain)
        return template.generate_report(
            res_model=template.res_model,
            fields_list=fields_data,
            ids_list=[],
            domain=template.domain or "[]",
            bg_color=template.header_bg_color or "#e3e2de",
            font_color=template.header_font_color or "#000000",
            field_type=template.field_type or 'standard',
            groupby=template.groupby or '',
            filename=template.filename or '',
            sheet_name=template.sheet_name or 'Data',
            freeze_header=template.freeze_header,
            alternate_rows=template.alternate_rows,
            show_totals=template.show_totals,
            sort_field=template.sort_field or '',
            sort_dir=template.sort_dir or 'asc',
            export_limit=template.export_limit or 0,
            export_title=template.export_title or '',
            auto_filter=template.auto_filter,
            chart_type=template.chart_type or '',
            chart_measure=template.chart_measure or '',
        )

    @api.model
    def generate_report(self, res_model, fields_list, ids_list, domain,
                        bg_color="#e3e2de", font_color="#000000", field_type='standard',
                        groupby='', filename='', sheet_name='Data', freeze_header=True,
                        alternate_rows=False, show_totals=False,
                        sort_field='', sort_dir='asc', export_limit=0, export_title='',
                        auto_filter=False, chart_type='', chart_measure=''):
        params = urllib.parse.urlencode({
            'model': res_model,
            'fields': json.dumps(fields_list),
            'ids': ",".join(map(str, ids_list)),
            'domain': domain,
            'bg_color': bg_color,
            'font_color': font_color,
            'field_type': str(field_type),
            'groupby': str(groupby),
            'filename': str(filename),
            'sheet_name': str(sheet_name or 'Data'),
            'freeze_header': '1' if freeze_header else '0',
            'alternate_rows': '1' if alternate_rows else '0',
            'show_totals': '1' if show_totals else '0',
            'sort_field': str(sort_field or ''),
            'sort_dir': str(sort_dir or 'asc'),
            'limit': str(export_limit or 0),
            'export_title': str(export_title or ''),
            'auto_filter': '1' if auto_filter else '0',
            'chart_type': str(chart_type or ''),
            'chart_measure': str(chart_measure or ''),
        })
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/export/dynamic_xlsx?{params}',
            'target': 'new',
        }

    # ── Scheduled email delivery ───────────────────────────────────────────────

    def _deliver_report_email(self):
        """Generate the XLSX and email it to the configured recipients.

        The workbook is always built with the rights of the template's owner -
        never those of the cron (superuser) or of whoever pressed "Send Now" -
        so a scheduled report can only contain what its owner may read.
        """
        self.ensure_one()
        recipients = (self.schedule_email_to or '').strip()
        if not recipients:
            _logger.warning("Template %s scheduled but has no recipients.", self.name)
            return False
        owner = self.sudo().user_id
        if not owner:
            _logger.warning("Template %s scheduled but has no owner; not delivered.", self.name)
            return False
        data = self.with_user(owner)._generate_xlsx_bytes()
        safe_name = _sanitize_filename(self.filename, f"report_{self.res_model}")
        attachment = self.env['ir.attachment'].sudo().create({
            'name': f"{safe_name}.xlsx",
            'datas': base64.b64encode(data),
            'type': 'binary',
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })
        body = _(
            "<p>Hello,</p>"
            "<p>Please find attached the scheduled report "
            "<strong>%(name)s</strong> generated on %(date)s.</p>"
            "<p style='color:#888;font-size:12px'>Sent automatically by the "
            "Excel Report Builder.</p>"
        ) % {'name': self.name, 'date': fields.Datetime.now()}
        mail = self.env['mail.mail'].sudo().create({
            'subject': _("Scheduled Report: %s") % self.name,
            'body_html': body,
            'email_to': recipients,
            'attachment_ids': [(6, 0, [attachment.id])],
            'auto_delete': True,
        })
        mail.send()
        self.schedule_last_sent = fields.Datetime.now()
        return True

    def action_send_now(self):
        """Manual 'Send Now' button on the template form."""
        for rec in self:
            rec._deliver_report_email()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'title': _("Report Sent"),
                'message': _("Scheduled report(s) delivered."),
                'sticky': False,
            },
        }

    @api.model
    def _cron_send_scheduled(self):
        """Cron entry point: deliver every template whose schedule is due."""
        now = fields.Datetime.now()
        due = self.search([
            ('schedule_enabled', '=', True),
            ('schedule_next', '!=', False),
            ('schedule_next', '<=', now),
        ])
        for tpl in due:
            try:
                tpl._deliver_report_email()
            except Exception as e:
                _logger.exception("Scheduled report '%s' failed: %s", tpl.name, e)
            finally:
                tpl.schedule_next = tpl._compute_next_run(base=now)
        return True
