from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResPartner(models.Model):
    _inherit = 'res.partner'

    is_petty_cash_holder = fields.Boolean(string='Is Petty Cash Holder', help='Designates this partner as a petty cash holder.')
