# -*- coding: utf-8 -*-
from odoo import _, api, fields, models


class LabOrderCancelChoice(models.TransientModel):
    """Cancel, asked once and plainly - and, on a confirmed order whose jobs are
    being made, the amendment offered beside it.

    An order used to be corrected with Cancel and Set to Quotation. The cancel
    stops every job, so the work done on the case was left behind on a cancelled
    job. This is where that habit meets the other way. (client, 2026-11-26)
    """
    _name = 'lab.order.cancel.choice'
    _description = 'Cancel or Amend an Order'

    order_id = fields.Many2one('sale.order', required=True, readonly=True)
    order_state = fields.Selection(related='order_id.state')
    jobs = fields.Char('Production', compute='_compute_jobs')
    has_jobs = fields.Boolean(compute='_compute_jobs')
    invoices = fields.Char('Invoices', compute='_compute_jobs')

    @api.depends('order_id')
    def _compute_jobs(self):
        for wizard in self:
            order = wizard.order_id
            jobs = order._lab_live_productions() if order else None
            labels = dict(jobs._fields['state']._description_selection(self.env)) if jobs else {}
            wizard.jobs = ', '.join('%s (%s)' % (j.name, labels.get(j.state, j.state))
                                    for j in jobs) if jobs else False
            wizard.has_jobs = bool(jobs) and order.state == 'sale'
            posted = order.sudo().invoice_ids.filtered(
                lambda m: m.state == 'posted' and m.move_type == 'out_invoice') if order else None
            wizard.invoices = ', '.join(posted.mapped('name')) if posted else False

    def action_amend(self):
        self.ensure_one()
        self.order_id.action_amend()
        return {'type': 'ir.actions.act_window_close'}

    def action_cancel_order(self):
        self.ensure_one()
        # without the button's flag: this time it is meant
        self.order_id.with_context(lab_offer_amend=False).action_cancel()
        return {'type': 'ir.actions.act_window_close'}
