# -*- coding: utf-8 -*-
from odoo import fields, models


class MrpWorkcenter(models.Model):
    _inherit = 'mrp.workcenter'

    # Where a bench's line goes on the Daily Production Report. Nearly all of them
    # are a row in their department's table; three are not, and the paper form
    # says so - model preparation has a line of its own under the heading, and the
    # final check and the packing are names at the foot.
    sheet_position = fields.Selection(
        [('section', "A row in its department's table"),
         ('model', 'The Model Preparation line under the heading'),
         ('check', '"Final Checked by" at the foot'),
         ('packed', '"Packed by" at the foot')],
        string='On the Production Report', default='section', required=True)
