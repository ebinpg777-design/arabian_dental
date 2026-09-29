# -*- coding: utf-8 -*-
from odoo import fields, models

from .ticket import CHANNELS


class DoctorCallLog(models.Model):
    _inherit = 'lab.doctor.call.log'

    ticket_id = fields.Many2one('lab.doctor.ticket', string='Ticket', ondelete='set null', index=True)
    channel = fields.Selection(CHANNELS, default='phone', required=True,
                               help="How the doctor was reached, or tried.")
