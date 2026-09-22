# -*- coding: utf-8 -*-
from odoo import api, fields, models


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    # A payment belongs to the route its clinic is on. Stored (not a plain related on
    # the fly) so the standard payment screens can group, filter and sort by it, and so
    # a month's collections can be aggregated in one grouped query.
    # (client, 2026-08-21: "track the payments in sales route wise")
    team_id = fields.Many2one(
        'crm.team', string='Sales Route',
        compute='_compute_team_id', store=True, readonly=False,
        index='btree_not_null', copy=False,
        help="The clinic's Sales Route. Set from the clinic when the payment is "
             "created; can be corrected by hand if a payment belongs elsewhere.")

    # Depends on the CLINIC, not on the clinic's route: with 'partner_id.team_id' in
    # the trigger, moving a clinic to another route silently rewrote the route on every
    # payment it had ever made - including the ones corrected by hand, which the help
    # text above promises will stick. A payment records where the money came from on the
    # day it arrived. (2026-08-21)
    @api.depends('partner_id')
    def _compute_team_id(self):
        for payment in self:
            partner = payment.partner_id
            if partner:
                payment.team_id = partner.team_id or partner.commercial_partner_id.team_id
            else:
                payment.team_id = payment.team_id
