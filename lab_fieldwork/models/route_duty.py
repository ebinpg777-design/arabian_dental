# -*- coding: utf-8 -*-
"""Route duty: who works which sales route, on which days.

A sales route (``crm.team``) has its standing people - the leader and the members -
and that is how every route on this database is staffed. It answers "whose round is
this" for good, and nothing else: not "who is on it tomorrow while Shaji is at a
wedding", not "send Ashiq along with him for the Attappadi run this week", not "put
the two new executives on Palakkad for a month". Those were arranged by phone and
the system never heard of them, so the stand-in could not see the route's clinics,
the visits planned for the absent person sat in his name, and the manager's board
counted a route nobody was on as fully staffed.

A duty is one person on one route for a span of days. It ADDS to the standing
membership for those days - everything that reads "my routes" (the clinics an
executive may reach, the pickers, Track order, new clinics, the live board) widens
through ``_routes_on`` - and it never takes anything away: the person who hands a
route over still sees it, because a round handed over on Monday is a round they are
back on by Wednesday.

Three kinds. *Assigned* is a manager's decision and is confirmed at once. *Covering*
and *Assisting* are what an executive asks a colleague for - "I'm away, take my
round" or "come with me" - and wait as a request until the colleague accepts, so
nobody finds a route in their day they never agreed to. A manager can also arrange
either directly, and can answer a request on the colleague's behalf.

Accepting a cover moves the absent person's planned visits on that route to the
cover, and cancelling it moves them back; *Plan This Week* plans straight to the
cover. The roster then shows a route with nobody on it, and a person with a heavy
day, before it happens rather than after. (client, 2026-11-07)
"""
from collections import defaultdict
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import format_date

from .local_day import local_now
from .location_live import PALETTE

KINDS = [
    ('regular', 'Assigned'),
    ('cover', 'Covering'),
    ('assist', 'Assisting'),
]
STATES = [
    ('pending', 'Waiting for an answer'),
    ('active', 'Confirmed'),
    ('declined', 'Declined'),
    ('cancelled', 'Cancelled'),
]
MANAGER = 'lab_fieldwork.group_fieldwork_manager'
# Routes a person is on in ONE day before the roster calls the day heavy.
HEAVY_DAY = 3
# How far ahead My Day lists what is coming.
UPCOMING_DAYS = 14
# The weekday the roster does not count as uncovered (Sunday).
OFF_WEEKDAY = 6


