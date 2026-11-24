# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError

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


class SaleOrderSheets(models.Model):
    """The two sheets, printed from the order for all of its jobs. (client, 2026-11-24)"""
    _inherit = 'sale.order'

    def _lab_sheet_productions(self):
        # by the order each job was raised for (sale_custom's sale_id), which every
        # job of the lab carries, rather than through the procurement chain
        jobs = self.env['mrp.production'].search(
            [('sale_id', 'in', self.ids), ('state', '!=', 'cancel')])
        if not jobs:
            raise UserError(_(
                "%s has no manufacturing order yet - the sheets are printed from the jobs "
                "an order raises when it is confirmed.", ', '.join(self.mapped('name'))))
        # an order's jobs together, in the order they were raised
        return jobs.sorted(lambda m: (m.sale_id.id, m.id))

    def action_print_job_sheets(self):
        return self.env.ref('lab_production_sheet.action_report_job_sheet') \
            .report_action(self._lab_sheet_productions())

    def action_print_production_reports(self):
        return self.env.ref('lab_production_sheet.action_report_production_report') \
            .report_action(self._lab_sheet_productions())
