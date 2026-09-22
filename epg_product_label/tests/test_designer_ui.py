# -*- coding: utf-8 -*-
"""Browser-side check for the designer widget.

The Python tests prove the render engines are right, but not that the canvas assets
compile or that dragging a box writes new coordinates back. This runs the real web
client to cover that.

It needs the ``websocket-client`` package and a Chrome binary; Odoo skips it
automatically when either is missing.
"""

from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestLabelDesignerUI(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.template = cls.env['epg.label.template'].create({
            'name': 'UI Test Label',
            'label_type': 'product',
            'label_width': 60.0,
            'label_height': 40.0,
            'layout_mode': 'roll',
            'margin_top': 0.0, 'margin_bottom': 0.0,
            'margin_left': 0.0, 'margin_right': 0.0,
        })
        cls.element = cls.env['epg.label.element'].create({
            'template_id': cls.template.id,
            'name': 'Product Name',
            'element_type': 'field',
            'field_path': 'name',
            'pos_x': 5.0, 'pos_y': 5.0, 'width': 30.0, 'height': 8.0,
        })
        cls.env.ref('base.user_admin').write({
            'group_ids': [(4, cls.env.ref('epg_product_label.group_label_designer').id)],
        })

    def test_designer_renders_and_moves_an_element(self):
        """Open the template form, drag the element, and check the move persisted."""
        self.start_tour(
            '/odoo/action-epg_product_label.action_epg_label_template/%s' % self.template.id,
            'epg_label_designer_tour', login='admin')
        self.element.invalidate_recordset()
        self.assertNotEqual(
            (self.element.pos_x, self.element.pos_y), (5.0, 5.0),
            "dragging the element on the canvas should have written new coordinates")
        self.assertGreater(self.element.pos_x, 5.0)
        self.assertGreater(self.element.pos_y, 5.0)
        # Snapping is on by default, so the result lands on a half-millimetre.
        self.assertEqual(self.element.pos_x % 0.5, 0.0)
        self.assertEqual(self.element.pos_y % 0.5, 0.0)