class LabRouteDuty(models.Model):
    _name = 'lab.route.duty'
    _description = 'Route Duty'
    _order = 'date_from desc, id desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    user_id = fields.Many2one(
        'res.users', string='Executive', required=True, index=True, tracking=True,
        domain="[('fw_is_field_person', '=', True)]",
        help="Who works the route for these days.")
    team_id = fields.Many2one(
        'crm.team', string='Sales Route', required=True, index=True, tracking=True,
        ondelete='restrict')
    color = fields.Integer(related='team_id.color')
    date_from = fields.Date(
        required=True, tracking=True, default=fields.Date.context_today)
    date_to = fields.Date(
        required=True, tracking=True, default=fields.Date.context_today,
        help="The same day as the start for a single day.")
    kind = fields.Selection(
        KINDS, required=True, default='regular', tracking=True,
        help="Assigned: a manager put them on the route. Covering: they stand in "
             "for a colleague who is away. Assisting: they work it alongside.")
    state = fields.Selection(
        STATES, required=True, default='active', index=True, tracking=True, copy=False,
        help="A request from an executive waits for the colleague's answer; a "
             "manager's assignment is confirmed at once.")
    origin_user_id = fields.Many2one(
        'res.users', string='Standing In For', tracking=True, index=True,
        domain="[('fw_is_field_person', '=', True)]",
        help="Whose duty this covers or assists.")
    requester_id = fields.Many2one(
        'res.users', string='Arranged By', readonly=True,
        default=lambda self: self.env.user)
    note = fields.Char(help="Leave, a wedding, a new executive learning the round - "
                            "said once so the roster can say it too.")
    answered_at = fields.Datetime(readonly=True, copy=False)
    # Planned visits handed to a cover, so a cancelled cover can hand them back.
    moved_visit_ids = fields.Many2many(
        'lab.visit', 'lab_route_duty_visit_rel', 'duty_id', 'visit_id',
        string='Visits Handed Over', readonly=True, copy=False)
    moved_visit_count = fields.Integer(compute='_compute_moved_visit_count')
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company)

    # Compared with TODAY, so not stored (see lab.desk.cover: a stored phase stays
    # 'scheduled' for ever because nothing on the record changes when a day comes).
    phase = fields.Selection(
        [('scheduled', 'Coming'), ('running', 'Running'), ('over', 'Finished')],
        compute='_compute_phase', search='_search_phase')
    days = fields.Integer(compute='_compute_days')

    _dates_ordered = models.Constraint(
        'CHECK(date_to >= date_from)',
        'A duty cannot end before it starts.')

    # ------------------------------------------------------------------ computes
    @api.depends('team_id', 'user_id', 'date_from', 'date_to')
    def _compute_display_name(self):
        for duty in self:
            duty.display_name = "%s · %s · %s" % (
                duty.team_id.name or '?', duty.user_id.name or '?', duty._span())

    @api.depends('date_from', 'date_to')
    def _compute_days(self):
        for duty in self:
            duty.days = ((duty.date_to - duty.date_from).days + 1) \
                if duty.date_from and duty.date_to else 0

    @api.depends('moved_visit_ids')
    def _compute_moved_visit_count(self):
        for duty in self:
            duty.moved_visit_count = len(duty.moved_visit_ids)

    @api.depends('date_from', 'date_to')
    def _compute_phase(self):
        today = self._today()
        for duty in self:
            if duty.date_to and duty.date_to < today:
                duty.phase = 'over'
            elif duty.date_from and duty.date_from > today:
                duty.phase = 'scheduled'
            else:
                duty.phase = 'running'

    def _search_phase(self, operator, value):
        if operator not in ('=', '!=', 'in', 'not in'):
            raise UserError(_("Unsupported operator for Phase."))
        wanted = set([value] if isinstance(value, str) else value)
        if operator in ('!=', 'not in'):
            wanted = {'scheduled', 'running', 'over'} - wanted
        today = self._today()
        parts = []
        if 'over' in wanted:
            parts.append([('date_to', '<', today)])
        if 'scheduled' in wanted:
            parts.append([('date_from', '>', today)])
        if 'running' in wanted:
            parts.append([('date_from', '<=', today), ('date_to', '>=', today)])
        if not parts:
            return [(0, '=', 1)]
        domain = parts[0]
        for part in parts[1:]:
            domain = ['|'] + domain + part
        return domain

    # --------------------------------------------------------------- constraints
    @api.constrains('user_id', 'origin_user_id', 'kind')
    def _check_people(self):
        for duty in self:
            if duty.kind in ('cover', 'assist') and not duty.origin_user_id:
                raise ValidationError(_(
                    "Say whose duty %s covers or assists.", duty.user_id.name))
            if duty.origin_user_id and duty.origin_user_id == duty.user_id:
                raise ValidationError(_(
                    "%s cannot stand in for themselves.", duty.user_id.name))

    # ------------------------------------------------------------------- helpers
    @api.model
    def _today(self, user=None):
        return local_now(self.env, user).date()

    @api.model
    def _is_manager(self):
        return self.env.su or self.env.user.has_group(MANAGER)

    @api.model
    def _check_manager(self):
        if not self._is_manager():
            raise AccessError(_("Only a field work manager can do this."))

    def _span(self):
        self.ensure_one()
        if not (self.date_from and self.date_to):
            return ''
        if self.date_from == self.date_to:
            return format_date(self.env, self.date_from, date_format='EEE d MMM')
        return "%s – %s" % (
            format_date(self.env, self.date_from, date_format='d MMM'),
            format_date(self.env, self.date_to, date_format='d MMM'))

    @api.model
    def _kind_word(self, kind, for_whom=None):
        words = {
            'regular': _("assigned"),
            'cover': _("covering for %s") % for_whom if for_whom else _("covering"),
            'assist': _("with %s") % for_whom if for_whom else _("assisting"),
        }
        return words.get(kind, kind)

    @api.model
    def _standing_routes(self, user):
        """The routes a person leads or is a member of - for good."""
        return self.env['crm.team'].sudo().search(
            ['|', ('user_id', '=', user.id), ('member_ids', 'in', user.id)])

    @api.model
    def _standing_users(self, team):
        """The people a route always has: its leader and its members."""
        team = team.sudo()
        return (team.user_id | team.member_ids).filtered('active')

    @api.model
    def _duties_on(self, user, day):
        """A person's confirmed duties that include `day`."""
        return self.sudo().search([
            ('user_id', '=', user.id), ('state', '=', 'active'),
            ('date_from', '<=', day), ('date_to', '>=', day)])

    @api.model
    def _routes_on(self, user, day=None):
        """Every route `user` works on `day`: standing membership plus the duties
        confirmed for that day. What "my routes" means everywhere in Field Work."""
        day = day or self._today(user)
        return self._standing_routes(user) | self._duties_on(user, day).team_id

    @api.model
    def _works_route(self, user, team, date_from, date_to):
        """Is `team` one of `user`'s routes on every day from `date_from` to
        `date_to` - standing, or by one confirmed duty spanning the whole of it?"""
        if team in self._standing_routes(user):
            return True
        return bool(self.sudo().search_count([
            ('user_id', '=', user.id), ('team_id', '=', team.id), ('state', '=', 'active'),
            ('date_from', '<=', date_from), ('date_to', '>=', date_to)], limit=1))

    @api.model
    def _cover_for(self, user, team, day):
        """Who stands in for `user` on `team` on `day`, if anybody."""
        if not (user and team):
            return self.env['res.users']
        duty = self.sudo().search([
            ('kind', '=', 'cover'), ('state', '=', 'active'),
            ('origin_user_id', '=', user.id), ('team_id', '=', team.id),
            ('date_from', '<=', day), ('date_to', '>=', day)], limit=1)
        return duty.user_id

    @api.model
    def _route_clinic_domain(self, team, prefix=''):
        """The clinics on a route, as the clinic pickers see them; `prefix`
        reaches them through a relation (`'partner_id.'` from a visit)."""
        return ['|', (prefix + 'team_id', '=', team.id),
                '&', (prefix + 'team_id', '=', False),
                     (prefix + 'commercial_partner_id.team_id', '=', team.id)]

    def _after_change(self):
        """A duty changes the answer to "my routes", and the access rules that
        read it are cached per user - flush them, the way a route change does."""
        self.env.transaction.invalidate_ormcache()
        self.env['res.users'].invalidate_model(['fw_route_ids'])

    # ----------------------------------------------------------- create / write
    @api.model
    def _executive_request_vals(self, vals):
        """What a plain executive may create: a request to a colleague, about
        their own duty, waiting for an answer. Everything else is a manager's."""
        me = self.env.user
        kind = vals.get('kind') or 'cover'
        if kind not in ('cover', 'assist'):
            raise UserError(_(
                "An executive asks a colleague to cover or to assist. Putting "
                "somebody on a route is a manager's job."))
        vals['kind'] = kind
        vals['origin_user_id'] = me.id
        vals['requester_id'] = me.id
        vals['state'] = 'pending'
        if vals.get('user_id') == me.id:
            raise UserError(_("Pick the colleague you are asking."))
        team = self.env['crm.team'].sudo().browse(vals.get('team_id'))
        date_from = fields.Date.to_date(vals.get('date_from')) or self._today(me)
        date_to = fields.Date.to_date(vals.get('date_to')) or date_from
        if not self._works_route(me, team, date_from, date_to):
            raise UserError(_(
                "%s is not your route on those days, so there is nothing of yours "
                "to hand over on it.", team.name or '?'))
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        if not self._is_manager():
            vals_list = [self._executive_request_vals(dict(vals)) for vals in vals_list]
        duties = super().create(vals_list)
        duties._after_change()
        for duty in duties:
            duty.message_subscribe(partner_ids=(
                duty.user_id | duty.origin_user_id | duty.requester_id).partner_id.ids)
            duty._announce('created')
        return duties

    def write(self, vals):
        if not self._is_manager():
            # An executive changes a duty through its buttons; by hand they may
            # only reword their own open request.
            stray = set(vals) - {'note'}
            if stray:
                raise UserError(_(
                    "Answer or withdraw a duty with its buttons; a manager changes "
                    "the rest."))
            foreign = self.filtered(
                lambda d: d.requester_id != self.env.user or d.state != 'pending')
            if foreign:
                raise UserError(_("You can only reword a request of yours that is "
                                  "still waiting for an answer."))
        res = super().write(vals)
        if set(vals) & {'user_id', 'team_id', 'date_from', 'date_to', 'state', 'active'}:
            self._after_change()
        return res

    def unlink(self):
        self._check_manager()
        self._undo_cover()
        res = super().unlink()
        self._after_change()
        return res

    # ------------------------------------------------------------------ buttons
    def _can_answer(self):
        self.ensure_one()
        return self._is_manager() or self.user_id == self.env.user

    def action_accept(self):
        now = fields.Datetime.now()
        for duty in self:
            if duty.state != 'pending':
                raise UserError(_("%s is not waiting for an answer.", duty.display_name))
            if not duty._can_answer():
                raise AccessError(_(
                    "Only %s, or a manager, can answer this request.", duty.user_id.name))
            privileged = duty.sudo()
            privileged.write({'state': 'active', 'answered_at': now})
            privileged._apply_cover()
            privileged.activity_ids.filtered(
                lambda a: a.user_id == duty.user_id).action_feedback(
                feedback=_("Accepted"))
            privileged._announce('accepted')
        return True

    def action_decline(self):
        now = fields.Datetime.now()
        for duty in self:
            if duty.state != 'pending':
                raise UserError(_("%s is not waiting for an answer.", duty.display_name))
            if not duty._can_answer():
                raise AccessError(_(
                    "Only %s, or a manager, can answer this request.", duty.user_id.name))
            privileged = duty.sudo()
            privileged.write({'state': 'declined', 'answered_at': now})
            privileged.activity_ids.filtered(
                lambda a: a.user_id == duty.user_id).unlink()
            privileged._announce('declined')
        return True

    def action_cancel(self):
        """Withdraw a request or end a duty early. The person asked, the person
        asking, the one being covered, or a manager."""
        for duty in self:
            if duty.state not in ('pending', 'active'):
                raise UserError(_("%s is already closed.", duty.display_name))
            me = self.env.user
            if not (self._is_manager() or me in (
                    duty.user_id | duty.origin_user_id | duty.requester_id)):
                raise AccessError(_("This duty is not yours to cancel."))
            privileged = duty.sudo()
            privileged._undo_cover()
            privileged.write({'state': 'cancelled'})
            privileged.activity_ids.unlink()
            privileged._announce('cancelled')
        return True

    def action_reopen(self):
        """A manager puts a declined or cancelled duty back as a request."""
        self._check_manager()
        for duty in self.filtered(lambda d: d.state in ('declined', 'cancelled')):
            duty.sudo().write({'state': 'pending', 'answered_at': False})
            duty.sudo()._announce('created')
        return True

    def action_view_visits(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Visits Handed Over'),
            'res_model': 'lab.visit', 'view_mode': 'list,form',
            'domain': [('id', 'in', self.moved_visit_ids.ids)],
        }

    # ------------------------------------------------------ the cover's visits
    def _apply_cover(self):
        """The absent person's planned visits on this route, for these days, go to
        the cover. Only planned ones: a visit already at the clinic is somebody's
        afternoon, not a plan."""
        Visit = self.env['lab.visit'].sudo()
        for duty in self.filtered(
                lambda d: d.kind == 'cover' and d.state == 'active' and d.origin_user_id):
            visits = Visit.search([
                ('user_id', '=', duty.origin_user_id.id), ('state', '=', 'planned'),
                ('date', '>=', duty.date_from), ('date', '<=', duty.date_to),
                *duty._route_clinic_domain(duty.team_id, prefix='partner_id.'),
                ('id', 'not in', duty.moved_visit_ids.ids)])
            if not visits:
                continue
            visits.write({'user_id': duty.user_id.id})
            duty.write({'moved_visit_ids': [(4, v.id) for v in visits]})
            duty.message_post(body=_(
                "%(n)s planned visit(s) handed to %(who)s: %(names)s",
                n=len(visits), who=duty.user_id.name,
                names=', '.join(visits.mapped('partner_id.name'))))

    def _undo_cover(self):
        """Hand back what is still only planned and still in the cover's name."""
        for duty in self.sudo().filtered('moved_visit_ids'):
            back = duty.moved_visit_ids.filtered(
                lambda v: v.state == 'planned' and v.user_id == duty.user_id)
            if back:
                back.write({'user_id': duty.origin_user_id.id})
                duty.message_post(body=_(
                    "%(n)s planned visit(s) handed back to %(who)s.",
                    n=len(back), who=duty.origin_user_id.name))
            duty.write({'moved_visit_ids': [(5, 0, 0)]})

    # ------------------------------------------------------------- announcing
    def _announce(self, event):
        """Who is told what. In their inbox: a duty somebody arranged for you and
        never mentioned is a duty you do not know you are working."""
        self.ensure_one()
        duty = self.sudo()
        route, span = duty.team_id.name, duty._span()
        why = (" (%s)" % duty.note) if duty.note else ""
        origin = duty.origin_user_id
        asker = duty.requester_id or origin or self.env.user
        others = (origin | duty.requester_id) - duty.user_id
        if event == 'created' and duty.state == 'pending':
            verb = _("cover your round on") if duty.kind == 'cover' else _("come along on")
            duty.message_notify(
                partner_ids=duty.user_id.partner_id.ids,
                subject=_("A request on %s", route),
                body=_("%(who)s asks you to %(verb)s %(route)s, %(span)s.%(why)s "
                       "Answer it on My Day.", who=asker.name, verb=verb,
                       route=route, span=span, why=why))
            duty.activity_schedule(
                'mail.mail_activity_data_todo', user_id=duty.user_id.id,
                date_deadline=min(duty.date_from, self._today()),
                summary=_("Answer %(who)s's request: %(route)s, %(span)s",
                          who=asker.name, route=route, span=span),
                note=duty.note or '')
        elif event in ('created', 'accepted') and duty.state == 'active':
            if event == 'created':
                duty.message_notify(
                    partner_ids=duty.user_id.partner_id.ids,
                    subject=_("You are on %s", route),
                    body=_("%(who)s put you on %(route)s, %(span)s (%(kind)s).%(why)s",
                           who=asker.name, route=route, span=span,
                           kind=self._kind_word(duty.kind, origin.name), why=why))
            if others:
                duty.message_notify(
                    partner_ids=others.partner_id.ids,
                    subject=_("%s on %s", duty.user_id.name, route),
                    body=_("%(who)s is %(kind)s on %(route)s, %(span)s.",
                           who=duty.user_id.name, route=route, span=span,
                           kind=self._kind_word(duty.kind, origin.name)))
        elif event == 'declined' and others:
            duty.message_notify(
                partner_ids=others.partner_id.ids,
                subject=_("%s declined", duty.user_id.name),
                body=_("%(who)s cannot take %(route)s, %(span)s. Ask somebody else, "
                       "or ask your manager.", who=duty.user_id.name, route=route,
                       span=span))
        elif event == 'cancelled':
            told = (duty.user_id | others) - self.env.user
            if told:
                duty.message_notify(
                    partner_ids=told.partner_id.ids,
                    subject=_("%s: duty cancelled", route),
                    body=_("%(who)s cancelled %(name)s's duty on %(route)s, %(span)s.",
                           who=self.env.user.name, name=duty.user_id.name,
                           route=route, span=span))

    # --------------------------------------------------------- for the screens
    @api.model
    def _avatar(self, user):
        return '/web/image/res.users/%d/avatar_128' % user.id

    @api.model
    def _people(self):
        """Everyone who works the field, in name order, with a colour each."""
        people = self.env['lab.desk']._executives()
        for team in self.env['crm.team'].sudo().search([]):
            people |= self._standing_users(team).filtered('fw_is_field_person')
        return people.sorted('name')

    @api.model
    def _person_row(self, user, index):
        return {'id': user.id, 'name': user.name, 'avatar': self._avatar(user),
                'colour': PALETTE[index % len(PALETTE)]}

    @api.model
    def _clinic_counts(self, teams):
        Partner = self.env['res.partner'].sudo()
        return {team.id: Partner.search_count(self._route_clinic_domain(team))
                for team in teams}

    @api.model
    def get_my_routes(self, day=None):
        """My Day's card: the routes this person is on today, who else is on
        them, the requests waiting for them, what they asked of others, and
        what is coming. Read elevated and filtered by person: an executive
        cannot read crm.team, and must still see their own routes by name."""
        me = self.env.user
        today = self._today(me)
        day = fields.Date.to_date(day) if day else today
        Duty = self.sudo()
        standing = self._standing_routes(me)
        duties = self._duties_on(me, day)
        teams = standing | duties.team_id
        Visit = self.env['lab.visit'].sudo()
        visits = Visit.search([('user_id', '=', me.id), ('date', '=', day),
                               ('state', '!=', 'cancel')])
        visits_by_team = defaultdict(int)
        for visit in visits:
            team = visit.partner_id.team_id or visit.partner_id.commercial_partner_id.team_id
            visits_by_team[team.id] += 1
        clinics = self._clinic_counts(teams)
        on_route = Duty.search([('team_id', 'in', teams.ids), ('state', '=', 'active'),
                                ('date_from', '<=', day), ('date_to', '>=', day),
                                ('user_id', '!=', me.id)])
        rows, seen = [], set()

        def others_on(team):
            people = (self._standing_users(team) - me) | on_route.filtered(
                lambda d: d.team_id == team).user_id
            return [{'id': u.id, 'name': u.name, 'avatar': self._avatar(u)}
                    for u in people.sorted('name')]

        for team in standing.sorted('name'):
            seen.add(team.id)
            rows.append({
                'id': team.id, 'name': team.name, 'color': team.color or 0,
                'kind': 'standing', 'for': '', 'until': '', 'duty_id': 0,
                'with': others_on(team), 'clinics': clinics.get(team.id, 0),
                'visits': visits_by_team.get(team.id, 0)})
        for duty in duties.sorted(lambda d: (d.team_id.name or '', d.id)):
            if duty.team_id.id in seen:
                continue
            seen.add(duty.team_id.id)
            rows.append({
                'id': duty.team_id.id, 'name': duty.team_id.name,
                'color': duty.team_id.color or 0, 'kind': duty.kind,
                'for': duty.origin_user_id.name or '',
                'until': format_date(self.env, duty.date_to, date_format='d MMM')
                if duty.date_to != day else _("today"),
                'duty_id': duty.id, 'with': others_on(duty.team_id),
                'clinics': clinics.get(duty.team_id.id, 0),
                'visits': visits_by_team.get(duty.team_id.id, 0)})
        requests = Duty.search([('user_id', '=', me.id), ('state', '=', 'pending'),
                                ('date_to', '>=', today)], order='date_from, id')
        outgoing = Duty.search([
            '|', ('origin_user_id', '=', me.id), ('requester_id', '=', me.id),
            ('user_id', '!=', me.id), ('state', 'in', ('pending', 'active')),
            ('date_to', '>=', today)], order='date_from, id')
        upcoming = Duty.search([
            ('user_id', '=', me.id), ('state', '=', 'active'),
            ('date_from', '>', day),
            ('date_from', '<=', day + timedelta(days=UPCOMING_DAYS))], order='date_from, id')
        return {
            'day': fields.Date.to_string(day),
            'rows': rows,
            'requests': [{
                'id': d.id, 'from': (d.requester_id or d.origin_user_id).name,
                'avatar': self._avatar(d.requester_id or d.origin_user_id),
                'route': d.team_id.name, 'color': d.team_id.color or 0,
                'kind': d.kind, 'span': d._span(), 'days': d.days, 'note': d.note or '',
            } for d in requests],
            'outgoing': [{
                'id': d.id, 'to': d.user_id.name, 'avatar': self._avatar(d.user_id),
                'route': d.team_id.name, 'color': d.team_id.color or 0,
                'kind': d.kind, 'span': d._span(), 'state': d.state,
                'mine': d.requester_id == me,
            } for d in outgoing],
            'upcoming': [{
                'id': d.id, 'route': d.team_id.name, 'color': d.team_id.color or 0,
                'kind': d.kind, 'for': d.origin_user_id.name or '', 'span': d._span(),
            } for d in upcoming],
            'can_ask': bool(teams),
        }

    @api.model
    def dialog_options(self, mode='handover', day=None):
        """What the hand-over / assign dialog offers: the routes it may name, the
        people it may name, and for each person how loaded that day is and which
        routes they already know - so the first suggestion is the right one."""
        me = self.env.user
        day = fields.Date.to_date(day) if day else self._today(me)
        if mode == 'assign':
            self._check_manager()
            teams = self.env['crm.team'].sudo().search([], order='sequence, name')
            people = self._people()
        else:
            # A colleague: somebody who walks a beat, not the office.
            teams = self._routes_on(me, day).sorted('name')
            people = self.env['lab.desk']._executives() - me
        clinics = self._clinic_counts(teams)
        Duty = self.sudo()
        on_day = Duty.search([('user_id', 'in', people.ids), ('state', '=', 'active'),
                              ('date_from', '<=', day), ('date_to', '>=', day)])
        ever = Duty.search([('user_id', 'in', people.ids), ('state', '=', 'active')])
        load, knows = defaultdict(int), defaultdict(set)
        for duty in on_day:
            load[duty.user_id.id] += 1
        for duty in ever:
            knows[duty.user_id.id].add(duty.team_id.id)
        standing_of = {}
        for user in people:
            routes = self._standing_routes(user)
            standing_of[user.id] = routes.ids
            load[user.id] += len(routes)
            knows[user.id].update(routes.ids)
        return {
            'day': fields.Date.to_string(day),
            'mode': mode,
            'kinds': [{'id': k, 'name': label} for k, label in
                      self._fields['kind']._description_selection(self.env)],
            'routes': [{'id': t.id, 'name': t.name, 'color': t.color or 0,
                        'clinics': clinics.get(t.id, 0)} for t in teams],
            'people': [dict(self._person_row(u, i), load=load[u.id],
                            standing=standing_of[u.id], knows=sorted(knows[u.id]))
                       for i, u in enumerate(people)],
            'me': me.id,
        }

    @api.model
    def request_transfer(self, team_ids, user_id, date_from, date_to=None,
                         kind='cover', note=''):
        """An executive hands a route over, or asks for company, for some days.
        One request per route; each waits for the colleague's answer."""
        date_from = fields.Date.to_date(date_from)
        date_to = fields.Date.to_date(date_to) or date_from
        if not team_ids:
            raise UserError(_("Pick at least one route."))
        if not user_id:
            raise UserError(_("Pick the colleague you are asking."))
        duties = self.create([{
            'team_id': team_id, 'user_id': user_id, 'kind': kind,
            'date_from': date_from, 'date_to': date_to, 'note': note or False,
            'origin_user_id': self.env.user.id,
        } for team_id in team_ids])
        return {'ids': duties.ids, 'count': len(duties)}

    @api.model
    def assign(self, user_ids, team_ids, date_from, date_to=None, kind='regular',
               origin_user_id=False, note=''):
        """A manager puts people on routes for a span of days: every person on
        every route, confirmed at once. Says what it skipped and why."""
        self._check_manager()
        date_from = fields.Date.to_date(date_from)
        date_to = fields.Date.to_date(date_to) or date_from
        if not user_ids or not team_ids:
            raise UserError(_("Pick at least one person and one route."))
        if kind in ('cover', 'assist') and not origin_user_id:
            raise UserError(_("Say whose duty they cover or assist."))
        users = self.env['res.users'].browse(user_ids)
        teams = self.env['crm.team'].sudo().browse(team_ids)
        vals_list, skipped = [], []
        for user in users:
            standing = self._standing_routes(user)
            for team in teams:
                if kind == 'regular' and team in standing:
                    skipped.append(_("%(who)s already works %(route)s for good.",
                                     who=user.name, route=team.name))
                    continue
                if origin_user_id == user.id:
                    skipped.append(_("%s cannot stand in for themselves.", user.name))
                    continue
                twin = self.sudo().search([
                    ('user_id', '=', user.id), ('team_id', '=', team.id),
                    ('kind', '=', kind), ('state', 'in', ('pending', 'active')),
                    ('date_from', '<=', date_to), ('date_to', '>=', date_from)], limit=1)
                if twin:
                    skipped.append(_("%(who)s is already on %(route)s %(span)s.",
                                     who=user.name, route=team.name, span=twin._span()))
                    continue
                vals_list.append({
                    'user_id': user.id, 'team_id': team.id, 'kind': kind,
                    'date_from': date_from, 'date_to': date_to,
                    'origin_user_id': origin_user_id or False, 'note': note or False,
                    'state': 'active', 'requester_id': self.env.user.id,
                })
        duties = self.create(vals_list)
        duties._apply_cover()
        return {'ids': duties.ids, 'count': len(duties), 'skipped': skipped}

    @api.model
    def move_duty(self, duty_id, team_id=None, day=None):
        """The roster's drag: the same duty on another route, or shifted so it
        starts on another day - its length kept."""
        self._check_manager()
        duty = self.browse(int(duty_id))
        if duty.state not in ('pending', 'active'):
            raise UserError(_("%s is closed and stays where it is.", duty.display_name))
        vals = {}
        if team_id and int(team_id) != duty.team_id.id:
            vals['team_id'] = int(team_id)
        if day:
            day = fields.Date.to_date(day)
            if day != duty.date_from:
                vals['date_from'] = day
                vals['date_to'] = day + (duty.date_to - duty.date_from)
        if not vals:
            return False
        privileged = duty.sudo()
        privileged._undo_cover()
        privileged.write(vals)
        privileged._apply_cover()
        return True

    @api.model
    def get_roster(self, date_from, date_to):
        """The manager's grid: every route against every day of the span, who is
        on it (standing, assigned, covering, assisting), who is away and covered,
        the requests waiting, and the numbers worth a look - routes with nobody
        on them, people with a heavy day."""
        self._check_manager()
        d0 = fields.Date.to_date(date_from)
        d1 = fields.Date.to_date(date_to) or d0
        if d1 < d0:
            d0, d1 = d1, d0
        d1 = min(d1, d0 + timedelta(days=30))
        today = self._today()
        days = []
        cursor = d0
        while cursor <= d1:
            days.append({'iso': fields.Date.to_string(cursor),
                         'label': format_date(self.env, cursor, date_format='EEE d'),
                         'month': format_date(self.env, cursor, date_format='MMM'),
                         'is_today': cursor == today, 'past': cursor < today,
                         'off': cursor.weekday() == OFF_WEEKDAY})
            cursor += timedelta(days=1)
        teams = self.env['crm.team'].sudo().search([], order='sequence, name')
        people = self._people()
        person_rows = {u.id: self._person_row(u, i) for i, u in enumerate(people)}
        standing = {team.id: self._standing_users(team) for team in teams}
        for team in teams:
            for user in standing[team.id]:
                if user.id not in person_rows:
                    person_rows[user.id] = self._person_row(user, len(person_rows))
        duties = self.sudo().search([
            ('state', 'in', ('pending', 'active')),
            ('date_from', '<=', d1), ('date_to', '>=', d0)], order='date_from, id')
        cells = defaultdict(list)
        for team in teams:
            for day in days:
                for user in standing[team.id]:
                    cells['%d|%s' % (team.id, day['iso'])].append({
                        'user_id': user.id, 'kind': 'standing', 'duty_id': 0,
                        'state': 'active', 'origin_user_id': 0, 'away': False,
                        'covered_by': ''})
        covers = defaultdict(dict)   # (team, iso) -> origin user id -> cover name
        duty_rows = []
        for duty in duties:
            duty_rows.append({
                'id': duty.id, 'user_id': duty.user_id.id, 'team_id': duty.team_id.id,
                'date_from': fields.Date.to_string(duty.date_from),
                'date_to': fields.Date.to_string(duty.date_to), 'span': duty._span(),
                'kind': duty.kind, 'state': duty.state, 'note': duty.note or '',
                'origin_user_id': duty.origin_user_id.id or 0,
                'origin': duty.origin_user_id.name or '',
                'requester': duty.requester_id.name or '',
                'user': duty.user_id.name, 'route': duty.team_id.name,
                'moved_visits': len(duty.moved_visit_ids)})
            if duty.user_id.id not in person_rows:
                person_rows[duty.user_id.id] = self._person_row(duty.user_id, len(person_rows))
            cursor = max(duty.date_from, d0)
            while cursor <= min(duty.date_to, d1):
                key = '%d|%s' % (duty.team_id.id, fields.Date.to_string(cursor))
                cells[key].append({
                    'user_id': duty.user_id.id, 'kind': duty.kind, 'duty_id': duty.id,
                    'state': duty.state, 'origin_user_id': duty.origin_user_id.id or 0,
                    'away': False, 'covered_by': ''})
                if duty.kind == 'cover' and duty.state == 'active' and duty.origin_user_id:
                    covers[key][duty.origin_user_id.id] = duty.user_id.name
                cursor += timedelta(days=1)
        # The person covered is away for that day on that route.
        for key, by_origin in covers.items():
            for chip in cells[key]:
                if chip['duty_id'] == 0 and chip['user_id'] in by_origin:
                    chip['away'] = True
                    chip['covered_by'] = by_origin[chip['user_id']]
        # The numbers.
        uncovered, load = [], defaultdict(int)
        for team in teams:
            for day in days:
                key = '%d|%s' % (team.id, day['iso'])
                present = [c for c in cells.get(key, [])
                           if c['state'] == 'active' and not c['away']]
                for chip in present:
                    load['%d|%s' % (chip['user_id'], day['iso'])] += 1
                if not present and not day['off'] and not day['past']:
                    uncovered.append(key)
        heavy = sorted(k for k, n in load.items() if n > HEAVY_DAY)
        away = [{
            'user_id': d.origin_user_id.id, 'user': d.origin_user_id.name,
            'route': d.team_id.name, 'team_id': d.team_id.id, 'span': d._span(),
            'covered_by': d.user_id.name, 'duty_id': d.id, 'note': d.note or '',
        } for d in duties if d.kind == 'cover' and d.state == 'active']
        return {
            'today': fields.Date.to_string(today),
            'date_from': fields.Date.to_string(d0), 'date_to': fields.Date.to_string(d1),
            'days': days,
            'routes': [{'id': t.id, 'name': t.name, 'color': t.color or 0,
                        'standing': standing[t.id].ids} for t in teams],
            'people': sorted(person_rows.values(), key=lambda p: p['name']),
            'duties': duty_rows,
            'cells': dict(cells),
            'requests': [r for r in duty_rows if r['state'] == 'pending'],
            'away': away,
            'uncovered': uncovered,
            'heavy': heavy,
            'heavy_after': HEAVY_DAY,
            'kpis': {
                'uncovered': len(uncovered), 'pending': len(
                    [r for r in duty_rows if r['state'] == 'pending']),
                'away': len(away), 'heavy': len(heavy),
                'on_duty_today': len({c['user_id'] for key, chips in cells.items()
                                      if key.endswith(fields.Date.to_string(today))
                                      for c in chips
                                      if c['state'] == 'active' and not c['away']}),
            },
        }

    # --------------------------------------------------------------------- cron
    @api.model
    def _cron_turn_of_day(self):
        """A duty that begins or ends today changes "my routes" without anybody
        writing anything, and the access rules that read it are cached. Once a
        day, when there is such a duty, flush them. Hourly because the day turns
        on the lab's clock, not the server's."""
        today = self._today()
        icp = self.env['ir.config_parameter'].sudo()
        if icp.get_str('lab_fieldwork.duty_cache_day') == str(today):
            return False
        icp.set_str('lab_fieldwork.duty_cache_day', str(today))
        yesterday = today - timedelta(days=1)
        if self.sudo().search_count([
                ('state', '=', 'active'),
                '|', ('date_from', '=', today), ('date_to', '=', yesterday)], limit=1):
            self._after_change()
        return True
