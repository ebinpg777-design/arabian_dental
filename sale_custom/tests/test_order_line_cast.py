# -*- coding: utf-8 -*-
"""Cast on the order line: U, L or UL, and nothing at all. (client, 2026-09-18)

Which cast came with the case, beside the arch the appliance is for - an upper
appliance is often made on both casts, so the two are separate questions.
"""
from datetime import timedelta

from lxml import etree

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestOrderLineCast(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clinic = cls.env['res.partner'].create({'name': 'Cast Clinic'})
        cls.product = cls.env['product.product'].create({
            'name': 'Cast Appliance', 'type': 'consu', 'list_price': 100.0})

    def _line(self, **vals):
        order = self.env['sale.order'].create({
            'partner_id': self.clinic.id,
            'order_line': [(0, 0, dict(vals, product_id=self.product.id))]})
        return order.order_line

    def test_the_three_options_and_none(self):
        self.assertEqual(
            dict(self.env['sale.order.line']._fields['cast'].selection),
            {'upper': 'U', 'lower': 'L', 'ul': 'UL'})
        self.assertFalse(self._line().cast, "optional: an empty cast is fine")
        for key in ('upper', 'lower', 'ul'):
            self.assertEqual(self._line(cast=key).cast, key)

    def test_the_cast_is_its_own_question_beside_the_arch(self):
        line = self._line(ul='upper', cast='ul')
        self.assertEqual((line.ul, line.cast), ('upper', 'ul'),
                         "an upper appliance made on both casts")

    def test_it_is_on_the_order_form(self):
        arch = etree.fromstring(self.env['sale.order'].get_view(
            self.env.ref('sale.view_order_form').id, 'form')['arch'])
        for path in ("//field[@name='order_line']/list/field[@name='cast']",
                     "//field[@name='order_line']/form//field[@name='cast']"):
            self.assertTrue(arch.xpath(path), path)


@tagged('post_install', '-at_install')
class TestDueDate(TransactionCase):
    """The day the lab owes the work, and the list of what is owed and unbilled.

    Two days from registration, the same day for an emergency - the promise the
    counter makes, written down so it can be chased. (client, 2026-09-18)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clinic = cls.env['res.partner'].create({'name': 'Due Date Clinic'})
        cls.product = cls.env['product.product'].create({
            'name': 'Due Date Appliance', 'type': 'consu', 'list_price': 500.0,
            'invoice_policy': 'order'})

    def _order(self, **vals):
        return self.env['sale.order'].create(dict({
            'partner_id': self.clinic.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 1,
                                   'tax_ids': [(6, 0, [])]})]}, **vals))

    def test_an_ordinary_case_is_owed_in_two_days(self):
        order = self._order()
        self.assertFalse(order.date_due, "nothing is promised until it is confirmed")
        order.action_confirm()
        today = fields.Date.context_today(order)
        self.assertEqual(order.date_due, today + timedelta(days=2))
        self.assertEqual(order.days_overdue, -2, "two days in hand")

    def test_urgent_work_is_owed_the_same_day(self):
        order = self._order(priority='urgent')
        order.action_confirm()
        self.assertEqual(order.date_due, fields.Date.context_today(order))
        # An emergency line says the same thing as the priority does.
        other = self._order()
        other.order_line.is_urgent = True
        other.action_confirm()
        self.assertEqual(other.date_due, fields.Date.context_today(other))

    def test_a_date_somebody_agreed_is_never_overwritten(self):
        agreed = fields.Date.context_today(self.env['sale.order']) + timedelta(days=9)
        order = self._order(date_due=agreed)
        order.action_confirm()
        self.assertEqual(order.date_due, agreed, "what the doctor was told stands")

    def test_due_for_invoicing_lists_what_is_owed_and_unbilled(self):
        today = fields.Date.context_today(self.env['sale.order'])
        due = self._order(); due.action_confirm(); due.date_due = today - timedelta(days=1)
        later = self._order(); later.action_confirm()      # due in two days
        billed = self._order(); billed.action_confirm()
        billed.date_due = today
        billed._create_invoices().action_post()
        action = self.env.ref('sale_custom.action_orders_due_for_invoicing')
        domain = self.env['ir.actions.act_window'].browse(action.id).domain
        found = self.env['sale.order'].search(
            eval(domain, {'context_today': lambda: today}))     # noqa: S307
        self.assertIn(due, found, "its day has come and nobody has billed it")
        self.assertNotIn(later, found, "not due yet")
        self.assertNotIn(billed, found, "already invoiced")
        self.assertEqual(due.days_overdue, 1)
        # And the search knows how to ask for it the other way round.
        self.assertIn(due, self.env['sale.order'].search([('days_overdue', '>=', 1)]))
        self.assertNotIn(later, self.env['sale.order'].search([('days_overdue', '>=', 1)]))

    def test_reworks_are_filtered_out_where_a_person_can_see_it(self):
        """A rework is unbilled for ever; left in, it drowns the cases somebody can
        act on. Filtered by default, but as a facet in the search bar rather than
        buried in the domain. (client, 2026-09-18)"""
        action = self.env.ref('sale_custom.action_orders_due_for_invoicing')
        self.assertIn('search_default_no_rework', action.context)
        self.assertNotIn('is_rework', action.domain, "not hidden in the domain")
        arch = self.env['sale.order'].get_view(
            self.env.ref('sale_custom.view_order_due_search').id, 'search')['arch']
        self.assertIn('name="no_rework"', arch)
        self.assertIn('name="rework_only"', arch, "and the other way round is one click")

    def test_the_menu_is_there(self):
        menu = self.env.ref('sale_custom.menu_orders_due_for_invoicing')
        self.assertEqual(menu.action.id, self.env.ref(
            'sale_custom.action_orders_due_for_invoicing').id)
        # On the Sales menu itself, not inside Orders. (client, 2026-09-19)
        self.assertEqual(menu.parent_id, self.env.ref('sale.sale_menu_root'))
