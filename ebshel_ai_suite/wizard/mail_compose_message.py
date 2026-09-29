# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
from odoo import models


class MailComposeMessage(models.TransientModel):
    _inherit = 'mail.compose.message'

    def action_cai_writing_assistant(self):
        """Open the AI writing assistant on the current draft. Nothing is sent automatically."""
        self.ensure_one()
        return self.env['community.ai.text.wizard'].cai_open_for_composer(self)
