# -*- coding: utf-8 -*-
from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestLockMixinOnVisit(TransactionCase):
    """The state lock, tested against a real locked model.

    These live here rather than in lab_access_control: a library module's tests
    must not reference a consumer's models, or uninstalling the consumer breaks
    the library's suite — which is exactly what happened.

    The lock has to hold in write(), because that is the only place a list-view
    edit, an import and an RPC call all pass through."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Visit = cls.env['lab.visit']
        cls.partner = cls.env['res.partner'].create(
            {'name': 'Lock Test Clinic', 'is_clinic': True})
        cls.junior = cls.env['res.users'].create({
            'name': 'Junior Executive', 'login': 'lock_junior',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('lab_fieldwork.group_fieldwork_executive').id,
            ])],
        })
        cls.manager = cls.env['res.users'].create({
            'name': 'Field Manager', 'login': 'lock_manager',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('lab_fieldwork.group_fieldwork_manager').id,
            ])],
        })
        # visit.user_id is related to employee_id.user_id, and the executive record
        # rule is [('user_id','=',user.id)] — link the employee to the junior or the
        # rule (correctly) denies the write before the lock is ever consulted.


    def _visit(self, state='planned'):
        visit = self.Visit.create({
            'partner_id': self.partner.id, 'user_id': self.junior.id})
        if state != 'planned':
            visit.state = state
        return visit

    def test_open_record_is_editable(self):
        visit = self._visit()
        visit.with_user(self.junior).purpose = 'payment'
        self.assertEqual(visit.purpose, 'payment')

    def test_closed_record_blocks_business_field(self):
        """The whole point: a completed visit must not accept a silent edit."""
        visit = self._visit('done')
        with self.assertRaises(UserError):
            visit.with_user(self.junior).write({'purpose': 'complaint'})

    def test_cancelled_record_blocks_business_field(self):
        visit = self._visit('cancel')
        with self.assertRaises(UserError):
            visit.with_user(self.junior).write({'purpose': 'complaint'})

    def test_exempt_field_still_writable_when_locked(self):
        visit = self._visit('done')
        visit.with_user(self.junior).write({'note': 'added after the visit closed'})
        self.assertEqual(visit.note, 'added after the visit closed')

    def test_state_itself_is_always_writable(self):
        """Workflow buttons must still be able to move a locked record."""
        visit = self._visit('done')
        visit.with_user(self.junior).write({'state': 'planned'})
        self.assertEqual(visit.state, 'planned')

    def test_unlock_and_reset_is_one_operation(self):
        """Leaving the locked state may clear the stamps that state left behind."""
        visit = self._visit('done')
        visit.check_out = fields.Datetime.now()
        visit.with_user(self.junior).write({'state': 'planned', 'check_out': False})
        self.assertEqual(visit.state, 'planned')
        self.assertFalse(visit.check_out)

    def test_leaving_the_locked_state_cannot_carry_an_edit(self):
        """Setting a value in the same write as the unlock would be an edit to a
        completed visit dressed up as a reset."""
        visit = self._visit('done')
        with self.assertRaises(UserError):
            visit.with_user(self.junior).write({'state': 'planned', 'purpose': 'round'})

    def test_bypass_group_may_edit_locked_record(self):
        visit = self._visit('done')
        visit.with_user(self.manager).write({'purpose': 'complaint'})
        self.assertEqual(visit.purpose, 'complaint')

    def test_chatter_still_works_on_locked_record(self):
        """Guarding framework fields would break the chatter without protecting
        anything, so message_post must keep working."""
        visit = self._visit('done')
        visit.with_user(self.junior).message_post(body='still auditable')
        self.assertTrue(visit.message_ids)

    def test_lock_survives_multi_record_write(self):
        """A mixed batch must not let a locked record through on the coat-tails of an
        open one."""
        open_visit, closed_visit = self._visit(), self._visit('done')
        batch = (open_visit | closed_visit).with_user(self.junior)
        with self.assertRaises(UserError):
            batch.write({'purpose': 'complaint'})


@tagged('post_install', '-at_install')
class TestCompletedVisitStillRecordsWhatHappened(TransactionCase):
    """A visit is checked out at the counter with the doctor still talking, and
    the cases, the outcome and the money are often settled a minute later. By
    then it is Completed, and the whole record used to be frozen - so the day's
    figures were wrong and only a manager could fix them. A CANCELLED visit
    stays frozen: nothing happened at a visit that did not happen.
    (client, 2026-09-12)"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Visit = cls.env['lab.visit']
        cls.partner = cls.env['res.partner'].create(
            {'name': 'Late Entry Clinic', 'is_clinic': True})
        cls.junior = cls.env['res.users'].create({
            'name': 'Late Entry Executive', 'login': 'late_junior',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('lab_fieldwork.group_fieldwork_executive').id,
            ])],
        })
        # A SHIPPED tag, not an invented one: the chips write the primary tag's
        # code into the visit's `outcome` selection, and a code that is not one
        # of its keys is refused by the ORM.
        cls.outcome = cls.env.ref('lab_fieldwork.outcome_order')

    def _visit(self, state):
        visit = self.Visit.create({
            'partner_id': self.partner.id, 'user_id': self.junior.id})
        visit.state = state
        return visit

    def test_the_money_and_the_outcome_go_in_after_completing(self):
        visit = self._visit('done').with_user(self.junior)
        visit.write({'cases_counted': 3, 'collected': 750.0, 'pay_mode': 'cash',
                     'outcome_ids': [(6, 0, self.outcome.ids)]})
        self.assertEqual((visit.cases_counted, visit.collected, visit.pay_mode),
                         (3, 750.0, 'cash'))
        self.assertEqual(visit.outcome_ids, self.outcome)

    def test_the_rest_of_a_completed_visit_is_still_frozen(self):
        visit = self._visit('done').with_user(self.junior)
        with self.assertRaises(UserError):
            visit.write({'purpose': 'complaint'})
        with self.assertRaises(UserError):
            visit.write({'date': fields.Date.to_date('2026-01-01')})

    def test_a_cancelled_visit_records_nothing(self):
        visit = self._visit('cancel').with_user(self.junior)
        for vals in ({'collected': 500.0}, {'cases_counted': 2},
                     {'outcome_ids': [(6, 0, self.outcome.ids)]}):
            with self.assertRaises(UserError):
                visit.write(vals)

    def test_the_note_stays_writable_in_both_closed_states(self):
        for state in ('done', 'cancel'):
            visit = self._visit(state).with_user(self.junior)
            visit.write({'note': 'added afterwards'})
            self.assertEqual(visit.note, 'added afterwards')

    def test_the_banner_says_what_can_still_be_recorded(self):
        done = self._visit('done').with_user(self.junior)
        self.assertIn('can still be recorded', done.lock_message)
        cancelled = self._visit('cancel').with_user(self.junior)
        self.assertIn('can no longer be edited', cancelled.lock_message)

    def test_the_form_lets_those_fields_be_typed_on_a_completed_visit(self):
        """The server rule and the form must agree: a field the mixin now allows
        must not still be rendered read-only on a completed visit."""
        arch = self.env.ref('lab_fieldwork.view_visit_form').arch_db
        for field in ('cases_counted', 'outcome_ids', 'reworks_collected',
                      'collected', 'pay_mode'):
            block = arch.split('name="%s"' % field, 1)
            self.assertEqual(len(block), 2, "%s is on the form" % field)
            following = block[1][:260]
            self.assertNotIn('readonly="state == \'done\'"', following,
                             "%s must be editable once the visit is completed" % field)
