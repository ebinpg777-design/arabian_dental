# -*- coding: utf-8 -*-
"""What the Technician Work screen prints: the board as the screen shows it - its
filters, its rows (by technician, department, product...), its chosen columns, and
each row's breakdown if asked - on the lab letterhead."""
from odoo import _, api, fields, models
from odoo.tools.misc import formatLang

from ..models.technician_board import COLUMNS


class TechnicianWorkSummary(models.TransientModel):
    _name = 'sale.technician.work.summary'
    _description = 'Technician Work Summary'

    date_from = fields.Date(string='From', required=True)
    date_to = fields.Date(string='To', required=True)
    options = fields.Json(help="The screen's filters, as get_board takes them.")
    columns = fields.Json(help="The columns chosen on the screen, in order.")
    with_breakdown = fields.Boolean(string='With Each Row Broken Down')


class TechnicianWorkSummaryReport(models.AbstractModel):
    _name = 'report.lab_technician_work.report_technician_work'
    _description = 'Technician Work Summary renderer'

    @api.model
    def _get_report_values(self, docids, data=None):
        summary = self.env['sale.technician.work.summary'].browse(docids)
        summary.ensure_one()
        Work = self.env['sale.technician.work']
        board = Work.get_board(summary.options or {})
        opts = board['options']
        labels = dict(COLUMNS)
        columns = [(key, self.env._(labels[key])) for key in (summary.columns or []) if key in labels] \
            or [(key, self.env._(labels[key])) for key in board['default_columns']]
        breakdowns = {}
        if summary.with_breakdown:
            for row in board['rows']:
                breakdowns[row['key']] = Work.get_breakdown(summary.options, row['key'])
        currency = self.env.company.currency_id
        choices = board['choices']

        def chosen(key, label):
            ids = set(opts.get(key) or [])
            names = [item['name'] for item in choices.get(key, []) if item['id'] in ids]
            return (label, ', '.join(names)) if names else None

        criteria = [item for item in (
            chosen('technician_ids', _('Technicians')),
            chosen('department_ids', _('Departments')),
            chosen('categ_ids', _('Categories')),
            chosen('team_ids', _('Routes')),
            chosen('partner_ids', _('Clinics')),
        ) if item]
        criteria.append((_('Counted by'), _('delivered date') if opts['date_field'] == 'delivered_date'
                         else _('order date')))
        if opts['rework'] != 'all':
            criteria.append((_('Reworks'), _('left out') if opts['rework'] == 'exclude' else _('only')))
        if opts['cases'] != 'all':
            criteria.append((_('Cases'), _('shared only') if opts['cases'] == 'shared' else _('worked alone')))

        def show(key, value):
            if key in ('value', 'share'):
                return '%s %s' % (currency.symbol or '', formatLang(self.env, value or 0.0,
                                                                    digits=currency.decimal_places))
            if key in ('rework_rate', 'works_pct'):
                return '%s%%' % formatLang(self.env, value or 0.0, digits=1)
            if key == 'trend':
                return ('+%s' % value) if value > 0 else str(value) if value else '0'
            if value is None:
                return ''
            return formatLang(self.env, value, digits=0 if float(value).is_integer() else 1)

        top = max((row['works'] for row in board['rows']), default=0)
        return {
            'doc_ids': summary.ids,
            'doc_model': summary._name,
            'docs': summary,
            'wizard': summary,
            'board': board,
            'columns': columns,
            'breakdowns': breakdowns,
            'criteria': criteria,
            'show': show,
            'top': top,
            'pct': lambda part, whole: (100.0 * part / whole) if whole else 0.0,
            'printed_on': fields.Datetime.context_timestamp(summary, fields.Datetime.now()).strftime('%d-%m-%Y %H:%M'),
        }
