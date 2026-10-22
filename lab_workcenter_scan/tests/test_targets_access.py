# -*- coding: utf-8 -*-
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestTargetsAccess(TransactionCase):
    """Every technician's targets, read as superuser: the production report's, so for
    manufacturing users, like its menu. (2026-10-22)"""

    def test_the_targets_are_for_manufacturing_users(self):
        Users = self.env['res.users'].with_context(no_reset_password=True)
        base = self.env.ref('base.group_user')
        outsider = Users.create({'name': 'Targets Outsider', 'login': 'targets.outsider@test',
                                 'group_ids': [(6, 0, [base.id])]})
        planner = Users.create({'name': 'Targets MRP User', 'login': 'targets.mrp@test',
                                'group_ids': [(6, 0, [base.id, self.env.ref('mrp.group_mrp_user').id])]})
        Report = self.env['lab.mrp.report']
        with self.assertRaises(AccessError):
            Report.with_user(outsider).get_targets()
        self.assertIsNotNone(Report.with_user(planner).get_targets())
