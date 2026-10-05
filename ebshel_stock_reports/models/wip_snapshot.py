# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
"""Work in progress at a date (report #3) and its month-end snapshots.

The definition is the one Odoo's WIP accounting wizard posts (DESIGN.md §2.3):
components issued = picked raw move lines with a quantity and a date up to the
instant, priced at today's standard price (or, by company setting, at the
product's average cost on that day); overhead = work order time cost up to the
instant.
"""
from collections import defaultdict
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

OPEN_MO_STATES = ('confirmed', 'progress', 'to_close')


class WipSnapshot(models.Model):
    _name = 'asr.wip.snapshot'
    _description = 'WIP Snapshot'
    _order = 'date desc, id desc'

    name = fields.Char(required=True, readonly=True)
    company_id = fields.Many2one('res.company', required=True, readonly=True, index=True,
                                 default=lambda self: self.env.company)
    date = fields.Date(required=True, readonly=True, help="WIP as of the end of this day.")
    instant = fields.Datetime(readonly=True, help="UTC instant of the end of the day in the reporting timezone.")
    kind = fields.Selection([('month_end', 'Month End'), ('manual', 'On Demand')], required=True, readonly=True,
                            default='manual')
    pricing = fields.Selection([('standard', 'Standard Price'), ('avg_cost', 'Average Cost at Date')],
                               readonly=True)
    line_ids = fields.One2many('asr.wip.snapshot.line', 'snapshot_id', readonly=True)
    production_count = fields.Integer(compute='_compute_totals')
    component_value = fields.Monetary(compute='_compute_totals', currency_field='currency_id',
                                      groups='ebshel_stock_reports.group_see_values')
    overhead_value = fields.Monetary(compute='_compute_totals', currency_field='currency_id',
                                     groups='ebshel_stock_reports.group_see_values')
    total_value = fields.Monetary(compute='_compute_totals', currency_field='currency_id',
                                  groups='ebshel_stock_reports.group_see_values')
    currency_id = fields.Many2one(related='company_id.currency_id')

    _unique_month_end = models.UniqueIndex("(company_id, date) WHERE kind = 'month_end'")

    @api.depends('line_ids.value', 'line_ids.line_type')
    def _compute_totals(self):
        for snapshot in self:
            lines = snapshot.line_ids.sudo()
            snapshot.production_count = len(lines.production_id)
            snapshot.component_value = sum(lines.filtered(lambda line: line.line_type == 'component').mapped('value'))
            snapshot.overhead_value = sum(lines.filtered(lambda line: line.line_type == 'overhead').mapped('value'))
            snapshot.total_value = snapshot.component_value + snapshot.overhead_value

    # ------------------------------------------------------------------
    # Computation shared with the WIP report
    # ------------------------------------------------------------------
    @api.model
    def _compute_wip(self, company, day, productions=None, pricing=None):
        """Return a list of line dicts (production, product, line_type, workcenter, quantity, value)
        for the WIP of ``company`` at the end of ``day``.

        ``productions`` defaults to the orders open now; a past date therefore
        needs a snapshot taken at that time (the cron does it at month end).
        """
        company = company.sudo()
        pricing = pricing or company.asr_wip_component_price
        instant = company._asr_day_end_utc(day)
        if productions is None:
            productions = self.env['mrp.production'].sudo().search([
                ('company_id', '=', company.id), ('state', 'in', OPEN_MO_STATES)])
        lines = []
        avg_costs = {}
        if pricing == 'avg_cost':
            product_ids = productions.move_raw_ids.product_id.ids
            closing = self.env['asr.stock.value.daily'].sudo()._closing_at(company, product_ids, day)
            avg_costs = {pid: cost for pid, (_q, _v, cost) in closing.items()}
        for production in productions:
            per_product = defaultdict(lambda: [0.0, 0.0])
            for ml in production.move_raw_ids.move_line_ids:
                if not ml.picked or not ml.quantity or not ml.date or ml.date > instant:
                    continue
                product = ml.product_id
                if pricing == 'avg_cost' and product.id in avg_costs:
                    price = avg_costs[product.id]
                elif product.lot_valuated and ml.lot_id:
                    price = ml.lot_id.standard_price
                else:
                    price = product.with_company(company).standard_price
                per_product[product][0] += ml.quantity_product_uom
                per_product[product][1] += ml.quantity_product_uom * price
            for product, (qty, value) in per_product.items():
                lines.append({
                    'production_id': production.id, 'product_id': production.product_id.id,
                    'component_id': product.id, 'line_type': 'component', 'workcenter_id': False,
                    'quantity': qty, 'value': value,
                })
            for workorder in production.workorder_ids:
                cost = workorder._cal_cost(instant)
                if not cost:
                    continue
                lines.append({
                    'production_id': production.id, 'product_id': production.product_id.id,
                    'component_id': False, 'line_type': 'overhead', 'workcenter_id': workorder.workcenter_id.id,
                    'quantity': 0.0, 'value': cost,
                })
        return lines

    @api.model
    def _take(self, company, day, kind='manual', pricing=None):
        company = company.sudo()
        pricing = pricing or company.asr_wip_component_price
        lines = self._compute_wip(company, day, pricing=pricing)
        snapshot = self.sudo().create({
            'name': self.env._("WIP %(date)s", date=fields.Date.to_string(day)),
            'company_id': company.id, 'date': day, 'instant': company._asr_day_end_utc(day),
            'kind': kind, 'pricing': pricing,
            'line_ids': [(0, 0, dict(line, company_id=company.id)) for line in lines],
        })
        return snapshot

    @api.model
    def _cron_month_end(self):
        """On the first day of a month, snapshot the previous month end once per company."""
        for company in self.env['res.company'].sudo().search([]):  # pylint: disable=no-search-all
            today = company._asr_local_day(fields.Datetime.now())
            if today.day != 1:
                continue
            month_end = today - timedelta(days=1)
            if self.sudo().search_count([('company_id', '=', company.id), ('date', '=', month_end),
                                         ('kind', '=', 'month_end')]):
                continue
            self._take(company, month_end, kind='month_end')
            self.env['ir.cron']._commit_progress(1)

    def action_take_now(self):
        company = self.env.company
        snapshot = self._take(company, company._asr_local_day(fields.Datetime.now()))
        return {
            'type': 'ir.actions.act_window', 'res_model': 'asr.wip.snapshot', 'res_id': snapshot.id,
            'view_mode': 'form', 'target': 'current',
        }

    @api.model
    def _previous_month_end(self, day):
        return day.replace(day=1) - relativedelta(days=1)


class WipSnapshotLine(models.Model):
    _name = 'asr.wip.snapshot.line'
    _description = 'WIP Snapshot Line'
    _order = 'production_id, line_type, id'

    snapshot_id = fields.Many2one('asr.wip.snapshot', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one('res.company', required=True, readonly=True, index=True)
    production_id = fields.Many2one('mrp.production', string='Manufacturing Order', readonly=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', string='Finished Product', readonly=True)
    component_id = fields.Many2one('product.product', readonly=True)
    line_type = fields.Selection([('component', 'Components Issued'), ('overhead', 'Overhead')], readonly=True)
    workcenter_id = fields.Many2one('mrp.workcenter', readonly=True)
    quantity = fields.Float(digits='Product Unit of Measure', readonly=True)
    value = fields.Monetary(currency_field='currency_id', readonly=True, groups='ebshel_stock_reports.group_see_values')
    currency_id = fields.Many2one(related='company_id.currency_id')
