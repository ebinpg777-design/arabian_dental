# -*- coding: utf-8 -*-
from odoo import api, fields, models

from .lab_case import patient_key


class SaleOrder(models.Model):
    """The order side of duplicate detection.

    `patient_key` is stored and indexed on the ORDER, not computed at search time, for
    one reason: the check runs while an executive is standing at a counter with the
    doctor waiting. Scanning and normalising every recent order's patient name in Python
    would make the warning arrive after the moment it was useful — and a duplicate
    warning that is slow is a duplicate warning nobody waits for.
    """
    _inherit = 'sale.order'

    patient_key = fields.Char(compute='_compute_patient_key', store=True, index=True)

    @api.depends('patient')
    def _compute_patient_key(self):
        for order in self:
            order.patient_key = patient_key(order.patient)


class SaleOrderFieldOrigin(models.Model):
    """An order that came from a clinic visit is always checked (client rule, 2026-08-09).

    Declared here rather than in `lab_order_control` because `visit_id` is this
    module's field. Odoo unions @api.depends across inheritance levels, so adding
    `visit_id` here keeps every trigger the base compute already declared.
    """
    _inherit = 'sale.order'

    @api.depends('visit_id')
    def _compute_verification_needed(self):
        return super()._compute_verification_needed()

    def _verification_origin_requires(self):
        self.ensure_one()
        return bool(self.visit_id) or super()._verification_origin_requires()


class SaleOrderClinicFlag(models.Model):
    """A partner this lab has taken work from IS a clinic.

    `is_clinic` had no writer but three `default_is_clinic` contexts, so on this
    database it was TRUE for 0 partners out of 7,278 — which quietly emptied Planning >
    Clinics, the whole coverage report and the Control Tower's rescue alert, all of
    which filter on it. The back-fill is a migration; this keeps it true from now on,
    at the one moment the answer is certain: the lab has confirmed an order for them.

    On the commercial partner, not the contact: a doctor ordering under their own name
    and under the practice's is one clinic, and every "clinic" screen groups that way.
    (client, 2026-08-29)
    """
    _inherit = 'sale.order'

    def action_confirm(self):
        result = super().action_confirm()
        partners = self.mapped('partner_id.commercial_partner_id').filtered(
            lambda p: not p.is_clinic)
        if partners:
            # sudo: a field executive may confirm an order without holding write on
            # the customer record, and the flag must not depend on who was standing
            # at the counter.
            partners.sudo().write({'is_clinic': True})
        return result
