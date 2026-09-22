# -*- coding: utf-8 -*-
from odoo.tests import TransactionCase


class LabelCase(TransactionCase):
    """Shared fixtures: one product with a valid EAN-13, one addressable contact."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Template = cls.env['epg.label.template']
        cls.Element = cls.env['epg.label.element']
        cls.Print = cls.env['epg.label.print']

        cls.product = cls.env['product.product'].create({
            'name': 'Label Test Widget',
            'default_code': 'LTW-001',
            'barcode': '5901234123457',
            'list_price': 12.5,
            'standard_price': 7.0,
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Rivera Tooling',
            'street': '12 Foundry Row',
            'street2': 'Unit B',
            'city': 'Sheffield',
            'zip': 'S1 2AB',
            'phone': '+44 114 496 0000',
            'email': 'orders@example.com',
        })

    @classmethod
    def make_template(cls, **values):
        base = {
            'name': 'Test Template',
            'label_type': 'product',
            'unit': 'mm',
            'label_width': 60.0,
            'label_height': 40.0,
            'layout_mode': 'roll',
            'margin_top': 0.0, 'margin_bottom': 0.0,
            'margin_left': 0.0, 'margin_right': 0.0,
            'output_type': 'both',
        }
        base.update(values)
        return cls.Template.create(base)

    @classmethod
    def make_element(cls, template, **values):
        base = {
            'template_id': template.id,
            'name': 'Element',
            'element_type': 'text',
            'pos_x': 1.0, 'pos_y': 1.0,
            'width': 30.0, 'height': 6.0,
        }
        base.update(values)
        return cls.Element.create(base)
