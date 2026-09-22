from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class AccountAccount(models.Model):
    _inherit = 'account.account'

    is_petty_cash = fields.Boolean(string='Is Petty Cash Account', default=False, help="Designates this account as a petty cash account.")


   