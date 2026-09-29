# -*- coding: utf-8 -*-
"""Around a report: the notes on its lines, the views people save, and the
schedules that put it in an inbox."""
import base64
import logging
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo.modules import module as odoo_module
from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class FinReportAnnotation(models.Model):
    _name = 'ebshel.fin.report.annotation'
    _description = 'Report note'
    _order = 'create_date desc'

    report_id = fields.Many2one('ebshel.fin.report', required=True, ondelete='cascade', index=True)
    line_key = fields.Char(required=True, index=True, help="The stable id of the line the note sits on.")
    line_name = fields.Char()
    column_key = fields.Char(help="Empty: the whole line.")
    text = fields.Text(required=True)
    user_id = fields.Many2one('res.users', default=lambda self: self.env.user, required=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    date = fields.Date(default=fields.Date.context_today, required=True)

    @api.model
    def for_report(self, report):
        out = {}
        for note in self.search([('report_id', '=', report.id),
                                 ('company_id', 'in', self.env.companies.ids)]):
            out.setdefault(note.line_key, []).append({
                'id': note.id, 'text': note.text, 'column_key': note.column_key or '',
                'user': note.user_id.name, 'date': fields.Date.to_string(note.date),
                'line_name': note.line_name or '',
                'mine': note.user_id == self.env.user,
            })
        return out

    @api.model
    def add_note(self, report_id, line_key, text, line_name='', column_key=''):
        if not self.env.user.has_group('account.group_account_invoice'):
            raise UserError(_("Only the accounting team can annotate a report."))
        return self.create({'report_id': int(report_id), 'line_key': line_key, 'text': text,
                            'line_name': line_name, 'column_key': column_key or False}).id


class FinReportView(models.Model):
    _name = 'ebshel.fin.report.view'
    _description = 'Saved report view'
    _order = 'name'

    name = fields.Char(required=True)
    report_id = fields.Many2one('ebshel.fin.report', required=True, ondelete='cascade', index=True)
    options = fields.Json(required=True)
    user_id = fields.Many2one('res.users', default=lambda self: self.env.user, required=True)
    shared = fields.Boolean(help="Everyone on the accounting team can open it.")
    is_default = fields.Boolean(string='Open by default', help="Your default for this report.")

    @api.model
    def for_report(self, report):
        return [{'id': v.id, 'name': v.name, 'shared': v.shared, 'mine': v.user_id == self.env.user,
                 'is_default': v.is_default and v.user_id == self.env.user, 'options': v.options}
                for v in self.search([('report_id', '=', report.id)])]

    @api.model
    def save_view(self, report_id, name, options, shared=False, is_default=False):
        options = dict(options or {})
        options.pop('expanded', None)
        view = self.create({'report_id': int(report_id), 'name': name, 'options': options,
                            'shared': bool(shared), 'is_default': bool(is_default)})
        if is_default:
            self.search([('report_id', '=', view.report_id.id), ('user_id', '=', self.env.uid),
                         ('id', '!=', view.id)]).write({'is_default': False})
        return view.id

    def action_open(self):
        self.ensure_one()
        return {'type': 'ir.actions.client', 'tag': 'ebshel_fin_report', 'name': self.report_id.name,
                'params': {'report_key': self.report_id.key, 'options': self.options}}


class FinReportSchedule(models.Model):
    _name = 'ebshel.fin.report.schedule'
    _description = 'Report schedule'
    _order = 'next_run, id'

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    report_id = fields.Many2one('ebshel.fin.report', required=True, ondelete='cascade')
    view_id = fields.Many2one('ebshel.fin.report.view', string='Saved view',
                              domain="[('report_id', '=', report_id)]",
                              help="The filters to run with. Empty: the report's defaults, "
                                   "which follow the calendar (this month, this quarter…).")
    format = fields.Selection([('xlsx', 'Excel'), ('pdf', 'PDF'), ('both', 'Excel and PDF')],
                              default='both', required=True)
    interval = fields.Selection([('daily', 'Every day'), ('weekly', 'Every week'),
                                 ('monthly', 'Every month'), ('quarterly', 'Every quarter')],
                                default='monthly', required=True)
    run_day = fields.Integer(string='Day', default=1,
                             help="Weekly: 1 = Monday … 7 = Sunday. Monthly and quarterly: day of the month.")
    next_run = fields.Date(required=True, default=fields.Date.context_today)
    last_run = fields.Datetime(readonly=True)
    partner_ids = fields.Many2many('res.partner', string='Recipients',
                                   domain="[('email', '!=', False)]")
    emails = fields.Char(string='Other emails', help="Comma separated.")
    subject = fields.Char(default=lambda self: _('{report} — {period}'))
    body = fields.Html(sanitize=True, default=lambda self: _(
        '<p>Please find attached the <b>{report}</b> for {period}.</p>'))
    user_id = fields.Many2one('res.users', string='Run as', default=lambda self: self.env.user,
                              required=True, help="The report is computed with this person's access.")
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    run_count = fields.Integer(readonly=True)

    def _period_text(self, options):
        return '%s → %s' % (options['date']['from'], options['date']['to'])

    def _recipients(self):
        self.ensure_one()
        emails = [p.email for p in self.partner_ids if p.email]
        emails += [e.strip() for e in (self.emails or '').split(',') if e.strip()]
        return emails

    def _attachments(self):
        """[(name, base64)] for the formats chosen."""
        self.ensure_one()
        report = self.report_id.with_user(self.user_id).with_company(self.company_id)
        options = dict(self.view_id.options or {}) if self.view_id else {}
        options.pop('expanded', None)
        out, period = [], ''
        if self.format in ('xlsx', 'both'):
            xlsx = report.export_xlsx(options)
            out.append((xlsx['filename'], xlsx['content']))
        if self.format in ('pdf', 'both'):
            pdf, _kind = self.env['ir.actions.report'].with_user(self.user_id).with_company(self.company_id)\
                ._render_qweb_pdf('ebshel_account_reports.action_fin_report_pdf', res_ids=[report.id],
                                  data={'options': options, 'report_id': report.id,
                                        # paper is for reading: the view as it was saved; the workbook carries every line
                                        'layout': {'scope': 'screen'}})
            out.append(('%s.pdf' % report.name, base64.b64encode(pdf).decode()))
        norm = self.env['ebshel.fin.engine'].with_user(self.user_id).normalize(report, options)
        period = self._period_text(norm)
        return out, period

    def action_send_now(self):
        for schedule in self:
            schedule._send()
        return True

    def _send(self):
        self.ensure_one()
        recipients = self._recipients()
        if not recipients:
            raise UserError(_("'%s' has nobody to send to.", self.name))
        attachments, period = self._attachments()
        values = {'report': self.report_id.name, 'period': period}
        subject = (self.subject or '{report} — {period}').format(**values)
        body = (self.body or '').format(**values)
        att_ids = self.env['ir.attachment'].sudo().create([{
            'name': name, 'datas': content, 'res_model': self._name, 'res_id': self.id,
        } for name, content in attachments]).ids
        self.env['mail.mail'].sudo().create({
            'subject': subject, 'body_html': body, 'email_to': ', '.join(recipients),
            'attachment_ids': [(6, 0, att_ids)],
            'author_id': self.user_id.partner_id.id,
        }).send()
        self.write({'last_run': fields.Datetime.now(), 'run_count': self.run_count + 1})

    def _advance(self):
        self.ensure_one()
        today = fields.Date.context_today(self)
        base = max(self.next_run, today)
        if self.interval == 'daily':
            nxt = base + timedelta(days=1)
        elif self.interval == 'weekly':
            nxt = base + timedelta(days=1)
            while nxt.isoweekday() != max(1, min(7, self.run_day or 1)):
                nxt += timedelta(days=1)
        else:
            months = 1 if self.interval == 'monthly' else 3
            first = (base.replace(day=1) + relativedelta(months=months))
            day = max(1, min(28, self.run_day or 1))
            nxt = first.replace(day=day)
        self.next_run = nxt

    @api.model
    def _cron_run(self):
        today = fields.Date.context_today(self)
        for schedule in self.search([('next_run', '<=', today)]):
            try:
                schedule._send()
            except Exception:                                          # noqa: BLE001
                _logger.exception("report schedule %s failed", schedule.name)
            schedule._advance()
            if not odoo_module.current_test:           # one schedule per transaction, never inside a test
                self.env.cr.commit()
