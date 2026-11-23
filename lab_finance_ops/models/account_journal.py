# -*- coding: utf-8 -*-
from odoo import fields, models


class AccountJournal(models.Model):
    _inherit = 'account.journal'

    # Odoo 17 kept a "PDC - <bank>" account per bank: a cheque sat there from the day
    # it was received until the day it was deposited. (client, 2026-11-23: "the customer
    # invoices should be matched with postdated cheques on the date of cheque received.
    # bank account will reflect on the date when the cheque deposited in the bank")
    lab_pdc_account_id = fields.Many2one(
        'account.account', string='Cheques in Hand (PDC) Account', check_company=True,
        domain="[('reconcile', '=', True), ('account_type', '=', 'asset_current')]",
        help="A cheque for this bank waits here from the day it is received - the clinic's "
             "invoices are settled that day - until the day it is deposited, when it moves "
             "to the bank account.")
