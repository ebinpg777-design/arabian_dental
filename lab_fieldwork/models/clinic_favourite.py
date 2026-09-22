# -*- coding: utf-8 -*-
"""One executive's starred clinics.

A row per (executive, clinic) rather than a flag on the clinic: the same doctor
is a favourite of the person who works that round and nothing at all to the six
other people who can see the record. A flag on res.partner would also mean an
executive needs WRITE on the clinic to star it, which is exactly the permission
this module spends its record rules not giving them. (client, 2026-09-02)
"""
from odoo import api, fields, models


class LabClinicFavourite(models.Model):
    _name = 'lab.clinic.favourite'
    _description = 'Executive\'s Favourite Clinic'
    _rec_name = 'partner_id'

    user_id = fields.Many2one(
        'res.users', required=True, index=True, ondelete='cascade',
        default=lambda self: self.env.user)
    partner_id = fields.Many2one(
        'res.partner', string='Clinic', required=True, index=True,
        ondelete='cascade')
    company_id = fields.Many2one(
        'res.company', default=lambda self: self.env.company, index=True)

    _user_partner_uniq = models.Constraint(
        'unique(user_id, partner_id)',
        'This clinic is already one of your favourites.')

    @api.model
    def toggle(self, partner_id):
        """Star or unstar one clinic for the person asking. Returns the state.

        Idempotent by construction: a double tap on a slow phone connection is
        the normal case, not the exception, so this reports what IS rather than
        failing on the unique index.
        """
        partner_id = int(partner_id)
        existing = self.search([('user_id', '=', self.env.uid),
                                ('partner_id', '=', partner_id)], limit=1)
        if existing:
            existing.unlink()
            return False
        self.create({'user_id': self.env.uid, 'partner_id': partner_id})
        return True

    @api.model
    def my_partner_ids(self):
        """The clinic ids this user has starred."""
        return self.search([('user_id', '=', self.env.uid)]).partner_id.ids
