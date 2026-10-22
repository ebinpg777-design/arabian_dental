# -*- coding: utf-8 -*-
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestHabitsAccess(TransactionCase):
    """A practice's call habits are read from every call ever logged, as superuser:
    the desk's, not anybody's who asks over RPC. (2026-10-22)"""

    def test_call_habits_are_the_desks(self):
        clinic = self.env['res.partner'].create({'name': 'Habits Clinic'})
        Users = self.env['res.users'].with_context(no_reset_password=True)
        base = self.env.ref('base.group_user')
        outsider = Users.create({'name': 'Habits Outsider', 'login': 'habits.outsider@test',
                                 'group_ids': [(6, 0, [base.id])]})
        agent = Users.create({'name': 'Habits Agent', 'login': 'habits.agent@test',
                              'group_ids': [(6, 0, [base.id, self.env.ref(
                                  'lab_doctor_desk.group_doctor_desk_agent').id])]})
        Ticket = self.env['lab.doctor.ticket']
        with self.assertRaises(AccessError):
            Ticket.with_user(outsider).doctor_habits(clinic.id)
        self.assertEqual(Ticket.with_user(agent).doctor_habits(clinic.id)['calls'], 0)
