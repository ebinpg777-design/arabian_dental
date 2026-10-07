# -*- coding: utf-8 -*-
"""Every executive restriction must have a supervisor twin.

Odoo OR-s record rules across a user's groups. A rule attached ONLY to the
Executive group is therefore not "a restriction on executives" - it is the whole
answer for anyone who holds that group, including a Manager or Administrator who
also holds it. Three separate outages on this project came from exactly that:

* res.partner  (2026-08-24) - the admin login saw 1 contact of 6,763;
* ir.ui.menu   (2026-08-24) - the same login saw one app of 21;
* sale.order   (2026-08-26) - the same login saw 0 orders of 20,988, because the
  executive rule scopes by `user.fw_route_ids` and an administrator works no route.

So this is a rule ABOUT the rules: if a model restricts the Executive group, it
must also carry a permissive rule for the Manager group. Administrator implies
Manager, so the one twin covers both.

Odoo 20 replaced ir.rule with ir.access. A row WITH a group is now a permission
(OR-ed), a row WITHOUT one a restriction (AND-ed for everybody). The executive
rows that only ever WIDENED (orders, floats, own visits) stay permissions and
keep their manager twin; the two that RESTRICTED (contacts, menus) are now
restrictions whose domain bites only for a plain executive - so the twin lives
inside the domain itself and is checked here as text.
"""
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestRulePairing(TransactionCase):

    def test_every_executive_rule_has_a_manager_twin(self):
        executive = self.env.ref('lab_fieldwork.group_fieldwork_executive')
        manager = self.env.ref('lab_fieldwork.group_fieldwork_manager')
        Access = self.env['ir.access'].sudo()
        everything = ("[(1, '=', 1)]", '[(1, "=", 1)]', '')

        exec_models = {
            a.model_id.model
            for a in Access.search([('group_id', '=', executive.id)])
            # A row that already lets everything through is not a restriction.
            if (a.domain or '').strip() not in everything
        }
        manager_models = {
            a.model_id.model
            for a in Access.search([('group_id', '=', manager.id)])
        }
        missing = sorted(exec_models - manager_models)
        self.assertFalse(missing, (
            "These models restrict the field-work Executive with no permissive "
            "Manager rule to OR against, so anyone holding both roles gets the "
            "restriction and nothing else: %s" % ', '.join(missing)))

        # The restrictions that only bite for an executive must let the three
        # supervising groups through - in their own domain, since nothing can be
        # OR-ed against a restriction.
        gated = Access.search([('group_id', '=', False),
                               ('domain', 'like', 'group_fieldwork_executive')])
        self.assertTrue(gated, "the contact and menu restrictions exist")
        for access in gated:
            for twin in ('lab_fieldwork.group_fieldwork_manager',
                         'base.group_system', 'base.group_erp_manager'):
                self.assertIn(twin, access.domain, (
                    "%s restricts executives but does not spare %s"
                    % (access.name, twin)))

    def test_a_supervisor_who_is_also_an_executive_is_not_restricted(self):
        """The concrete case: the login that runs the lab holds every role."""
        admin = self.env.ref('base.user_admin')
        groups = [self.env.ref('lab_fieldwork.group_fieldwork_executive').id,
                  self.env.ref('lab_fieldwork.group_fieldwork_manager').id]
        admin.sudo().write({'group_ids': [(4, g) for g in groups]})
        admin.invalidate_recordset()

        for model in ('sale.order', 'res.partner', 'ir.ui.menu'):
            if model not in self.env:
                continue
            everything = self.env[model].sudo().search_count([])
            if not everything:
                continue
            seen = self.env[model].with_user(admin).search_count([])
            self.assertEqual(seen, everything, (
                "a supervisor who also holds the Executive group sees only %s of "
                "%s %s records - the executive restriction is winning" % (
                    seen, everything, model)))
