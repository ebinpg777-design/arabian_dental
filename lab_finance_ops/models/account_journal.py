# -*- coding: utf-8 -*-
from odoo import fields, models


class AccountJournal(models.Model):
    _inherit = 'account.journal'

    # Odoo 17 kept a "PDC - <bank>" account per bank: a cheque sits there from the day
    # it is received until the day it clears. (client, 2026-11-23: "the customer
    # invoices should be matched with postdated cheques on the date of cheque received.
    # bank account will reflect on the date when the cheque deposited in the bank"; the
    # same day: the bank account only when the cheque clears)
    lab_pdc_account_id = fields.Many2one(
        'account.account', string='Cheques in Hand (PDC) Account', check_company=True,
        domain="[('reconcile', '=', True), ('account_type', '=', 'asset_current')]",
        help="A cheque for this bank waits here from the day it is received - the clinic's "
             "invoices are settled that day - until the day the bank clears it, when it "
             "moves to the bank account.")
