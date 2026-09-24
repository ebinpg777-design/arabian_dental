# -*- coding: utf-8 -*-
from odoo import fields, models


class MrpProduction(models.Model):
    _inherit = 'mrp.production'

    # sale_line_id is provided natively by ``sale_mrp``; we derive the rest from it
    # (the old procurement.order chain no longer exists in Odoo 19).
    sale_id = fields.Many2one(
        'sale.order', related='sale_line_id.order_id',
        string='Sale Order', store=True)
    color_scheme = fields.Char(
        related='sale_line_id.color_scheme.name', string='Colour', store=True)
    ul = fields.Selection(
        related='sale_line_id.ul', string='Jaw', store=True)


class MrpWorkorder(models.Model):
    _inherit = 'mrp.workorder'

    # NOT 'Technician': `lab_workcenter_scan.bench_user_id` carries that name
    # and that meaning - who has the job at the bench right now - and two fields
    # with one label is a trap in a filter or an export, especially when this one
    # is set on 15 of this database's 127,977 work orders. Kept because a job
    # card still prints it. (measured 2026-09-12)
    user_id = fields.Many2one(
        'res.users', string='Technician (legacy)',
        help="Superseded by the technician the Station Board records. Left in "
             "place because the printed job card still reads it.")
    sale_id = fields.Many2one(
        related='production_id.sale_id', readonly=True,
        string='Sale Order', store=True)
    sale_line_id = fields.Many2one(
        'sale.order.line', related='production_id.sale_line_id',
        readonly=True, string='Sale Order Line', store=True)
    color_scheme = fields.Char(
        related='production_id.color_scheme', readonly=True, string='Colour')
    ul = fields.Selection(
        related='production_id.ul', readonly=True, string='Jaw')


class MrpWorkcenter(models.Model):
    _inherit = 'mrp.workcenter'

    users = fields.Many2many(
        'res.users', 'workcenter_users_rel', 'workcenter_id', 'uid',
        string="Technicians")
