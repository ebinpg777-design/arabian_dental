# -*- coding: utf-8 -*-
from odoo import fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    # The lab has always run this off a paper sheet - one line per doctor, a
    # percentage next to their name, some crossed out and re-agreed in pen.
    # Putting it on the partner is what lets a discount default to the number
    # already agreed with that clinic instead of everyone typing 5% out of habit.
    outstanding_discount_percent = fields.Float(
        string='Standard Discount %',
        help="The discount percentage normally agreed with this customer. "
             "Suggested automatically when starting a new Outstanding Discount for "
             "them - still just a starting point, not a limit.")
