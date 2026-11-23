# -*- coding: utf-8 -*-
"""Technician Work Summary: the printed sheet of a period - per technician, the cases,
the works (order lines), the quantity and the teeth, and the products if asked."""
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.misc import formatLang

from odoo.addons.sale_custom.wizard.order_list_wizard import local_midnight_utc

MEASURES = ('case_count', 'rework_count', 'quantity', 'teeth_count', 'amount', 'amount_share')


class TechnicianWorkSummary(models.TransientModel):
    _name = 'sale.technician.work.summary'
    _description = 'Technician Work Summary'

    def _default_date_from(self):
        return fields.Date.context_today(self).replace(day=1)

    date_from = fields.Date(string='From', required=True, default=_default_date_from)
    date_to = fields.Date(string='To', required=True, default=fields.Date.context_today)
    date_field = fields.Selection(
        [('date', 'Order date'), ('delivered_date', 'Delivered on')],
        string='Count by', required=True, default='date',
        help="Order date: the cases registered in the period. Delivered on: the cases "
             "delivered in the period - the work finished.")
    technician_ids = fields.Many2many('res.partner', string='Technicians',
                                      domain="[('is_technician', '=', True)]")
    department_ids = fields.Many2many('hr.department', string='Departments')
    include_reworks = fields.Boolean(string='Count Reworks', default=True)
    show_products = fields.Boolean(string='Show the Products of Each Technician')
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)

    def _domain(self):
        self.ensure_one()
        if self.date_from > self.date_to:
            raise UserError(_('The start date is after the end date.'))
        # the reader's whole days, not UTC ones
        start = local_midnight_utc(self, self.date_from)
        end = local_midnight_utc(self, self.date_to + timedelta(days=1))
        domain = [(self.date_field, '>=', start), (self.date_field, '<', end),
                  ('company_id', '=', self.company_id.id)]
        if self.technician_ids:
            domain.append(('technician_id', 'in', self.technician_ids.ids))
        if self.department_ids:
            domain.append(('department_id', 'in', self.department_ids.ids))
        if not self.include_reworks:
            domain.append(('is_rework', '=', False))
        return domain

    def _rows(self):
        """[{technician, cases, works, quantity, teeth, value, share, products: [...]}],
        the busiest first."""
        self.ensure_one()
        Work = self.env['sale.technician.work']
        aggregates = ['__count'] + ['%s:sum' % measure for measure in MEASURES]

        def values(result):
            count, cases, reworks, quantity, teeth, value, share = result
            return {'works': count, 'cases': cases or 0, 'reworks': reworks or 0,
                    'quantity': quantity or 0.0, 'teeth': teeth or 0,
                    'value': value or 0.0, 'share': share or 0.0}

        rows = {}
        for technician, *result in Work._read_group(self._domain(), ['technician_id'], aggregates):
            rows[technician.id] = dict(values(result), technician=technician, products=[])
        if self.show_products and rows:
            for technician, product, *result in Work._read_group(
                    self._domain(), ['technician_id', 'product_id'], aggregates, order='__count desc'):
                rows[technician.id]['products'].append(dict(values(result), product=product))
        return sorted(rows.values(), key=lambda row: (-row['works'], row['technician'].name or ''))

    def action_print(self):
        self.ensure_one()
        return self.env.ref('lab_technician_work.action_report_technician_work').report_action(self)

    def action_open_analysis(self):
        """The same period, as a pivot to drill into."""
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('lab_technician_work.action_technician_work')
        action['domain'] = self._domain()
        action['context'] = {}
        return action


class TechnicianWorkSummaryReport(models.AbstractModel):
    _name = 'report.lab_technician_work.report_technician_work'
    _description = 'Technician Work Summary renderer'

    @api.model
    def _get_report_values(self, docids, data=None):
        wizard = self.env['sale.technician.work.summary'].browse(docids)
        wizard.ensure_one()
        currency = wizard.company_id.currency_id
        rows = wizard._rows()
        total = {key: sum(row[key] for row in rows)
                 for key in ('works', 'cases', 'reworks', 'quantity', 'teeth', 'value', 'share')}
        criteria = []
        if wizard.technician_ids:
            criteria.append((_('Technicians'), ', '.join(wizard.technician_ids.mapped('name'))))
        if wizard.department_ids:
            criteria.append((_('Departments'), ', '.join(wizard.department_ids.mapped('name'))))
        criteria.append((_('Counted by'), dict(wizard._fields['date_field']._description_selection(
            self.env))[wizard.date_field]))
        if not wizard.include_reworks:
            criteria.append((_('Reworks'), _('not counted')))
        top = max((row['works'] for row in rows), default=0)
        return {
            'doc_ids': wizard.ids,
            'doc_model': wizard._name,
            'docs': wizard,
            'wizard': wizard,
            'rows': rows,
            'total': total,
            'top': top,
            'criteria': criteria,
            'printed_on': fields.Datetime.context_timestamp(wizard, fields.Datetime.now()).strftime('%d-%m-%Y %H:%M'),
            'money': lambda amount: '%s %s' % (currency.symbol or '', formatLang(
                self.env, amount or 0.0, digits=currency.decimal_places)),
            'number': lambda value: formatLang(self.env, value or 0.0, digits=0 if float(value or 0).is_integer() else 2),
            'pct': lambda part, whole: (100.0 * part / whole) if whole else 0.0,
        }
