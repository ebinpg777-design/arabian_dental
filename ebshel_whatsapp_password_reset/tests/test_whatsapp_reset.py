# Part of ebshel_whatsapp_password_reset. See LICENSE file for full copyright and licensing details.
from urllib.parse import unquote

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from ..models.res_users import international_digits


@tagged('post_install', '-at_install')
class TestWhatsappReset(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.india = cls.env.ref('base.in')
        cls.env.company.country_id = cls.india
        cls.admin = cls.env.ref('base.user_admin')
        cls.user = cls.env['res.users'].create({
            'name': 'Field Person', 'login': 'field_person', 'password': 'field_person_pw',
            'company_id': cls.env.company.id, 'company_ids': [(6, 0, cls.env.company.ids)]})
        cls.user.partner_id.phone = '98097 81772'

    def test_the_link_goes_to_whatsapp_with_the_users_number(self):
        action = self.user.with_user(self.admin).action_whatsapp_reset_password()
        self.assertEqual(action['type'], 'ir.actions.act_url')
        self.assertEqual(action['target'], 'new')
        url = action['url']
        self.assertTrue(url.startswith('https://wa.me/919809781772?text='), url)
        text = unquote(url.split('?text=', 1)[1])
        self.assertIn('Field Person', text)
        self.assertIn('field_person', text, "the login is in the message")
        self.assertIn('/web/reset_password?', text)
        self.assertIn('token=', text)
        # the chatter says it went - never the link
        note = self.user.message_ids[:1]
        self.assertIn('+919809781772', note.body)
        self.assertNotIn('token', note.body)

    def test_the_link_is_one_the_reset_page_accepts(self):
        url = self.user.with_user(self.admin).action_whatsapp_reset_password()['url']
        token = unquote(url).split('token=', 1)[1].split('&')[0].split('\n')[0]
        partner = self.env['res.partner']._signup_retrieve_partner(token, check_validity=True)
        self.assertEqual(partner, self.user.partner_id)

    def test_without_a_number_whatsapp_asks_whom_to_send_it_to(self):
        self.user.partner_id.phone = False
        url = self.user.with_user(self.admin).action_whatsapp_reset_password()['url']
        self.assertTrue(url.startswith('https://wa.me/?text='), url)

    def test_only_administrators_and_never_for_yourself_or_an_archived_user(self):
        other = self.env['res.users'].create({'name': 'Other', 'login': 'other_person'})
        with self.assertRaises(AccessError):
            self.user.with_user(other).action_whatsapp_reset_password()
        with self.assertRaises(UserError):
            self.admin.with_user(self.admin).action_whatsapp_reset_password()
        self.user.active = False
        with self.assertRaises(UserError):
            self.user.with_user(self.admin).action_whatsapp_reset_password()

    def test_numbers_are_written_the_way_whatsapp_wants_them(self):
        self.assertEqual(international_digits('+91 98097 81772', 91), '919809781772')
        self.assertEqual(international_digits('0091 9809781772', 91), '919809781772')
        self.assertEqual(international_digits('098097-81772', 91), '919809781772')
        self.assertEqual(international_digits('9809781772', 91), '919809781772')
        self.assertEqual(international_digits('919809781772', 91), '919809781772', "already with the code")
        self.assertEqual(international_digits('+965 6000 1234', 91), '96560001234', "a foreign number keeps its own")
        self.assertEqual(international_digits('12345', 91), '', "too short to be a number")
        self.assertEqual(international_digits('', 91), '')
        self.assertEqual(international_digits(None, 91), '')
