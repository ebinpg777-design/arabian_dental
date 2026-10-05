# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
"""Report #3 - Work in progress at a date.

Today: computed live by ``asr.wip.snapshot._compute_wip`` (components issued =
picked raw move lines up to the end of the day, overhead = work order time
cost, DESIGN.md §2.3). A past date reads the latest snapshot taken at or before
that date (the month-end cron or an on-demand snapshot), because the open
orders of a past day cannot be rebuilt from today's data.
"""
from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_date

from .report_mixin import col

MO_STATES = [
    ('draft', 'Draft'), ('confirmed', 'Confirmed'), ('progress', 'In Progress'),
    ('to_close', 'To Close'), ('done', 'Done'), ('cancel', 'Cancelled'),
]


class ReportWip(models.TransientModel):
    _name = 'asr.report.wip'
    _inherit = 'asr.report.mixin'
    _description = 'Work in Progress'
    _asr_title = 'Work in Progress'
    _asr_line_model = 'asr.report.wip.line'

    date = fields.Date('As Of', required=True, default=fields.Date.context_today)
    pricing = fields.Selection([
        ('standard', "Today's standard price (as Odoo's WIP entry posts it)"),
        ('avg_cost', "Average cost of the product on the report date"),
    ], string='Component Pricing', required=True, default=lambda self: self.env.company.asr_wip_component_price)
    is_live = fields.Boolean(compute='_compute_snapshot')
    snapshot_id = fields.Many2one('asr.wip.snapshot', compute='_compute_snapshot')
    snapshot_warning = fields.Char(compute='_compute_snapshot')
    line_ids = fields.One2many('asr.report.wip.line', 'wizard_id')

    @api.depends('date', 'company_id')
    def _compute_snapshot(self):
        Snapshot = self.env['asr.wip.snapshot'].sudo()
        for wizard in self:
            company = wizard.company_id.sudo()
            today = company._asr_local_day(fields.Datetime.now())
            wizard.is_live = not wizard.date or wizard.date >= today
            wizard.snapshot_id = False
            wizard.snapshot_warning = False
            if wizard.is_live:
                continue
            snapshot = Snapshot.search([('company_id', '=', company.id), ('date', '=', wizard.date),
                                        ('kind', '=', 'month_end')], limit=1)
            if not snapshot:
                snapshot = Snapshot.search([('company_id', '=', company.id), ('date', '<=', wizard.date)],
                                           order='date desc, id desc', limit=1)
            wizard.snapshot_id = snapshot
            if not snapshot:
                wizard.snapshot_warning = self.env._(
                    "No WIP snapshot exists at or before %s: a past date can only be read from a snapshot "
                    "(month-end cron or \"Take snapshot now\").", format_date(self.env, wizard.date))
            elif snapshot.date != wizard.date:
                wizard.snapshot_warning = self.env._(
                    "No snapshot was taken on %(date)s; showing the closest earlier snapshot, %(name)s "
                    "of %(snapshot_date)s.", date=format_date(self.env, wizard.date), name=snapshot.name,
                    snapshot_date=format_date(self.env, snapshot.date))
            elif snapshot.pricing and snapshot.pricing != wizard.pricing:
                wizard.snapshot_warning = self.env._(
                    "The snapshot of %(date)s was priced with the \"%(rule)s\" rule; the pricing choice does "
                    "not apply to it.",
                    date=format_date(self.env, snapshot.date),
                    rule=dict(Snapshot._fields['pricing']._description_selection(self.env)).get(snapshot.pricing))

    # ------------------------------------------------------------------
    # Columns
    # ------------------------------------------------------------------
    def _asr_columns(self):
        return [
            col('production_id', 'Manufacturing Order', 'many2one', width=18),
            col('product_id', 'Finished Product', 'many2one', width=30),
            col('state', 'MO State', width=12),
            col('line_type', 'Type', width=16),
            col('component_id', 'Component', 'many2one', width=30),
            col('workcenter_id', 'Work Center', 'many2one', width=18),
            col('uom_id', 'Unit', 'many2one', width=8),
            col('quantity', 'Quantity', 'qty', total=True),
            col('value', 'Value', 'monetary', value=True, total=True),
        ]

    def _asr_filter_text(self):
        parts = [self.env._("As of %s", format_date(self.env, self.date))]
        if self.is_live:
            parts.append(self.env._("Live, components at %s",
                                    dict(self._fields['pricing']._description_selection(self.env))[self.pricing]))
        elif self.snapshot_id:
            parts.append(self.env._("Snapshot %s", self.snapshot_id.name))
        if self.warehouse_ids:
            parts.append(self.env._("Warehouses: %s", ', '.join(self.warehouse_ids.mapped('name'))))
        if self.categ_ids:
            parts.append(self.env._("Categories: %s", ', '.join(self.categ_ids.mapped('complete_name'))))
        if self.product_ids:
            parts.append(self.env._("Products: %s", ', '.join(self.product_ids[:5].mapped('display_name'))
                                    + (' …' if len(self.product_ids) > 5 else '')))
        return ' | '.join(parts)

    def _asr_xlsx_filename(self):
        return f"Work_in_Progress_{fields.Date.to_string(self.date)}.xlsx"

    # ------------------------------------------------------------------
    # Rows
    # ------------------------------------------------------------------
    def _asr_check(self):
        res = super()._asr_check()
        if not self.is_live and not self.snapshot_id:
            raise UserError(self.snapshot_warning or self.env._(
                "A past WIP date needs a snapshot taken at that time."))
        return res

    def _wip_lines(self):
        """Line dicts with ids (production_id, product_id, component_id, line_type, workcenter_id,
        quantity, value), live or from the snapshot."""
        company = self.company_id.sudo()
        if self.is_live:
            return self.env['asr.wip.snapshot']._compute_wip(company, self.date, pricing=self.pricing)
        lines = self.snapshot_id.line_ids.sudo()
        lines.fetch(['production_id', 'product_id', 'component_id', 'line_type', 'workcenter_id', 'quantity', 'value'])
        return [{
            'production_id': line.production_id.id, 'product_id': line.product_id.id,
            'component_id': line.component_id.id, 'line_type': line.line_type,
            'workcenter_id': line.workcenter_id.id, 'quantity': line.quantity, 'value': line.value,
        } for line in lines]

    def _production_filter(self, productions):
        if self.warehouse_ids:
            productions = productions.filtered(lambda p: p.picking_type_id.warehouse_id in self.warehouse_ids)
        if self.product_ids:
            productions = productions.filtered(lambda p: p.product_id in self.product_ids)
        if self.categ_ids:
            categs = self.env['product.category'].search([('id', 'child_of', self.categ_ids.ids)])
            productions = productions.filtered(lambda p: p.product_id.categ_id in categs)
        return productions

    def _asr_rows(self):
        self.ensure_one()
        lines = self._wip_lines()
        if not lines:
            return []
        Production = self.env['mrp.production'].sudo()
        productions = Production.browse(sorted({line['production_id'] for line in lines if line['production_id']}))
        productions.fetch(['name', 'product_id', 'state', 'picking_type_id', 'date_start'])
        productions = self._production_filter(productions)
        if not productions:
            return []
        keep = set(productions.ids)
        Product = self.env['product.product'].with_context(active_test=False)
        components = Product.browse(sorted({line['component_id'] for line in lines if line['component_id']}))
        components.fetch(['display_name', 'uom_id'])
        component_by_id = {p.id: p for p in components}
        Workcenter = self.env['mrp.workcenter']
        workcenters = {w.id: w for w in Workcenter.browse(
            sorted({line['workcenter_id'] for line in lines if line['workcenter_id']}))}
        state_labels = dict(MO_STATES)
        type_labels = {'component': self.env._("Components Issued"), 'overhead': self.env._("Overhead")}
        by_production = {}
        for line in lines:
            if line['production_id'] in keep:
                by_production.setdefault(line['production_id'], []).append(line)
        rows = []
        for production in productions.sorted(key=lambda p: (p.date_start or fields.Datetime.now(), p.id)):
            group = by_production.get(production.id)
            if not group:
                continue
            rows.append({'_group': f"{production.name} - {production.product_id.display_name}"})
            group.sort(key=lambda ln: (ln['line_type'] != 'component', ln['component_id'] or 0,
                                       ln['workcenter_id'] or 0))
            for line in group:
                component = component_by_id.get(line['component_id'])
                rows.append({
                    'production_id': production,
                    'product_id': production.product_id,
                    'state': state_labels.get(production.state, production.state),
                    'line_type': type_labels.get(line['line_type'], line['line_type']),
                    'component_id': component,
                    'workcenter_id': workcenters.get(line['workcenter_id']),
                    'uom_id': component.uom_id if component else False,
                    'quantity': line['quantity'],
                    'value': line['value'],
                })
        return rows

    # ------------------------------------------------------------------
    # Snapshot button
    # ------------------------------------------------------------------
    def action_take_snapshot(self):
        self.ensure_one()
        company = self.company_id.sudo()
        today = company._asr_local_day(fields.Datetime.now())
        snapshot = self.env['asr.wip.snapshot']._take(company, today, pricing=self.pricing)
        return {
            'type': 'ir.actions.act_window', 'res_model': 'asr.wip.snapshot', 'res_id': snapshot.id,
            'view_mode': 'form', 'target': 'current',
        }


class ReportWipLine(models.TransientModel):
    _name = 'asr.report.wip.line'
    _description = 'Work in Progress Line'
    _order = 'production_id, line_type, id'

    wizard_id = fields.Many2one('asr.report.wip', required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='wizard_id.currency_id')
    production_id = fields.Many2one('mrp.production', 'Manufacturing Order', readonly=True)
    product_id = fields.Many2one('product.product', 'Finished Product', readonly=True)
    state = fields.Char('MO State', readonly=True)
    line_type = fields.Char('Type', readonly=True)
    component_id = fields.Many2one('product.product', readonly=True)
    workcenter_id = fields.Many2one('mrp.workcenter', 'Work Center', readonly=True)
    uom_id = fields.Many2one('uom.uom', 'Unit', readonly=True)
    quantity = fields.Float(digits='Product Unit of Measure', readonly=True)
    value = fields.Monetary(currency_field='currency_id', readonly=True, groups='ebshel_stock_reports.group_see_values')
