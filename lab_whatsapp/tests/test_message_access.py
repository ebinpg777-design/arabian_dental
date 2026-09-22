# -*- coding: utf-8 -*-
"""The WhatsApp smart button, for someone who has never touched WhatsApp settings.

`whatsapp_message_count` is shown on every clinic and, via the mixin, every sale
order / invoice / picking / payment - to any user who can see that record at all,
not only WhatsApp: send / configure. Tapping it opened `epg.whatsapp.message`
directly, which those groups' ACL denied outright: "You are not allowed to access
'WhatsApp Message' records" - reported from the field via the mobile app
2026-08-29, the moment an ordinary field executive tapped the button on a clinic
they were standing in front of. The count computed fine (the mixin's own compute
already sudos); it was the CLICK-THROUGH that had nowhere to go. Read-only access
for every internal user closes that gap without handing out send/configure rights.
(client, 2026-08-29)
"""
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestWhatsappMessageAccess(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plain_user = cls.env['res.users'].create({
            'name': 'Plain Field User', 'login': 'plain.wa@test',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])]})
        cls.clinic = cls.env['res.partner'].create({
            'name': 'WA Access Clinic', 'phone': '9999999999'})
        cls.env['epg.whatsapp.message'].sudo().create({
            'partner_id': cls.clinic.id, 'res_model': 'res.partner',
            'res_id': cls.clinic.id, 'direction': 'outbound', 'number': '919999999999',
            'body': 'Test notification', 'state': 'sent'})

    def test_an_ordinary_user_is_not_in_either_whatsapp_group(self):
        """The fixture proves the fix, not a user who happens to already have rights."""
        self.assertFalse(self.plain_user.has_group('epg_whatsapp.group_whatsapp_user'))
        self.assertFalse(self.plain_user.has_group('epg_whatsapp.group_whatsapp_admin'))

    def test_the_smart_button_action_no_longer_dead_ends(self):
        action = self.clinic.with_user(self.plain_user).action_whatsapp_messages()
        messages = self.env['epg.whatsapp.message'].with_user(self.plain_user).search(
            action['domain'])
        self.assertEqual(len(messages), 1, "the click-through must actually open, "
                                           "not raise an AccessError")

    def test_an_ordinary_user_still_cannot_write_or_configure(self):
        message = self.env['epg.whatsapp.message'].sudo().search(
            [('partner_id', '=', self.clinic.id)], limit=1)
        from odoo.exceptions import AccessError
        with self.assertRaises(AccessError):
            message.with_user(self.plain_user).write({'body': 'tampered'})
        with self.assertRaises(AccessError):
            self.env['epg.whatsapp.template'].with_user(self.plain_user).search([])

    def test_the_mixin_button_on_a_sale_order_also_opens(self):
        """The same mixin backs sale.order / account.move / stock.picking /
        account.payment - one fix, four models."""
        product = self.env['product.product'].create({
            'name': 'WA Access Product', 'type': 'consu', 'list_price': 10})
        order = self.env['sale.order'].create({
            'partner_id': self.clinic.id,
            'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': 1})]})
        action = order.with_user(self.plain_user).action_view_whatsapp()
        # must not raise - the domain resolves under the plain user's own rights
        self.env['epg.whatsapp.message'].with_user(self.plain_user).search(
            action['domain'])
