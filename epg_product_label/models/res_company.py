# -*- coding: utf-8 -*-
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    epg_label_template_ids = fields.One2many(
        'epg.label.template', 'company_id', string='Label Templates')
