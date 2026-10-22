# -*- coding: utf-8 -*-
from odoo import api, models

# Whoever keeps the petty cash box: an Approver sees every fund, and an
# Administrator is an Approver.
BOX_KEEPERS = ('ebshel_petty_cash.group_petty_cash_approver',)


class CashHandover(models.Model):
    _inherit = 'lab.cash.handover'

    @api.model
    def _receiver_groups(self):
        """The petty cash box's keepers count executives' cash in too, as they did
        under the old petty_cash module's officer role."""
        return super()._receiver_groups() + BOX_KEEPERS
