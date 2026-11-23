# -*- coding: utf-8 -*-
"""Technician Work: one row per technician and order line.

The lab names the technicians who worked a case on the order (sale.order.technician_ids:
most cases name two to four, one for each stage of the work), not on its lines. So
each line of an order counts once for every technician named on it - the count the
client asked for: "a report of technicians' work based on the SO line, to check the
count of work done by a technician" (2026-11-23).

* Cases: the order, counted once per technician (on its first line).
* Works: the order lines (the row count).
* Quantity: the line's quantity; Teeth: the teeth written on the line (FDI numbers).
* Value: the line's amount before tax; Value Share: that amount divided between the
  technicians of the case, so the shares of a case add up to it once.

Cancelled orders and section or note lines are left out; reworks are kept, and marked.
"""
from odoo import api, fields, models, tools
from odoo.tools import SQL


class SaleTechnicianWork(models.Model):
    _name = 'sale.technician.work'
    _description = 'Technician Work'
    _auto = False
    _order = 'date desc, order_id desc, order_line_id'
    _rec_name = 'order_line_id'

    technician_id = fields.Many2one('res.partner', string='Technician', readonly=True)
    order_id = fields.Many2one('sale.order', string='Order', readonly=True)
    order_line_id = fields.Many2one('sale.order.line', string='Order Line', readonly=True)
    date = fields.Datetime(string='Order Date', readonly=True)
    delivered_date = fields.Datetime(string='Delivered On', readonly=True)
    partner_id = fields.Many2one('res.partner', string='Clinic', readonly=True)
    team_id = fields.Many2one('crm.team', string='Sales Route', readonly=True)
    user_id = fields.Many2one('res.users', string='Salesperson', readonly=True)
    product_id = fields.Many2one('product.product', string='Product', readonly=True)
    categ_id = fields.Many2one('product.category', string='Product Category', readonly=True)
    department_id = fields.Many2one('hr.department', string='Department', readonly=True)
    teeth = fields.Char(readonly=True)
    teeth_count = fields.Integer(string='Teeth', readonly=True, aggregator='sum')
    quantity = fields.Float(string='Quantity', readonly=True, aggregator='sum', digits='Product Unit')
    amount = fields.Monetary(string='Value', readonly=True, aggregator='sum', currency_field='currency_id',
                             help="The line's amount before tax.")
    amount_share = fields.Monetary(
        string='Value Share', readonly=True, aggregator='sum', currency_field='currency_id',
        help="The line's amount divided between the technicians of the case.")
    work_count = fields.Integer(string='Works', readonly=True, aggregator='sum',
                                help="Order lines: each line counted once per technician.")
    case_count = fields.Integer(string='Cases', readonly=True, aggregator='sum',
                                help="Orders: each case counted once per technician.")
    rework_count = fields.Integer(string='Rework Cases', readonly=True, aggregator='sum')
    technician_count = fields.Integer(string='Technicians on the Case', readonly=True, aggregator='max')
    is_rework = fields.Boolean(string='Rework', readonly=True)
    state = fields.Selection(selection='_state_selection', string='Order Status', readonly=True)
    company_id = fields.Many2one('res.company', readonly=True)
    currency_id = fields.Many2one('res.currency', readonly=True)

    @api.model
    def _state_selection(self):
        return self.env['sale.order']._fields['state']._description_selection(self.env)

    def _query(self):
        return SQL("""
            WITH technicians AS (
                SELECT order_id, partner_id,
                       COUNT(*) OVER (PARTITION BY order_id) AS technician_count
                  FROM sale_order_technician_rel
            ), first_line AS (
                SELECT order_id, MIN(id) AS line_id
                  FROM sale_order_line
                 WHERE display_type IS NULL
                 GROUP BY order_id
            )
            SELECT l.id::bigint * 10000000 + t.partner_id AS id,
                   t.partner_id AS technician_id,
                   l.order_id,
                   l.id AS order_line_id,
                   o.date_order AS date,
                   o.effective_date AS delivered_date,
                   o.partner_id,
                   o.team_id,
                   o.user_id,
                   l.product_id,
                   pt.categ_id,
                   l.department_id,
                   l.teeth,
                   CARDINALITY(ARRAY_REMOVE(
                       STRING_TO_ARRAY(REGEXP_REPLACE(COALESCE(l.teeth, ''), '[^0-9,]', '', 'g'), ','), ''
                   )) AS teeth_count,
                   l.product_uom_qty AS quantity,
                   l.price_subtotal AS amount,
                   l.price_subtotal / t.technician_count AS amount_share,
                   1 AS work_count,
                   CASE WHEN f.line_id = l.id THEN 1 ELSE 0 END AS case_count,
                   CASE WHEN f.line_id = l.id AND o.is_rework THEN 1 ELSE 0 END AS rework_count,
                   t.technician_count,
                   COALESCE(o.is_rework, FALSE) AS is_rework,
                   o.state,
                   o.company_id,
                   l.currency_id
              FROM sale_order_line l
              JOIN sale_order o ON o.id = l.order_id
              JOIN technicians t ON t.order_id = l.order_id
              JOIN first_line f ON f.order_id = l.order_id
         LEFT JOIN product_product pp ON pp.id = l.product_id
         LEFT JOIN product_template pt ON pt.id = pp.product_tmpl_id
             WHERE l.display_type IS NULL
               AND o.state != 'cancel'
        """)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(SQL("CREATE OR REPLACE VIEW %s AS (%s)", SQL.identifier(self._table), self._query()))

    def action_open_order(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'res_model': 'sale.order',
            'res_id': self.order_id.id, 'view_mode': 'form', 'views': [(False, 'form')],
        }
