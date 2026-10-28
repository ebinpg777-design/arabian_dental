# -*- coding: utf-8 -*-
"""Odoo 17's Material on the order (client, 2026-10-28: "In old server there is an
option of entering Material in the sale order. Please do the same here and migrate
the information to old SOs")."""
import importlib.util
import os

from psycopg2 import IntegrityError

from odoo.modules.module import get_module_path
from odoo.tests import Form, TransactionCase, tagged
from odoo.tools import mute_logger


def _post_migrate():
    path = os.path.join(get_module_path('sale_custom'), 'migrations', '20.0.1.58.0', 'post-migrate.py')
    spec = importlib.util.spec_from_file_location('sale_custom_material_post_migrate', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.migrate


@tagged('post_install', '-at_install')
class TestOrderMaterial(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clinic = cls.env['res.partner'].create({'name': 'MT Dental Care'})
        cls.Material = cls.env['lab.material']

    def _order(self, **vals):
        return self.env['sale.order'].create(dict({'partner_id': self.clinic.id}, **vals))

    def test_an_order_carries_its_material(self):
        vita = self.Material.create({'name': 'MT VITA'})
        form = Form(self.env['sale.order'])
        form.partner_id = self.clinic
        form.material_id = vita
        order = form.save()
        self.assertEqual(order.material_id, vita)
        search = self.env['sale.order'].get_view(view_type='search')['arch']
        self.assertIn('group_material', search, "orders group by material")

    def test_a_material_name_is_unique(self):
        self.Material.create({'name': 'MT IVOCLAR'})
        with mute_logger('odoo.sql_db'), self.assertRaises(IntegrityError), self.cr.savepoint():
            self.Material.create({'name': 'MT IVOCLAR'})

    def test_the_registration_slip_prints_it(self):
        order = self._order(patient='MT ANU', material_id=self.Material.create({'name': 'MT LITHIUM'}).id)
        html = self.env['ir.actions.report']._render_qweb_html(
            'sale_custom.action_report_dental_registration_ack', order.ids)[0].decode()
        self.assertIn('MT LITHIUM', html)

    def test_the_migrated_line_becomes_the_field(self):
        """The migration wrote "Material: VITA" into Special Instructions; the upgrade
        makes it the field and takes the line out - only where it is the whole text."""
        listed = self.Material.create({'name': 'MT VM9'})
        only = self._order(instruction='Material: MT VITA')
        twin = self._order(instruction='Material: MT VITA')
        known = self._order(instruction='Material: MT VM9')
        added_to = self._order(instruction='Material: MT VITA\nRing the doctor first')
        other = self._order(instruction='Ring the doctor first')
        chosen = self._order(instruction='Material: MT VITA', material_id=listed.id)
        self.env.flush_all()

        _post_migrate()(self.env.cr, '20.0.1.57.0')
        self.env.invalidate_all()

        vita = self.Material.search([('name', '=', 'MT VITA')])
        self.assertEqual(len(vita), 1, "one entry for every order that named it")
        self.assertEqual((only.material_id, twin.material_id, known.material_id), (vita, vita, listed))
        self.assertFalse(only.instruction or twin.instruction or known.instruction)
        self.assertEqual(added_to.instruction, 'Material: MT VITA\nRing the doctor first', "somebody's own text stays")
        self.assertFalse(added_to.material_id)
        self.assertEqual(other.instruction, 'Ring the doctor first')
        self.assertEqual(chosen.material_id, listed, "a material already chosen is not overwritten")
        self.assertEqual(chosen.instruction, 'Material: MT VITA')

        _post_migrate()(self.env.cr, '20.0.1.57.0')
        self.assertEqual(self.Material.search_count([('name', '=', 'MT VITA')]), 1, "a second run changes nothing")
