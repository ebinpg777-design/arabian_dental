# -*- coding: utf-8 -*-
from odoo import models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    def action_epg_print_labels(self):
        """Open the print wizard on the selected contacts."""
        action = self.env['ir.actions.act_window']._for_xml_id(
            'epg_product_label.action_epg_label_print')
        action['context'] = {
            'default_partner_ids': self.ids,
            'default_label_type': 'partner',
        }
        return action
