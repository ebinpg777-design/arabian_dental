# -*- coding: utf-8 -*-
"""Keep the route-based record rules honest when a route changes hands.

The partner and order rules confine an executive through ``user.fw_route_ids`` -
the routes they lead or belong to. Odoo evaluates a rule's domain once per user and
caches the result (``ir.rule._compute_domain`` is ormcached on uid/model/mode), so a
manager moving an executive from KLM to GNRL used to change nothing on the
executive's screen: they kept KLM's 529 clinics and saw none of GNRL's until the
server's cache happened to clear. Verified on the live database, 2026-08-24.

The cache is flushed here, on the one write that changes the answer. Registry-wide
rather than surgical because ir.rule's cache has no per-user key to evict, and a
route reassignment is a rare, deliberate act - the cost of rebuilding the cache is
nothing next to an executive working the wrong round for a day.
"""
from odoo import api, models

ROUTE_PEOPLE_FIELDS = ('user_id', 'member_ids', 'crm_team_member_ids')


class CrmTeam(models.Model):
    _inherit = 'crm.team'

    def _fw_clear_rule_cache(self):
        self.env.registry.clear_cache()

    def write(self, vals):
        res = super().write(vals)
        if any(f in vals for f in ROUTE_PEOPLE_FIELDS):
            self._fw_clear_rule_cache()
        return res

    @api.model_create_multi
    def create(self, vals_list):
        teams = super().create(vals_list)
        if any(any(f in vals for f in ROUTE_PEOPLE_FIELDS) for vals in vals_list):
            teams._fw_clear_rule_cache()
        return teams

    def unlink(self):
        res = super().unlink()
        self._fw_clear_rule_cache()
        return res
