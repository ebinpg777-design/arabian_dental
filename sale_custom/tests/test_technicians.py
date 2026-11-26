# -*- coding: utf-8 -*-
"""Technicians are contacts marked so (client, 2026-10-29: "Add a boolean in partner
that is_technician. filter the partner with is_technician. Set the field for all
partners those are added in any of the SOs. Provide a new widget button of creating
new technician from SO")."""
import importlib.util
import os

from odoo.exceptions import UserError
from odoo.modules.module import get_module_path
from odoo.tests import TransactionCase, tagged


def _post_migrate():
    path = os.path.join(get_module_path('sale_custom'), 'migrations', '20.0.1.59.0', 'post-migrate.py')
    spec = importlib.util.spec_from_file_location('sale_custom_technician_post_migrate', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.migrate


@tagged('post_install', '-at_install')
class TestTechnicians(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Partner = cls.env['res.partner']
        cls.clinic = cls.Partner.create({'name': 'TT Dental Care'})

    def _order(self, technicians, **vals):
        return self.env['sale.order'].create(dict(
            {'partner_id': self.clinic.id, 'technician_ids': [(6, 0, technicians.ids)]}, **vals))

    def test_the_field_offers_technicians_and_makes_them(self):
        field = self.env['sale.order']._fields['technician_ids']
        self.assertEqual(field.domain, "[('is_technician', '=', True), ('active', '=', True)]")
        self.assertEqual(field.context, {'active_test': False, 'default_is_technician': True})
        made = self.Partner.with_context(**field.context).name_create('TT QUICK')[0]
        self.assertTrue(self.Partner.browse(made).is_technician, "quick-create from the field marks one")
        form = self.env['sale.order'].get_view(view_type='form')['arch']
        self.assertIn('widget="technician_tags"', form)

    def test_everybody_already_named_on_an_order_is_marked(self):
        named = self.Partner.create({'name': 'TT NAMED'})
        other = self.Partner.create({'name': 'TT NEVER NAMED'})
        self._order(named)
        named.is_technician = False
        self.env.flush_all()
        _post_migrate()(self.env.cr, '20.0.1.58.0')
        self.env.invalidate_all()
        self.assertTrue(named.is_technician)
        self.assertFalse(other.is_technician)

    def test_a_new_technician_is_made_once_whatever_the_spelling(self):
        made = self.Partner.lab_create_technician('  tt   rana . ', phone='9847000001')
        self.assertTrue(made['created'])
        partner = self.Partner.browse(made['id'])
        self.assertEqual((partner.name, partner.is_technician, partner.phone), ('TT RANA .', True, '9847000001'))
        again = self.Partner.lab_create_technician('TT Rana')
        self.assertEqual((again['id'], again['created']), (partner.id, False), "the same person, not a second one")
        with self.assertRaises(UserError):
            self.Partner.lab_create_technician(' . ')

    def test_a_typed_name_shows_who_it_may_already_be(self):
        raihan = self.Partner.create({'name': 'TT RAIHAN', 'is_technician': True})
        self._order(raihan)
        self.Partner.create({'name': 'TT RAIHANA', 'is_technician': True})
        contact = self.Partner.create({'name': 'TT SHIBU'})
        found = {m['id']: m for m in self.Partner.lab_technician_lookup('tt raihan')}
        self.assertTrue(found[raihan.id]['exact'])
        self.assertEqual(found[raihan.id]['orders'], 1)
        self.assertEqual(self.Partner.lab_technician_lookup('tt raihan')[0]['id'], raihan.id, "the exact one first")
        shibu = self.Partner.lab_technician_lookup('TT Shibu')
        self.assertEqual([(m['id'], m['technician']) for m in shibu], [(contact.id, False)],
                         "a contact spelled exactly so, not yet a technician")
        self.assertEqual(self.Partner.lab_technician_lookup('t'), [], "too short to say anything")
        contact.lab_mark_technician()
        self.assertTrue(contact.is_technician)

    def test_an_archived_technician_stays_on_the_order_and_cannot_be_picked(self):
        """(client, 2026-11-26: "show the archived technicians which are already selected
        in the SO; do not allow to select archived technicians in SO when selecting")"""
        still = self.Partner.create({'name': 'TT STILL HERE', 'is_technician': True})
        gone = self.Partner.create({'name': 'TT LEFT THE LAB', 'is_technician': True})
        order = self._order(still | gone)
        gone.action_archive()
        order.invalidate_recordset()
        self.assertEqual(order.technician_ids, still | gone, "the order still names both")
        # what the form reads
        names = [t['display_name'] for t in order.web_read(
            {'technician_ids': {'fields': {'display_name': {}}}})[0]['technician_ids']]
        self.assertEqual(sorted(names), ['TT LEFT THE LAB', 'TT STILL HERE'])
        # what the picker offers: the field's own domain and context, as the web client sends them
        field = self.env['sale.order']._fields['technician_ids']
        offered = self.Partner.with_context(**field.context).name_search(
            'TT ', domain=[('is_technician', '=', True), ('active', '=', True)], limit=20)
        offered_ids = [pid for pid, _name in offered]
        self.assertIn(still.id, offered_ids)
        self.assertNotIn(gone.id, offered_ids)
        # saving the order again keeps the archived one, and adding someone keeps it too
        other = self.Partner.create({'name': 'TT NEW HAND', 'is_technician': True})
        order.write({'technician_ids': [(4, other.id)]})
        self.assertEqual(order.technician_ids, still | gone | other)
        # the New button's lookup and creation do not hand an archived one back
        self.assertNotIn(gone.id, [m['id'] for m in self.Partner.lab_technician_lookup('TT LEFT')])
        made = self.Partner.with_context(active_test=False).lab_create_technician('TT LEFT THE LAB')
        self.assertTrue(made['created'])
        self.assertNotEqual(made['id'], gone.id)

