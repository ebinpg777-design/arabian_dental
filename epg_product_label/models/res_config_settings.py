# -*- coding: utf-8 -*-
from odoo import api, fields, models

from .label_units import ZPL_DENSITY_SELECTION


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    epg_label_replace_standard = fields.Boolean(
        string='Replace Standard Label Wizard',
        config_parameter='epg_product_label.replace_standard',
        help="Route Odoo's own 'Print Labels' action on products to the label "
             "builder instead. Uncheck to keep both available side by side.")

    epg_label_preview_product_id = fields.Many2one(
        'product.product', string='Preview Product',
        config_parameter='epg_product_label.preview_product_id',
        help="Product used to fill the designer preview when a template does not "
             "name one of its own.")
    epg_label_preview_partner_id = fields.Many2one(
        'res.partner', string='Preview Contact',
        config_parameter='epg_product_label.preview_partner_id')
    epg_label_pricelist_id = fields.Many2one(
        'product.pricelist', string='Default Pricelist',
        config_parameter='epg_product_label.pricelist_id',
        help="Pricelist the print wizard proposes for regular prices.")
    epg_label_promo_pricelist_id = fields.Many2one(
        'product.pricelist', string='Default Promotional Pricelist',
        config_parameter='epg_product_label.promo_pricelist_id',
        help="Pricelist the print wizard proposes for promotional prices, used by "
             "the promotional price and price difference elements.")

    epg_label_zpl_density = fields.Selection(
        ZPL_DENSITY_SELECTION, string='ZPL Print Density',
        config_parameter='epg_product_label.zpl_density', default='8')
    epg_label_zpl_rotation = fields.Selection([
        ('0', 'None'), ('90', '90°'), ('180', '180°'), ('270', '270°'),
    ], string='ZPL Rotation', config_parameter='epg_product_label.zpl_rotation',
        default='0')
    epg_label_zpl_encoding = fields.Selection([
        ('utf8', 'UTF-8 (^CI28)'), ('cp850', 'CP850'), ('ascii', 'ASCII only'),
    ], string='ZPL Character Encoding',
        config_parameter='epg_product_label.zpl_encoding', default='utf8')
    epg_label_zpl_darkness = fields.Integer(
        string='ZPL Darkness', config_parameter='epg_product_label.zpl_darkness',
        default=0, help="Relative media darkness, -30 to 30. 0 keeps the printer's "
                        "own setting.")
    epg_label_zpl_speed = fields.Integer(
        string='ZPL Print Speed', config_parameter='epg_product_label.zpl_speed',
        default=0, help="Inches per second, 1 to 14. 0 keeps the printer's own setting.")
    epg_label_zpl_printer_url = fields.Char(
        string='ZPL Printer Endpoint',
        config_parameter='epg_product_label.zpl_printer_url',
        help="HTTP endpoint of an IoT Box or print server that accepts a raw ZPL "
             "body, e.g. http://192.168.1.50:8069/hw_proxy/print_zpl. Leave empty "
             "to download the ZPL file instead of printing directly.")

    @api.model
    def get_values(self):
        values = super().get_values()
        # config_parameter handles the round trip; nothing extra to read here, but
        # the hook stays so downstream modules have somewhere to extend.
        return values

    def action_open_label_templates(self):
        return self.env['ir.actions.act_window']._for_xml_id(
            'epg_product_label.action_epg_label_template')
