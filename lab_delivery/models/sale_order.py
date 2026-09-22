# -*- coding: utf-8 -*-
"""Auto-create the delivery the moment the work is actually finished."""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    delivery_ids = fields.One2many('lab.delivery', 'sale_order_id',
                                   string='Deliveries')
    # NOT `delivery_count`: that name belongs to sale_stock (number of stock pickings)
    # and drives its "Delivery" smart button. Overriding it made that button appear on
    # orders with a dispatch but no picking, and clicking it raised IndexError in
    # `_get_action_view_picking` (production, 2026-08-17).
    lab_delivery_count = fields.Integer(
        string='Dispatches', compute='_compute_lab_delivery_count')

    # Has this work actually reached the doctor? Computed from the dispatch rather
    # than written by the wizard that raises one: an executive can confirm the
    # handover from the done wizard, from My Day, or from the dispatch form, and the
    # courier feed marks its own parcels delivered without a human at all. All four
    # end at `state = 'delivered'`, so depending on that is the only version of this
    # flag no path can slip past. (client, 2026-08-22)
    lab_handed_over = fields.Boolean(
        string='Handed Over', compute='_compute_lab_handed_over', store=True,
        index='btree_not_null', copy=False,
        help="Set when a dispatch for this order is confirmed delivered. A handed "
             "over order is no longer offered in the Hand Over wizard.")

    # Work that still has to reach a doctor: confirmed, not handed over, and nobody
    # already carrying it. Stored and indexed because three screens ask it as a
    # DOMAIN - the clinic list in the wizard, the worklist action, and the partner
    # filter - and a non-stored compute can answer none of them. (client, 2026-08-28)
    lab_awaiting_delivery = fields.Boolean(
        string='Waiting to be delivered', compute='_compute_lab_awaiting_delivery',
        store=True, index='btree_not_null', copy=False)

    # The bill the doctor will be looking at, beside the case number. Not stored: it
    # is read on a handful of rows at a time, and an executive cannot read
    # account.move at all, so it is fetched elevated.
    lab_invoice_ref = fields.Char(
        string='Invoice', compute='_compute_lab_invoice_ref')

    @api.depends('state', 'lab_handed_over', 'delivery_ids.state',
                 'delivery_ids.direction')
    def _compute_lab_awaiting_delivery(self):
        for order in self:
            carried = any(d.direction == 'out'
                          and d.state not in ('cancel', 'failed')
                          for d in order.delivery_ids)
            order.lab_awaiting_delivery = bool(
                order.state in ('sale', 'done')
                and not order.lab_handed_over
                and not carried)

    def _compute_lab_invoice_ref(self):
        """The posted customer invoices for this order, oldest first."""
        for order in self:
            names = order.sudo().invoice_ids.filtered(
                lambda m: m.move_type == 'out_invoice' and m.state == 'posted'
            ).sorted('invoice_date').mapped('name')
            order.lab_invoice_ref = ', '.join(n for n in names if n)

    @api.depends_context('lab_with_invoice')
    def _compute_display_name(self):
        """Show the invoice number beside the case number where it is asked for.

        The hand-over list is a column of checkboxes read standing in a doorway, and
        the number the doctor quotes down the phone is the INVOICE number, not the
        sales order. Context-gated so nothing else in the database changes.
        (client, 2026-08-28)
        """
        super()._compute_display_name()
        if not self.env.context.get('lab_with_invoice'):
            return
        for order in self:
            ref = order.lab_invoice_ref
            if ref:
                order.display_name = '%s · %s' % (order.display_name, ref)

    def action_lab_raise_delivery(self):
        """Raise the delivery for this order, with the clinic taken from it.

        One button on the worklist: the executive should not have to open a wizard,
        find the clinic they are already looking at, and tick the one line.
        """
        self.ensure_one()
        if self.state not in ('sale', 'done'):
            raise UserError(_("%s is not a confirmed order.", self.name))
        existing = self.env['lab.delivery'].sudo().search([
            ('sale_order_id', '=', self.id), ('direction', '=', 'out'),
            ('state', 'not in', ('cancel', 'failed'))], limit=1)
        if existing:
            raise UserError(_(
                "%(order)s is already on %(delivery)s. Open that rather than raising "
                "a second record for the same box.",
                order=self.name, delivery=existing.name))
        delivery = self.env['lab.delivery'].create({
            'sale_order_id': self.id,
            # partner_id and patient come from the order itself (_compute_from_order).
            'executive_id': (self.visit_id.user_id.id if self.visit_id
                             else self.user_id.id) or self.env.uid,
            'scheduled_date': self.commitment_date or fields.Datetime.now(),
            'state': 'assigned',
        })
        return {
            'type': 'ir.actions.act_window', 'name': _('Delivery'),
            'res_model': 'lab.delivery', 'res_id': delivery.id,
            'views': [(False, 'form')], 'view_mode': 'form',
        }

    @api.model
    def action_lab_delivery_worklist(self):
        """Everything still to be delivered, whichever clinic it is for."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('Work waiting to be delivered'),
            'res_model': 'sale.order',
            'view_mode': 'list',
            'views': [(self.env.ref(
                'lab_delivery.view_order_list_awaiting_delivery').id, 'list')],
            'domain': [('lab_awaiting_delivery', '=', True)],
            'context': {'create': False},
        }

    @api.depends('delivery_ids.state', 'delivery_ids.direction')
    def _compute_lab_handed_over(self):
        # Outbound only: a pickup marked 'delivered' means the LAB received an
        # impression, and it must not tell the Hand Over wizard the doctor has
        # their finished work.
        for order in self:
            order.lab_handed_over = any(
                d.state == 'delivered' and d.direction == 'out'
                for d in order.delivery_ids)

    def _compute_lab_delivery_count(self):
        counts = dict(self.env['lab.delivery']._read_group(
            [('sale_order_id', 'in', self.ids)], ['sale_order_id'], ['__count']))
        for order in self:
            order.lab_delivery_count = counts.get(order, 0)

    def _lab_ensure_delivery(self):
        """One open delivery per order, created when production finishes.

        Idempotent on purpose: several MOs finishing in one transaction must still
        produce exactly one delivery.
        """
        Delivery = self.env['lab.delivery'].sudo()
        # Which orders already have a live delivery - asked once for the batch.
        # A search_count per order meant marking 50 MOs done ran 50 queries; the
        # count compute four lines above already does it this way.
        existing = {
            order.id for order, in Delivery._read_group(
                [('sale_order_id', 'in', self.ids),
                 ('direction', '=', 'out'),
                 ('state', 'not in', ('cancel', 'failed'))], ['sale_order_id'])
        }
        for order in self:
            if order.state not in ('sale', 'done'):
                continue
            productions = order.mrp_production_ids.filtered(
                lambda m: m.state != 'cancel')
            if not productions or any(m.state != 'done' for m in productions):
                continue
            if order.id in existing:
                continue
            executive = order.visit_id.user_id if order.visit_id else order.user_id
            Delivery.create({
                'sale_order_id': order.id,
                'executive_id': executive.id or self.env.uid,
                'scheduled_date': order.commitment_date or fields.Datetime.now(),
            })
            order.message_post(body=_("Work finished — delivery record created."))

    def action_view_deliveries(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Deliveries'),
            'res_model': 'lab.delivery', 'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': [('sale_order_id', '=', self.id)],
            'context': {'default_sale_order_id': self.id},
        }


class MrpProduction(models.Model):
    _inherit = 'mrp.production'

    def write(self, vals):
        res = super().write(vals)
        if vals.get('state') == 'done':
            self.sudo().mapped('sale_id')._lab_ensure_delivery()
        return res
