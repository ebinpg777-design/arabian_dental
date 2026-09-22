# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # Related to the website being edited, the way every other per-site setting
    # in this form works - so a database with two sites configures each one.
    pwa_enabled = fields.Boolean(
        related='website_id.pwa_enabled', readonly=False)
    pwa_name = fields.Char(related='website_id.pwa_name', readonly=False)
    pwa_short_name = fields.Char(
        related='website_id.pwa_short_name', readonly=False)
    pwa_start_url = fields.Char(
        related='website_id.pwa_start_url', readonly=False)
    pwa_theme_color = fields.Char(
        related='website_id.pwa_theme_color', readonly=False)
    pwa_icon = fields.Image(related='website_id.pwa_icon', readonly=False)
    pwa_icon_warning = fields.Char(related='website_id.pwa_icon_warning')
