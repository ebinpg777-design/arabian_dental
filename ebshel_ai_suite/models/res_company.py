# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    cai_connection_id = fields.Many2one(
        'community.ai.connection', string='Default AI Connection', ondelete='set null',
        help='Connection used by assistants and AI features that do not specify their own.')
    cai_assistant_id = fields.Many2one(
        'community.ai.assistant', string='Default AI Assistant', ondelete='set null',
        help='Assistant preselected when a user opens the AI console.')
