# -*- coding: utf-8 -*-
"""Urgency (R10).

The lab already had `low / normal / urgent`. What it had no way to say was
**emergency** — the broken appliance for a patient sitting in the chair, which must go
out today and be visible from across the room.

So the existing selection is EXTENDED with one value rather than replaced by the spec's
`0/1/2` keys. Renumbering would mean migrating every existing order and case, and would
silently break the several places that already compare against `'urgent'`
(`sale_custom.compute_emergency`, the production flow board, the case's own line
defaults). A key change that buys nothing but breaks working code is not worth making.
"""
from odoo import api, fields, models

# Open in the sense that matters for "urgent work in progress": not finished, not dead.
# A tuple, not a bare string: `('invoiced')` made `not in` a substring test.
INVOICED_STATES = ('invoiced', 'upselling')


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    priority = fields.Selection(
        selection_add=[('emergency', 'Emergency')],
        ondelete={'emergency': 'set default'})

    is_urgent_open = fields.Boolean(
        compute='_compute_is_urgent_open', store=True,
        help="Urgent or emergency AND still in progress — what the urgent-work list "
             "and the KPI tiles filter on.")

    # `state` in its own right: a cancelled order's invoice status is 'no', which
    # reads as outstanding, so the invoice status alone kept dead jobs urgent.
    @api.depends('priority', 'state', 'invoice_status')
    def _compute_is_urgent_open(self):
        for order in self:
            order.is_urgent_open = bool(
                order.priority in ('urgent', 'emergency')
                and order.state != 'cancel'
                and order.invoice_status not in INVOICED_STATES)

    @api.onchange('priority')
    def _onchange_priority_emergency(self):
        """Make an emergency a deliberate act, not a mis-click on a dropdown.

        Everything downstream reacts to it — the dispatch board pins it, the delay grace
        shortens, people get notified. A priority that gets set by accident teaches the
        floor to ignore the colour.
        """
        if self.priority == 'emergency':
            return {'warning': {
                'title': "Emergency",
                'message': "This marks the order as an emergency: it will be pinned on "
                           "the dispatch board, checked against a much shorter delivery "
                           "grace, and notified to the manager. Set it only for work "
                           "that genuinely must go out today.",
            }}
