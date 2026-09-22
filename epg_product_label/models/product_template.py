# -*- coding: utf-8 -*-
"""Entry points from products into the label builder.

``action_open_label_layout`` is Odoo's own "Print Labels" action, reached from the
product list and the product form.  When the *Replace Standard Label Wizard*
setting is on we take it over; otherwise both wizards stay available and the
builder gets its own menu entry.
"""

from odoo import api, models
from odoo.exceptions import ValidationError
from odoo.tools.translate import _


class ProductLabelMixin(models.AbstractModel):
    _name = 'epg.product.label.mixin'
    _description = 'Product Label Builder Entry Points'

    def _epg_label_wizard_action(self, default_field):
        self.ensure_one_type()
        action = self.env['ir.actions.act_window']._for_xml_id(
            'epg_product_label.action_epg_label_print')
        action['context'] = {
            default_field: self.ids,
            'default_label_type': 'product',
        }
        return action

    def ensure_one_type(self):
        if any(record.type == 'service' for record in self):
            raise ValidationError(
                _("Labels cannot be printed for products of service type."))

    @api.model
    def _epg_replaces_standard_wizard(self):
        return self.env['ir.config_parameter'].sudo().get_param(
            'epg_product_label.replace_standard') in ('True', 'true', '1', True)


class ProductTemplate(models.Model):
    _name = 'product.template'
    _inherit = ['product.template', 'epg.product.label.mixin']

    def action_open_label_layout(self):
        if self._epg_replaces_standard_wizard():
            return self.action_epg_print_labels()
        return super().action_open_label_layout()

    def action_epg_print_labels(self):
        return self._epg_label_wizard_action('default_product_tmpl_ids')


class ProductProduct(models.Model):
    _name = 'product.product'
    _inherit = ['product.product', 'epg.product.label.mixin']

    def action_open_label_layout(self):
        if self._epg_replaces_standard_wizard():
            return self.action_epg_print_labels()
        return super().action_open_label_layout()

    def action_epg_print_labels(self):
        return self._epg_label_wizard_action('default_product_ids')
