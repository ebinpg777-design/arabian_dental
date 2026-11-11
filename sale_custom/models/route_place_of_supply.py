# -*- coding: utf-8 -*-
"""Place of supply on a Sales Route (client, 2026-11-11).

A route is a territory, so the GST state of its clinics is known from it. The route
carries it; a clinic put on the route with no state gets it; an invoice to a clinic
that still has none takes it instead of the lab's own state (Odoo's fallback, which
called every such sale intra-state and put Kerala on 9,995 invoices).
"""
from odoo import _, api, fields, models


class CrmTeam(models.Model):
    _inherit = 'crm.team'

    place_of_supply_id = fields.Many2one(
        'res.country.state', string='Place of Supply',
        domain="[('country_id.code', '=', 'IN')]",
        help="The state this team's customers are in. A customer put on the team with no "
             "state of their own gets it, and an invoice to a customer who still has none "
             "takes it as the GST place of supply.")
    place_of_supply_missing_count = fields.Integer(
        compute='_compute_place_of_supply_counts', string='Customers Without a State')
    place_of_supply_partner_count = fields.Integer(
        compute='_compute_place_of_supply_counts', string='Customers')

    def _place_of_supply_partners(self, missing_only=False):
        """The team's customers: every contact on the team, their contacts included."""
        self.ensure_one()
        domain = [('team_id', '=', self.id)]
        if missing_only:
            domain.append(('state_id', '=', False))
        return self.env['res.partner'].with_context(active_test=False).search(domain)

    def _compute_place_of_supply_counts(self):
        Partner = self.env['res.partner'].with_context(active_test=False)
        for team in self:
            team.place_of_supply_partner_count = Partner.search_count([('team_id', '=', team.id)])
            team.place_of_supply_missing_count = Partner.search_count(
                [('team_id', '=', team.id), ('state_id', '=', False)])

    def action_apply_place_of_supply(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Apply the Place of Supply'),
            'res_model': 'team.place.of.supply.apply',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_team_id': self.id},
        }


class ResPartner(models.Model):
    _inherit = 'res.partner'

    @api.model_create_multi
    def create(self, vals_list):
        partners = super().create(vals_list)
        partners._fill_place_of_supply()
        return partners

    def write(self, vals):
        res = super().write(vals)
        if 'team_id' in vals and not self.env.context.get('place_of_supply_filling'):
            self._fill_place_of_supply()
        return res

    def _fill_place_of_supply(self):
        """A contact on a team with a place of supply, and no state of its own, is in
        that state. Only the missing state is filled: a state somebody set stays."""
        india = self.env.ref('base.in', raise_if_not_found=False)
        for partner in self.filtered(lambda p: not p.state_id and p.team_id.place_of_supply_id):
            if partner.country_id and india and partner.country_id != india:
                continue
            vals = {'state_id': partner.team_id.place_of_supply_id.id}
            if india and not partner.country_id:
                vals['country_id'] = india.id
            partner.with_context(place_of_supply_filling=True).write(vals)


class AccountMove(models.Model):
    _inherit = 'account.move'

    @api.depends('partner_id', 'partner_shipping_id', 'company_id')
    def _compute_l10n_in_state_id(self):
        """Odoo falls back to the company's own state when the customer has none,
        which calls every such sale intra-state. The team's place of supply is the
        better guess, and it is what the customer would have been given."""
        super()._compute_l10n_in_state_id()
        for move in self:
            if move.country_code != 'IN' or not move.is_sale_document(include_receipts=True):
                continue
            partner = move.partner_id
            commercial = partner.commercial_partner_id
            if (partner.state_id or commercial.state_id or move.partner_shipping_id.state_id
                    or (partner.country_id and partner.country_id.code != 'IN')):
                continue
            team = getattr(move, 'team_id', None) or commercial.team_id or partner.team_id
            if team and team.place_of_supply_id:
                move.l10n_in_state_id = team.place_of_supply_id
