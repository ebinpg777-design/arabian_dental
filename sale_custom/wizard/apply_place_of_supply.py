# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ApplyPlaceOfSupply(models.TransientModel):
    _name = 'team.place.of.supply.apply'
    _description = 'Apply a Sales Team Place of Supply to its Customers'

    team_id = fields.Many2one('crm.team', required=True, readonly=True)
    state_id = fields.Many2one(related='team_id.place_of_supply_id', string='Place of Supply')
    mode = fields.Selection([
        ('missing', 'Customers without a state'),
        ('all', 'Every customer on the team'),
    ], required=True, default='missing')
    summary = fields.Char(compute='_compute_summary')

    @api.depends('team_id', 'mode')
    def _compute_summary(self):
        for wizard in self:
            team = wizard.team_id
            if not team.place_of_supply_id:
                wizard.summary = _("Set a place of supply on the team first.")
                continue
            count = team.place_of_supply_missing_count if wizard.mode == 'missing' \
                else team.place_of_supply_partner_count
            wizard.summary = _("%(count)s customer(s) will be put in %(state)s.",
                               count=count, state=team.place_of_supply_id.name)

    def action_apply(self):
        self.ensure_one()
        state = self.team_id.place_of_supply_id
        if not state:
            raise UserError(_("Set a place of supply on the team first."))
        partners = self.team_id._place_of_supply_partners(missing_only=self.mode == 'missing')
        india = self.env.ref('base.in')
        partners = partners.filtered(lambda p: not p.country_id or p.country_id == india)
        if partners:
            partners.with_context(place_of_supply_filling=True).write(
                {'state_id': state.id, 'country_id': india.id})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'message': _("%(count)s customer(s) put in %(state)s.",
                             count=len(partners), state=state.name),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
