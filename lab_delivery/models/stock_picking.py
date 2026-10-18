# -*- coding: utf-8 -*-
"""The courier receipt on the stock transfer.

`sale_custom` gave the transfer its courier fields (company, option, consignment
number) as plain text, typed at the counter. The same receipt reader that fills the
delivery's number fills these, and the receipt is kept on the transfer as proof of
what was handed over and when. (client, 2026-08-28)
"""
import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    courier_receipt_image = fields.Image(
        'Courier Receipt', max_width=1400, max_height=1400, copy=False,
        help="The booking receipt the courier handed back, photographed at the "
             "counter. The consignment number is read from it.")
    consignment_source = fields.Selection(
        [('typed', 'Typed'), ('barcode', 'Read from barcode'),
         ('vision', 'Read from receipt'), ('both', 'Barcode and receipt agree')],
        string='Number Came From', copy=False, default='typed')

    def _action_done(self):
        """A transfer validated at the counter closes its order's dispatch too.

        Marking a dispatch delivered already validates the transfer; this is the
        other direction, which was missing - see lab.delivery._close_if_shipped.
        Never allowed to fail the validation itself."""
        res = super()._action_done()
        orders = self.filtered(
            lambda p: p.picking_type_code == 'outgoing' and p.state == 'done').sale_id
        if orders:
            try:
                with self.env.cr.savepoint():
                    Delivery = self.env['lab.delivery'].sudo()
                    Delivery.search([('sale_order_id', 'in', orders.ids), ('direction', '=', 'out'),
                                     ('state', 'in', ('draft', 'assigned', 'out'))])._close_if_shipped()
            except Exception as exc:                                    # noqa: BLE001
                _logger.warning("closing dispatches for %s failed: %s", orders.mapped('name'), exc)
        return res
