# -*- coding: utf-8 -*-
"""What KIND of appliance a case is. (client, 2026-09-09)"""
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestApplianceType(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clinic = cls.env['res.partner'].create({'name': 'Appliance Clinic'})
        cls.product = cls.env['product.product'].create({
            'name': 'Appliance Product', 'type': 'consu', 'list_price': 700.0})

    def _order(self, **vals):
        return self.env['sale.order'].create(dict({
            'partner_id': self.clinic.id, 'patient': 'Appliance Patient',
            'order_line': [(0, 0, {'product_id': self.product.id,
                                   'product_uom_qty': 1})]}, **vals))

    def test_the_four_words_the_lab_uses(self):
        selection = dict(self.env['sale.order']._fields['appliance_type']
                         ._description_selection(self.env))
        self.assertEqual(list(selection), ['fixed', 'removable', 'clear_retainer', 'other'])
        self.assertEqual(list(selection.values()),
                         ['Fixed', 'Removable', 'Clear Retainer', 'Other'])

    def test_it_is_empty_until_somebody_says(self):
        """No default: an unanswered question must not read as 'Fixed'."""
        self.assertFalse(self._order().appliance_type)

    def test_it_is_kept_and_can_be_changed(self):
        """The field carries `tracking=True` like the clinical fields beside it.

        Not asserted here: this database posts no tracking message for a change
        on a draft order - core's own tracked fields on sale.order behave the
        same way in a plain transaction - so a test for the note would be a test
        of the mail framework's configuration, not of this field.
        """
        order = self._order(appliance_type='clear_retainer')
        self.assertEqual(order.appliance_type, 'clear_retainer')
        order.appliance_type = 'removable'
        self.env.flush_all()
        order.invalidate_recordset(['appliance_type'])
        self.assertEqual(order.appliance_type, 'removable')
        self.assertTrue(self.env['sale.order']._fields['appliance_type'].tracking)

    def test_the_form_asks_for_it_while_the_order_is_a_draft(self):
        arch = self.env['sale.order'].get_view(
            self.env.ref('sale.view_order_form').id, view_type='form')['arch']
        self.assertIn('name="appliance_type"', arch)
        self.assertIn("required=\"state == 'draft'\"", arch)

    def test_a_copy_keeps_the_kind(self):
        """A repeat case is the same kind of appliance; retyping it invites a wrong one."""
        order = self._order(appliance_type='fixed')
        self.assertEqual(order.copy().appliance_type, 'fixed')

    def test_the_desk_can_group_by_it(self):
        """'How many clear retainers last month' is the question it exists for."""
        self._order(appliance_type='clear_retainer')
        self._order(appliance_type='fixed')
        groups = self.env['sale.order']._read_group(
            [('partner_id', '=', self.clinic.id)], ['appliance_type'], ['__count'])
        kinds = {kind: count for kind, count in groups}
        self.assertEqual(kinds.get('clear_retainer'), 1)
        self.assertEqual(kinds.get('fixed'), 1)
