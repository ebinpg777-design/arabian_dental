# -*- coding: utf-8 -*-
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ResPartner(models.Model):
    """A clinic is just a partner with a flag, a pin and a radius.

    Deliberately not a separate model: a clinic is already a customer, already invoiced
    and already has a ledger. A parallel 'clinic' record would mean two things to keep
    in step and two places to look a doctor up.
    """
    _inherit = 'res.partner'

    is_clinic = fields.Boolean('Is a Clinic', index=True)
    visit_radius_m = fields.Float(
        'Visit Radius (m)',
        help="How close an executive must be for the check-in to count as at the "
             "clinic. Blank uses the company default.")
    is_geolocated = fields.Boolean(compute='_compute_is_geolocated', store=True,
                                   string='Pinned on Map')
    visit_ids = fields.One2many('lab.visit', 'partner_id', string='Visits')
    visit_count = fields.Integer(compute='_compute_visit_stats')
    last_visit = fields.Date(compute='_compute_visit_stats', string='Last Visited')
    # Everything the field force has produced at this clinic, reachable from the
    # clinic itself. A doctor's record should answer "what has come of visiting them"
    # without anyone having to build a filter.
    case_count = fields.Integer(compute='_compute_visit_stats', string='Cases')
    case_value = fields.Monetary(compute='_compute_visit_stats', string='Case Value',
                                 currency_field='currency_id')
    collected_total = fields.Monetary(compute='_compute_visit_stats',
                                      string='Collected', currency_field='currency_id')

    # ---------------------------------------------------------------- route scope
    # An executive works one round. Left unfiltered, the clinic picker on a visit
    # offered all 6,395 customers in the database, so a Kollam executive could raise
    # a visit against a Thrissur doctor by mistyping two letters. This narrows what
    # they can pick to the clinics on their OWN sales route - and only for them:
    # managers and administrators plan across routes and still see everything.
    # (client, 2026-08-22)
    #
    # A searchable computed field rather than a record rule on res.partner: a rule
    # would follow the executive into every other screen in Odoo - their own contact
    # card, the company, vendors - and lock them out of records that have nothing to
    # do with sales routes. This constrains the places field work actually offers a
    # clinic, and leaves the rest of the system alone.
    lab_on_my_route = fields.Boolean(
        string='On My Sales Route', compute='_compute_lab_on_my_route',
        search='_search_lab_on_my_route',
        help="For a field-work Executive: true when this clinic sits on a sales "
             "route they work. Always true for managers, who plan across routes.")

    @api.model
    def _lab_route_limited(self):
        """Is the CURRENT user one of the people this restriction is for?

        Only a plain Executive. Administrator implies Manager, so the one manager
        test covers both.
        """
        user = self.env.user
        return (user.has_group('lab_fieldwork.group_fieldwork_executive')
                and not user.has_group('lab_fieldwork.group_fieldwork_manager'))

    @api.model
    def _lab_my_route_ids(self):
        """The sales routes this user works.

        Both ways a person is attached to a route: leading it (`user_id`, which is
        how every route on this database is staffed today) and being listed on it
        (`member_ids`, unused so far but the field Odoo intends for exactly this).
        sudo because an executive cannot read crm.team.
        """
        user = self.env.user
        return self.env['crm.team'].sudo().search(
            ['|', ('user_id', '=', user.id), ('member_ids', 'in', user.id)]).ids

    def _compute_lab_on_my_route(self):
        limited = self._lab_route_limited()
        routes = set(self._lab_my_route_ids()) if limited else set()
        for partner in self:
            if not limited:
                partner.lab_on_my_route = True
                continue
            # A contact under a clinic belongs to the clinic's route, the same
            # fallback the collections and statement figures use.
            team = partner.team_id or partner.commercial_partner_id.team_id
            partner.lab_on_my_route = bool(team) and team.id in routes

    def _search_lab_on_my_route(self, operator, value):
        if operator not in ('=', '!='):
            raise UserError(_("Unsupported operator for On My Sales Route."))
        wanted = (operator == '=') == bool(value)
        if not self._lab_route_limited():
            return [(1, '=', 1)] if wanted else [(0, '=', 1)]
        routes = self._lab_my_route_ids()
        if not routes:
            # Fails CLOSED. An executive with no route assigned picks nothing rather
            # than everything - the empty list is the signal to give them a route,
            # where silently showing all 6,395 clinics would look like it worked.
            return [(0, '=', 1)] if wanted else [(1, '=', 1)]
        domain = ['|', ('team_id', 'in', routes),
                  '&', ('team_id', '=', False),
                       ('commercial_partner_id.team_id', 'in', routes)]
        return domain if wanted else ['!'] + domain

    @api.depends('partner_latitude', 'partner_longitude')
    def _compute_is_geolocated(self):
        for p in self:
            p.is_geolocated = bool(p.partner_latitude) or bool(p.partner_longitude)

    def _compute_visit_stats(self):
        """One grouped query for the whole recordset.

        This used to run a search PER PARTNER. On a form that is invisible; on the
        clinic list it is one query per row, so a lab with four hundred clinics paid
        four hundred round trips to draw one page.
        """
        for p in self:
            p.visit_count = p.case_count = 0
            p.case_value = p.collected_total = 0.0
            p.last_visit = False
        if not self.ids:
            return
        # sudo: these are aggregates shown on the clinic card. The compute runs for
        # EVERY user opening a contact (`case_count` sits on the form for all groups),
        # and a counter clerk or accountant has no lab.visit access — without sudo
        # they got "You are not allowed to access 'Clinic Visit'" just opening Contacts.
        # What is displayed stays governed by the views' `groups` on each field.
        groups = self.env['lab.visit'].sudo()._read_group(
            [('partner_id', 'in', self.ids), ('state', '=', 'done')],
            ['partner_id'],
            ['__count', 'date:max', 'order_count:sum', 'order_value:sum',
             'collected:sum'])
        by_partner = {g[0].id: g[1:] for g in groups}
        for p in self:
            row = by_partner.get(p.id)
            if not row:
                continue
            count, last, cases, value, collected = row
            p.visit_count = count
            p.last_visit = last
            p.case_count = cases or 0
            p.case_value = value or 0.0
            p.collected_total = collected or 0.0

    def action_view_visits(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': 'Visits',
            'res_model': 'lab.visit', 'view_mode': 'list,form',
            'domain': [('partner_id', '=', self.id)],
            'context': {'default_partner_id': self.id},
        }

    def action_view_cases(self):
        """Every case this clinic handed over in the field."""
        self.ensure_one()
        orders = self.env['lab.visit'].search(
            [('partner_id', '=', self.id)]).mapped('order_ids')
        return {
            'type': 'ir.actions.act_window', 'name': _('Cases from Visits'),
            'res_model': 'sale.order', 'view_mode': 'list,form',
            'domain': [('id', 'in', orders.ids)],
        }

    def action_view_collections(self):
        """Money taken at this clinic by hand, and which visit it came from."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Cash Collected Here'),
            'res_model': 'petty.cash.transaction', 'view_mode': 'list,form',
            'domain': [('partner_id', '=', self.id), ('type', '=', 'collection')],
            'context': {'create': False},
        }

    def action_view_coverage(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Coverage'),
            'res_model': 'lab.coverage', 'view_mode': 'list',
            'domain': [('partner_id', '=', self.id)],
        }

    @api.model_create_multi
    def create(self, vals_list):
        """A field executive may open a new clinic — on their own round.

        The route is not optional and not a default they can change: the record rule
        that lets an executive READ a clinic tests its route against theirs, so a clinic
        created without one, or on somebody else's, is a clinic they would create and
        then instantly be unable to see. Stamping it here is what makes "executives can
        add a clinic" actually work rather than half-work.

        Write is deliberately NOT granted alongside create: the executive partner rule
        is read-only (perm_write False), so a write ACL would be constrained by no rule
        at all and would let a field executive edit any contact in the company.
        (client, 2026-08-27)
        """
        executive = (not self.env.su
                     and self.env.user.has_group(
                         'lab_fieldwork.group_fieldwork_executive')
                     and not self.env.user.has_group(
                         'lab_fieldwork.group_fieldwork_manager'))
        if executive:
            routes = self.env.user.fw_route_ids
            if not routes:
                raise UserError(_(
                    "You are not on a sales route yet, so a new clinic would have "
                    "nowhere to belong. Ask your manager to put you on one."))
            for vals in vals_list:
                team = vals.get('team_id')
                if team and int(team) not in routes.ids:
                    raise UserError(_(
                        "A clinic you add has to be on one of your own routes (%s).",
                        ', '.join(routes.mapped('name'))))
                vals.setdefault('team_id', routes[0].id)
                # A doctor is a customer; without this the clinic never reaches the
                # lists that matter and cannot be ordered for.
                vals.setdefault('customer_rank', 1)
        return super().create(vals_list)


class ResUsers(models.Model):
    """The routes a person works, for the record rules to read.

    Computed, not stored, for the same reason `fw_clinic_ids` is: a route gains and
    loses its people, and a stale copy would either leak another round's clinics or
    hide the person's own. Kept light on purpose - a rule that walked every partner
    would be evaluated on every read; this returns a handful of route ids and the
    rule joins on them.
    """
    _inherit = 'res.users'

    fw_route_ids = fields.Many2many(
        'crm.team', 'fw_user_route_rel', 'user_id', 'team_id',
        string='My Sales Routes', compute='_compute_fw_routes',
        help="Sales routes this person leads or is a member of. Read by the partner "
             "record rule that confines a field executive to their own round.")

    fw_menu_ids = fields.Many2many(
        'ir.ui.menu', 'fw_user_menu_rel', 'user_id', 'menu_id',
        string='Field Work Menus', compute='_compute_fw_menus',
        help="The Field Work menu and everything under it. Read by the menu record "
             "rule that gives an executive this one menu and no other.")

    def _compute_fw_routes(self):
        # sudo: an executive cannot read crm.team, and must still resolve their own.
        Team = self.env['crm.team'].sudo()
        for user in self:
            user.fw_route_ids = Team.search(
                ['|', ('user_id', '=', user.id), ('member_ids', 'in', user.id)])

    fw_is_field_person = fields.Boolean(
        string='Works the Field', compute='_compute_fw_is_field_person',
        search='_search_fw_is_field_person',
        help="Someone who works a round: a field-work Executive or Manager. The "
             "Executive pickers use this so the list is colleagues rather than "
             "every login in the database.")

    def _fw_field_group_ids(self):
        """The groups that make somebody a field person, if installed."""
        ids = []
        for xmlid in ('lab_fieldwork.group_fieldwork_executive',
                      'lab_fieldwork.group_fieldwork_manager'):
            group = self.env.ref(xmlid, raise_if_not_found=False)
            if group:
                ids.append(group.id)
        return ids

    def _compute_fw_is_field_person(self):
        groups = set(self._fw_field_group_ids())
        for user in self:
            user.fw_is_field_person = bool(groups & set(user.all_group_ids.ids))

    def _search_fw_is_field_person(self, operator, value):
        if operator not in ('=', '!='):
            raise UserError(_("Unsupported operator for Works the Field."))
        wanted = (operator == '=') == bool(value)
        groups = self._fw_field_group_ids()
        if not groups:
            return [(1, '=', 1)] if not wanted else [(0, '=', 1)]
        # all_group_ids covers implied groups, so an Administrator (who implies
        # Manager) is a field person without being listed twice.
        domain = [('all_group_ids', 'in', groups)]
        return domain if wanted else ['!'] + domain

    @api.model
    def _fw_migrate_manager_split(self):
        """One-shot: whoever held the old single Manager role directly lands on
        Operational Manager - the day-to-day half of what that role was. The
        Marketing Manager role starts empty and is assigned by name; the
        Administrator implies both desks, so nothing is unapprovable meanwhile.

        Guarded by a parameter, not by noupdate: a <function> in noupdate data
        never runs on upgrade at all, and one that reruns every upgrade would
        quietly re-grant a role the client had deliberately taken away.
        """
        icp = self.env['ir.config_parameter'].sudo()
        if icp.get_param('lab_fieldwork.manager_split_done'):
            return False
        base = self.env.ref('lab_fieldwork.group_fieldwork_manager',
                            raise_if_not_found=False)
        ops = self.env.ref('lab_fieldwork.group_fieldwork_ops_manager',
                           raise_if_not_found=False)
        marketing = self.env.ref('lab_fieldwork.group_fieldwork_marketing_manager',
                                 raise_if_not_found=False)
        if not (base and ops and marketing):
            return False
        moved = self.env['res.users']
        for user in base.sudo().user_ids:      # DIRECT holders only
            roles = user.all_group_ids
            if ops in roles or marketing in roles:
                continue
            user.sudo().write({'group_ids': [(4, ops.id)]})
            moved |= user
        icp.set_param('lab_fieldwork.manager_split_done', '1')
        if moved:
            _logger.info("manager split: %s moved to Operational Manager",
                         moved.mapped('login'))
        return True

    def _compute_fw_menus(self):
        """The Field Work subtree, resolved here because a rule domain cannot.

        `domain_force` is evaluated with only `user`, `company_ids` and `time` in
        scope - there is no `ref()` - so the subtree is resolved on the user record
        and the rule simply reads it. sudo because the rule is what grants menu
        visibility in the first place; resolving it must not depend on it.
        """
        Menu = self.env['ir.ui.menu'].sudo()
        root = self.env.ref('lab_fieldwork.menu_fieldwork_root',
                            raise_if_not_found=False)
        subtree = Menu.search([('id', 'child_of', root.id)]) if root else Menu.browse()
        for user in self:
            user.fw_menu_ids = subtree
