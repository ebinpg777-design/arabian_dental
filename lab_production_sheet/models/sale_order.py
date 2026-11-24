# -*- coding: utf-8 -*-
from odoo import fields, models

YES_NO = [('Yes', 'Yes'), ('No', 'No')]


class SaleOrder(models.Model):
    """What the production sheet's heading asks that an order did not hold.

    Keyed 'Yes' / 'No' like the order's own Wax Bite and Scanned Impression, so
    the three read the same in a filter and on a print.
    """
    _inherit = 'sale.order'

    # A case can arrive as a poured cast rather than as an impression.
    impression_type = fields.Selection(
        selection_add=[('Cast', 'Cast')], ondelete={'Cast': 'set null'})
    opposing_model = fields.Selection(
        YES_NO, string='Opposing Model', tracking=True,
        help="Did the model of the opposing arch come with the case?")
    pre_cutting_model = fields.Selection(
        YES_NO, string='Pre Cutting Model', tracking=True,
        help="Is a model to be kept as it was before the dies are cut?")
