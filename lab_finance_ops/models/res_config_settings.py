# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    lab_require_payment_approval = fields.Boolean(
        'Require Payment Approval',
        config_parameter='lab_finance_ops.require_payment_approval',
        help="Payments at or above the threshold must be approved before they can be posted.")
    lab_payment_approval_threshold = fields.Float(
        'Approval Threshold',
        config_parameter='lab_finance_ops.payment_approval_threshold', default=0.0,
        help="Leave at 0 to require approval on every payment.")
