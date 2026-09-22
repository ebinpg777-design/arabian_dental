# -*- coding: utf-8 -*-
from odoo.exceptions import ValidationError
from odoo.tests import Form, tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestRework(TransactionCase):
    """A rework is a sale order with Rework ticked - nothing else."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clinic = cls.env['res.partner'].create({'name': 'Rework Clinic'})
        cls.product = cls.env['product.product'].create({
            'name': 'Twin block', 'type': 'consu', 'list_price': 500, 'invoice_policy': 'order'})
        cls.reason = cls.env['lab.rework.reason'].search([], limit=1) or cls.env['lab.rework.reason'].create(
            {'name': 'Fit issue', 'responsibility_default': 'fit_issue'})

    def _order(self, **kw):
        vals = {'partner_id': self.clinic.id,
                'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 2,
                                       'price_unit': 500})]}
        vals.update(kw)
        return self.env['sale.order'].create(vals)

    def test_the_form_requires_a_reason_when_rework_is_ticked(self):
        """Enforced by the form, not by a constraint: legacy reworks carry no reason and
        must stay editable."""
        from lxml import etree
        arch = etree.fromstring(self.env['sale.order'].get_view(view_type='form')['arch'])
        node = arch.xpath("//field[@name='rework_reason_id']")[0]
        self.assertEqual(node.get('required'), 'is_rework')
        self.assertEqual(node.get('invisible'), 'not is_rework')
        legacy = self._order(is_rework=True)          # no reason: allowed, as for migrated data
        legacy.patient = 'EDIT LATER'                 # and still editable
        self.assertTrue(legacy.is_rework)

    def test_an_order_cannot_be_a_rework_of_itself_or_of_a_rework(self):
        origin = self._order(); origin.action_confirm()
        rework = self._order(is_rework=True, rework_reason_id=self.reason.id, rework_origin_id=origin.id)
        with self.assertRaises(ValidationError):
            rework.rework_origin_id = rework
        second = self._order(is_rework=True, rework_reason_id=self.reason.id)
        with self.assertRaises(ValidationError):
            second.rework_origin_id = rework

    def test_original_order_is_optional(self):
        rework = self._order(is_rework=True, rework_reason_id=self.reason.id)
        self.assertFalse(rework.rework_origin_id)
        self.assertTrue(rework.name.startswith('RE'), "reworks keep the RE numbering")

    def test_picking_the_original_fills_the_case_in(self):
        origin = self._order(patient='RAVI', appliance_type='fixed')
        origin.action_confirm()
        form = Form(self.env['sale.order'])
        form.is_rework = True
        form.rework_reason_id = self.reason
        form.rework_origin_id = origin
        rework = form.save()
        self.assertEqual(rework.partner_id, origin.partner_id)
        self.assertEqual(rework.patient, 'RAVI')
        self.assertEqual(rework.appliance_type, 'fixed',
                         "a remake is the same kind of appliance as the job it remakes")
        self.assertEqual(len(rework.order_line), len(origin.order_line))
        self.assertEqual(rework.order_line.mapped('product_uom_qty'), [2.0])
        self.assertEqual(rework.amount_total, 0.0, "a remake is never charged")
        self.assertEqual(rework.rework_responsibility, self.reason.responsibility_default)

    def test_the_original_counts_its_reworks(self):
        origin = self._order()
        origin.action_confirm()
        self._order(is_rework=True, rework_reason_id=self.reason.id, rework_origin_id=origin.id)
        origin.invalidate_recordset()
        self.assertEqual(origin.rework_count, 1)
        action = origin.action_view_reworks()
        self.assertEqual(action['domain'], [('rework_origin_id', '=', origin.id)])

    def test_original_order_list_is_limited_to_the_customer(self):
        """The picker offers this clinic's own jobs, not everybody's."""
        mine = self._order(); mine.action_confirm()
        other_clinic = self.env['res.partner'].create({'name': 'Another Clinic'})
        theirs = self._order(partner_id=other_clinic.id); theirs.action_confirm()
        SO = self.env['sale.order']
        domain = [('is_rework', '=', False), ('id', '!=', 0), ('partner_id', '=?', self.clinic.id)]
        offered = SO.search(domain)
        self.assertIn(mine, offered)
        self.assertNotIn(theirs, offered)
        # and switching the clinic drops an original that no longer belongs to it
        form = Form(self.env['sale.order'])
        form.is_rework = True
        form.rework_reason_id = self.reason
        form.rework_origin_id = mine
        form.partner_id = other_clinic
        self.assertFalse(form.rework_origin_id)

    def test_create_rework_action_prefills(self):
        origin = self._order()
        origin.action_confirm()
        ctx = origin.action_create_rework()['context']
        self.assertTrue(ctx['default_is_rework'])
        self.assertEqual(ctx['default_rework_origin_id'], origin.id)

    def test_zero_value_rework_can_be_invoiced(self):
        origin = self._order(); origin.action_confirm()
        rework = self._order(is_rework=True, rework_reason_id=self.reason.id, rework_origin_id=origin.id)
        rework.order_line.write({'price_unit': 0.0})
        rework.action_confirm()
        invoice = rework._create_invoices()
        self.assertTrue(invoice)
        self.assertEqual(invoice.amount_total, 0.0)
