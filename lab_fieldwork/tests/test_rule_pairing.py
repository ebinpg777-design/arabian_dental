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
"""
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestRulePairing(TransactionCase):

    def test_every_executive_rule_has_a_manager_twin(self):
        executive = self.env.ref('lab_fieldwork.group_fieldwork_executive')
        manager = self.env.ref('lab_fieldwork.group_fieldwork_manager')
        Rule = self.env['ir.rule'].sudo()

        exec_models = {
            r.model_id.model
            for r in Rule.search([('groups', 'in', executive.id)])
            # A rule that already lets everything through is not a restriction.
            if r.domain_force and r.domain_force.strip() not in ("[(1, '=', 1)]",
                                                                 '[(1, "=", 1)]')
        }
        manager_models = {
            r.model_id.model
            for r in Rule.search([('groups', 'in', manager.id)])
        }
        missing = sorted(exec_models - manager_models)
        self.assertFalse(missing, (
            "These models restrict the field-work Executive with no permissive "
            "Manager rule to OR against, so anyone holding both roles gets the "
            "restriction and nothing else: %s" % ', '.join(missing)))

    def test_a_supervisor_who_is_also_an_executive_is_not_restricted(self):
        """The concrete case: the login that runs the lab holds every role."""
        admin = self.env.ref('base.user_admin')
        groups = [self.env.ref('lab_fieldwork.group_fieldwork_executive').id,
                  self.env.ref('lab_fieldwork.group_fieldwork_manager').id]
        admin.sudo().write({'group_ids': [(4, g) for g in groups]})
        admin.invalidate_recordset()

        for model in ('sale.order', 'res.partner'):
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
