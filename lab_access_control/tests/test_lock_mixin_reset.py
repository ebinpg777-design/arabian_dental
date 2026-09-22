# -*- coding: utf-8 -*-
"""A reset may clear what a closed record recorded, and nothing more.

A write that moved a record out of a locked state used to skip the guard for EVERY
field in the same write, so ``write({'state': 'draft', 'odo_end': 9999})`` rewrote an
approved trip's reading in one stroke. (2026-09-15)

No model in this module's dependencies uses the mixin, and a library's tests must not
name a consumer's models (see lab_fieldwork/tests/test_lock_mixin.py for the same
rule tested against a real visit). So res.partner is borrowed for the length of each
test: the mixin's methods are patched onto it with ``type`` as the state field and
'delivery' as the locked state. The check runs exactly as ``write`` runs it.
"""
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.lab_access_control.models.lab_lock_mixin import (
    LOCK_RESET, LOCK_RESET_KEY, LabLockMixin)


@tagged('post_install', '-at_install')
class TestLockResetGuard(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clerk = cls.env['res.users'].create({
            'name': 'Lock Reset Clerk', 'login': 'lock_reset_clerk',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('base.group_partner_manager').id])]})
        cls.company = cls.env['res.partner'].create(
            {'name': 'Lock Reset Parent', 'is_company': True})
        cls.closed = cls.env['res.partner'].create({
            'name': 'Closed Address', 'parent_id': cls.company.id,
            'type': 'delivery', 'ref': 'EVIDENCE-1'})

    def setUp(self):
        super().setUp()
        mixin = {name: getattr(LabLockMixin, name) for name in (
            '_LOCK_ALWAYS_ALLOWED', '_lock_is_bypassed', '_lock_exempt_fields_for',
            '_lock_guarded_fields', '_lock_locked_records', '_lock_check_write')}
        patcher = patch.multiple(
            type(self.env['res.partner']), create=True,
            _lock_states=('delivery',), _lock_state_field='type',
            _lock_exempt_fields=('comment',), _lock_bypass_groups=(), **mixin)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.as_clerk = self.closed.with_user(self.clerk)

    def test_the_fixture_is_locked_for_the_clerk(self):
        self.assertFalse(self.as_clerk._lock_is_bypassed())
        with self.assertRaises(UserError):
            self.as_clerk._lock_check_write({'ref': 'EDITED'})

    def test_a_reset_cannot_set_a_value_in_the_same_write(self):
        """The finding: the state change used to wave every other field through."""
        with self.assertRaises(UserError) as caught:
            self.as_clerk._lock_check_write({'type': 'contact', 'ref': 'EDITED'})
        self.assertIn('Reference', str(caught.exception))

    def test_a_reset_may_clear_what_the_closed_state_recorded(self):
        """Clearing is what a reset is for: cheque.action_draft clears its dates,
        visit.action_reset clears check-in, check-out and the GPS fix."""
        self.as_clerk._lock_check_write({'type': 'contact', 'ref': False})
        self.as_clerk._lock_check_write({'type': 'contact', 'partner_latitude': 0.0})

    def test_the_state_alone_still_moves(self):
        self.as_clerk._lock_check_write({'type': 'contact'})

    def test_an_exempt_field_may_be_set_during_a_reset(self):
        self.as_clerk._lock_check_write({'type': 'contact', 'comment': '<p>why</p>'})

    def test_moving_between_two_locked_states_is_not_a_reset(self):
        """Only leaving the locked states counts: a clear inside them is still an edit."""
        with patch.object(type(self.env['res.partner']), '_lock_states',
                          ('delivery', 'invoice')):
            with self.assertRaises(UserError):
                self.as_clerk._lock_check_write({'type': 'invoice', 'ref': False})

    def test_a_reset_from_server_code_may_set_values(self):
        self.as_clerk.with_context(**{LOCK_RESET_KEY: LOCK_RESET})._lock_check_write(
            {'type': 'contact', 'ref': 'REOPENED'})

    def test_a_client_cannot_forge_the_reset_flag(self):
        """An RPC context is JSON: the best a client can send is a string or True."""
        for forged in (True, 'LOCK_RESET', 1):
            with self.assertRaises(UserError):
                self.as_clerk.with_context(**{LOCK_RESET_KEY: forged})._lock_check_write(
                    {'type': 'contact', 'ref': 'FORGED'})

    def test_the_supervising_role_is_not_restricted(self):
        self.closed._lock_check_write({'type': 'contact', 'ref': 'SUPERUSER'})
        with patch.object(type(self.env['res.partner']), '_lock_bypass_groups',
                          ('base.group_partner_manager',)):
            self.as_clerk._lock_check_write({'type': 'contact', 'ref': 'MANAGER'})

    def test_write_runs_the_check(self):
        """The mixin's write must go through the check, or none of the above holds."""
        calls = []
        with patch.object(LabLockMixin, '_lock_check_write',
                          lambda self, vals: calls.append(vals)), \
                patch('odoo.models.BaseModel.write', lambda self, vals: True):
            LabLockMixin.write(self.env['lab.lock.mixin'], {'state': 'x'})
        self.assertEqual(calls, [{'state': 'x'}])
