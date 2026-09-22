# -*- coding: utf-8 -*-
"""Whose messages are whose, and the Desk surviving a contact you cannot read.
(client, 2026-09-19)

Two faults reported together, with one root between them. There were no record
rules on the message at all, so anybody holding "WhatsApp: send" could read
every message the lab had ever sent for every doctor. And the Desk rendered
`message.partner_id.display_name` under the reader's own rights, so the moment
one message in view was addressed to a clinic that reader cannot see - a field
executive sees clinics on their route, visited, or carried to, and no others -
the whole screen died with an AccessError instead of showing anything at all.
That was LATHEESH (id=64) on the live site, on the top-bar WhatsApp button.
"""
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

from .test_link_channel import LinkSetup


@tagged('post_install', '-at_install')
class TestDeskAccess(LinkSetup, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_link()
        cls.Desk = cls.env['epg.whatsapp.desk']
        base = cls.env.ref('base.group_user').id
        sender = cls.env.ref('epg_whatsapp.group_whatsapp_user').id
        configurer = cls.env.ref('epg_whatsapp.group_whatsapp_admin').id
        cls.alice = cls.env['res.users'].create({
            'name': 'Alice Sender', 'login': 'wa_alice',
            'group_ids': [(6, 0, [base, sender])]})
        cls.bob = cls.env['res.users'].create({
            'name': 'Bob Sender', 'login': 'wa_bob',
            'group_ids': [(6, 0, [base, sender])]})
        cls.boss = cls.env['res.users'].create({
            'name': 'Wanda Configurer', 'login': 'wa_boss',
            'group_ids': [(6, 0, [base, configurer])]})

    def _message(self, user=None, partner=None, sent_by=None):
        message = self.template.send(partner or self.partner)
        message.write({'user_id': user.id if user else False,
                       'sent_by_id': sent_by.id if sent_by else False})
        return message

    # ------------------------------------------------------------ whose is whose
    def test_a_sender_does_not_read_another_senders_message(self):
        mine, theirs = self._message(user=self.alice), self._message(user=self.bob)
        visible = self.env['epg.whatsapp.message'].with_user(self.alice).search([])
        self.assertIn(mine, visible)
        self.assertNotIn(theirs, visible, "Bob's message is not Alice's to read")

    def test_a_sender_reads_what_they_actually_sent(self):
        """Assigned away afterwards, but they are the one who sent it."""
        sent = self._message(user=self.bob, sent_by=self.alice)
        self.assertIn(sent, self.env['epg.whatsapp.message'].with_user(self.alice).search([]))

    def test_the_unassigned_queue_stays_everybodys(self):
        """user_id empty means "anybody with WhatsApp rights" - the shared queue.
        A rule that hid it would leave messages nobody could see to send."""
        loose = self._message(user=None)
        for who in (self.alice, self.bob):
            self.assertIn(loose, self.env['epg.whatsapp.message'].with_user(who).search([]))

    def test_configuring_reads_every_message(self):
        everyones = self._message(user=self.alice) | self._message(user=self.bob)
        visible = self.env['epg.whatsapp.message'].with_user(self.boss).search([])
        self.assertLessEqual(set(everyones.ids), set(visible.ids))

    def test_only_somebody_who_can_see_all_is_offered_Everyone(self):
        self.assertFalse(self.Desk.with_user(self.alice).get_desk()['can_see_all'])
        self.assertTrue(self.Desk.with_user(self.boss).get_desk()['can_see_all'])

    # ------------------------------------------- the contact you cannot read
    def test_the_desk_survives_a_contact_the_reader_cannot_see(self):
        """The reported crash. A rule the reader fails on res.partner, and a
        message of theirs pointing at it: the Desk must still open."""
        secret = self.env['res.partner'].create(
            {'name': 'Unreadable Clinic', 'phone': '+91 98765 22222'})
        self.env['ir.rule'].create({
            'name': 'TEST: Alice sees no partner named Unreadable',
            'model_id': self.env.ref('base.model_res_partner').id,
            'domain_force': "[('name', '!=', 'Unreadable Clinic')]",
            'groups': [(6, 0, [self.env.ref('epg_whatsapp.group_whatsapp_user').id])],
        })
        # Built directly, NOT through the res.partner template the rest of this
        # file uses. That template's linked record IS the contact, so the Desk
        # prefetches it with sudo and every later read comes back from cache -
        # which made an earlier version of this test pass with the fix taken out.
        # A real message points at a case, an invoice or nothing, and the contact
        # is then read for the first time in _card. (client, 2026-09-19)
        self.env['epg.whatsapp.message'].create({
            'partner_id': secret.id, 'account_id': self.account.id,
            'number': '+919876522222', 'direction': 'outbound', 'state': 'ready',
            'body': 'Your work is ready.', 'user_id': self.alice.id})

        # The ORM cache is per-TRANSACTION, not per-user: a field already fetched
        # as root is handed to Alice's environment without the rule being looked
        # at again, so everything must be unread before the Desk is asked.
        self.env.invalidate_all()
        with self.assertRaises(AccessError, msg="the rule really does bite"):
            self.env['res.partner'].with_user(self.alice).browse(secret.id).display_name
        self.env.invalidate_all()

        desk = self.Desk.with_user(self.alice).get_desk()
        groups = desk['groups']
        groups = list(groups.values()) if isinstance(groups, dict) else groups
        names = [g['partner'] for g in groups]
        names += [c['partner'] for g in groups for c in g['cards']]
        names += [c['partner'] for c in desk['later'] + desk['recent']]
        self.assertIn('Unreadable Clinic', names,
                      "the name is part of a message Alice is entitled to see")
