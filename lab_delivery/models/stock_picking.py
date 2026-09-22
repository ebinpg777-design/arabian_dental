# -*- coding: utf-8 -*-
"""The courier receipt on the stock transfer.

`sale_custom` gave the transfer its courier fields (company, option, consignment
number) as plain text, typed at the counter. The same receipt reader that fills the
delivery's number fills these, and the receipt is kept on the transfer as proof of
what was handed over and when. (client, 2026-08-28)
"""
from odoo import fields, models


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
