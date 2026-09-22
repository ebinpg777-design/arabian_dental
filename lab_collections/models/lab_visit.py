# -*- coding: utf-8 -*-
from odoo import _, api, fields, models


class LabVisit(models.Model):
    """The clinic's statement and open receivable, from the visit itself.

    An executive at the counter is asked "what do we owe?" - the answer was in
    Collections, two apps away from the visit they had open. (client, 2026-09-17)
    """
    _inherit = 'lab.visit'

    clinic_open_receivable = fields.Monetary(
        string='Open Receivable', currency_field='currency_id',
        compute='_compute_clinic_open_receivable',
        help="What the clinic still owes, counted back from the ledger.")

    @api.depends('partner_id', 'company_id')
    def _compute_clinic_open_receivable(self):
        Perf = self.env['lab.collection.performance'].sudo()
        for visit in self:
            partner = visit.partner_id.commercial_partner_id
            if not partner:
                visit.clinic_open_receivable = 0.0
                continue
            debits = Perf._open_debits(visit.company_id, partner_ids=[partner.id])
            visit.clinic_open_receivable = sum(d['open'] for d in debits)

    def action_open_statement(self):
        self.ensure_one()
        return self.env['lab.collection.performance'].action_statement_for_partner(
            self.partner_id.commercial_partner_id.id)

    def action_open_receivable(self):
        self.ensure_one()
        return self.env['lab.collection.performance'].action_open_items_for_partner(
            self.partner_id.commercial_partner_id.id)
