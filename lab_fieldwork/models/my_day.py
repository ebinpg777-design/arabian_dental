# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_date

from .lab_visit import _map_url_and_address, _wa_number, _wa_source

SUGGEST_LIMIT = 4


class LabMyDay(models.AbstractModel):
    """Everything the My Day screen needs, in one call.

    An executive opens this on a phone, often on a poor connection at a clinic door.
    One round trip is not an optimisation here, it is the difference between the screen
    working and not.
    """
    _name = 'lab.my.day'
    _description = 'Field Work — My Day'

    # Groups that supervise rather than walk a beat. The window exists to stop an
    # executive writing up a round from a fortnight ago; it has no business stopping
    # the person who has to CHECK that round. (client, 2026-08-27)
    UNLIMITED_GROUPS = (
        'lab_fieldwork.group_fieldwork_manager',
        'lab_fieldwork.group_fieldwork_admin',
        'lab_ceo_dashboard.group_lab_executive',
        'base.group_system',
    )

    @api.model
    def backdate_limit(self):
        """How many days before today this person may open, from settings.

        A manager, an administrator or whoever runs the lab gets the whole history:
        they are reading somebody else's day, often weeks later, and a three-day window
        made a supervisor unable to look at the round they were asking about.
        """
        if self._may_open_any_day():
            return None
        raw = self.env['ir.config_parameter'].sudo().get_param(
            'lab_fieldwork.backdate_days', 3)
        try:
            return max(int(raw), 0)
        except (TypeError, ValueError):
            return 3

    def _may_open_any_day(self):
        if self.env.su:
            return True
        user = self.env.user
        return any(
            user.has_group(group) for group in self.UNLIMITED_GROUPS
            # A group from a module that is not installed is not an error here: this
            # widget ships without the CEO board and must still load.
            if self.env.ref(group, raise_if_not_found=False))

    @api.model
    def get_day(self, day=None):
        today = fields.Date.to_date(day) if day else fields.Date.context_today(self)
        # A round finished after the phone died is written up the next morning, so
        # My Day opens a configured number of days back - and no further. Forward is
        # never allowed: tomorrow's visits are planned, not recorded.
        # (client, 2026-08-25)
        real_today = fields.Date.context_today(self)
        limit = self.backdate_limit()
        # Forward is never allowed for anybody: tomorrow's visits are planned, not
        # recorded. Backwards, `None` means no floor at all.
        if today > real_today:
            today = real_today
        elif limit is not None:
            earliest = real_today - timedelta(days=limit)
            if today < earliest:
                today = earliest
        Visit = self.env['lab.visit']
        # No user filter: the record rules already restrict an executive to their own
        # visits, so the same call is correct for a manager checking a stand-in.
        visits = Visit.search([('date', '=', today),
                               ('state', '!=', 'cancel')], order='state, id')
        trip = self.env['lab.trip'].search(
            [('user_id', '=', self.env.uid), ('date', '=', today)], limit=1)
        target = self.env['lab.target'].search([
            ('user_id', '=', self.env.uid), ('state', '=', 'open'),
            ('month', '<=', today)], order='month desc', limit=1)
        # The stored achievement does not follow the visits by itself.
        target._refresh_done()

        done = visits.filtered(lambda v: v.state == 'done')
        # Once, and translated - these were two dicts rebuilt from the raw
        # selection for every card, in the executive's language never.
        purpose_labels = dict(Visit._fields['purpose']._description_selection(self.env))
        outcome_labels = dict(Visit._fields['outcome']._description_selection(self.env))
        # Money taken at a door with no visit behind it counts the same: it is
        # the same money, and the executive is as accountable for it.
        # (client, 2026-09-12)
        Collection = self.env['lab.cash.collection']
        loose = Collection._collected_for([self.env.uid], today).get(self.env.uid, 0.0)
        # ONE figure for the lab's money in this person's hands - pocket, float,
        # or an envelope on the office desk waiting to be counted - read from
        # the same place the desks and the reminders read it. Two figures with
        # the same name used to sit on this screen: the pocket alone up top and
        # the whole float (advance included) on the card, and neither could go
        # DOWN, because nothing took cash back out. (client, 2026-09-18)
        Handover = self.env['lab.cash.handover']
        position = Handover.cash_position(self.env.user)[self.env.uid]
        pending = Handover.sudo().search(
            [('user_id', '=', self.env.uid), ('state', '=', 'declared')],
            order='date desc, id desc')
        max_days, ceiling = Handover._cash_policy()
        return {
            'date': fields.Date.to_string(today),
            # An ISO date is a machine's answer. The screen is opened at a clinic door
            # by someone checking they are on the right day, and "Thursday, 6 August"
            # answers that at a glance where "2026-08-06" has to be decoded.
            'date_label': format_date(self.env, today, date_format='EEEE, d MMMM'),
            'greeting': self._greeting(),
            'user_name': self.env.user.name.split()[0] if self.env.user.name else '',
            'currency_id': self.env.company.currency_id.id,
            'summary': {
                'planned': len(visits),
                'done': len(done),
                'cases': sum(done.mapped('order_count')),
                'value': sum(done.mapped('order_value')),
                'collected': sum(done.mapped('collected')) + loose,
                'cash_in_hand': position['available'],
                # The hand counts, over EVERY visit of the day and not only the
                # finished ones: they are typed while the executive is still at
                # the counter, and a number that only appears after check-out
                # reads as "it did not save". (client, 2026-09-08)
                'cases_collected': sum(visits.mapped('cases_counted')),
                'reworks_collected': sum(visits.mapped('reworks_collected')),
            },
            'cash': {
                # Not yet in the office's hands, and the part of it already on
                # the desk waiting to be counted.
                'with_you': position['with_you'],
                'available': position['available'],
                'unbanked': position['unbanked'],
                'in_float': position['in_float'],
                'pending': position['pending'],
                'pending_count': position['pending_count'],
                'days': position['days'],
                'overdue': Handover._is_overdue(position, (max_days, ceiling)),
                'max_days': max_days,
                'ceiling': ceiling,
                'allocation_id': position['allocation_id'],
                'pending_rows': [{
                    'id': h.id, 'name': h.name, 'amount': h.amount, 'code': h.code,
                    'date': format_date(self.env, h.date, date_format='d MMM'),
                } for h in pending],
            },
            'trip': {
                'id': trip.id,
                'state': trip.state,
                'distance': trip.distance,
                'amount': trip.amount,
            } if trip else False,
            'target': {
                'id': target.id,
                'progress': min(target.progress, 999.0),
                'goal': target.goal_value,
                'done': target.done_value,
            } if target else False,
            # The clinic is read under sudo ON PURPOSE. The visit is the
            # executive's own, but its clinic may not be on their route today - a
            # stand-in round, or a clinic the office moved - and the route rule
            # then refused the NAME of a place they are standing in, taking the
            # whole screen down with an AccessError. Three fields for display,
            # nothing writable. (2026-08-25)
            'visits': [{
                'id': v.id,
                'name': v.name,
                'clinic': clinic.display_name,
                'city': clinic.city or '',
                'state': v.state,
                'purpose': purpose_labels.get(v.purpose, ''),
                'outcome': outcome_labels.get(v.outcome, ''),
                # Whichever count is the truth right now. Slips only become orders at
                # check-out, so reporting order_count on an open visit told an executive
                # they had registered nothing seconds after they had.
                'cases': v.order_count if v.state == 'done' else v.case_count,
                'value': v.order_value,
                'collected': v.collected,
                'gps': v.gps_state or '',
                'pinned': clinic.is_geolocated,
                'unbanked': bool(v.state == 'done' and v.pay_mode == 'cash'
                                 and v.collected and not v.cash_banked),
                # Getting there and reaching the doctor are phone jobs, not form jobs,
                # so they belong on the card rather than two taps inside the record.
                'map_url': v.map_url or '',
                'map_address': v.map_address or '',
                'call': v.call_number or '',
                'whatsapp': v.whatsapp_number or '',
            } for v, clinic in ((v, v.partner_id.sudo()) for v in visits)],
            'suggestions': self._suggestions(today, visits),
            # What the arrows on the screen are allowed to do.
            'day': fields.Date.to_string(today),
            'is_today': today == fields.Date.context_today(self),
            'today': fields.Date.to_string(fields.Date.context_today(self)),
            'earliest': fields.Date.to_string(
                (fields.Date.context_today(self) - timedelta(days=limit))
                if limit is not None else False),
            'backdate_days': limit,
            # The doors this round opened this month. One number and a way in: an
            # executive is judged on the work they bring back AND the clinics they
            # open, and the second was visible nowhere on the screen they live in.
            # (client, 2026-08-27)
            'new_clinics': self.env['lab.new.clinics'].count_new_clinics(),
            # The stepper is hidden when there is no window to step through, so a
            # supervisor with NO limit needs saying so explicitly — otherwise `None`
            # reads as "no days back" and takes the control away from the one person
            # who can use all of them. (client, 2026-08-27)
            'any_day': limit is None,
        }

    @api.model
    def _greeting(self):
        """Said in the executive's own timezone, not the server's — a 6am round in
        Kerala must not be greeted with "good evening"."""
        now = fields.Datetime.context_timestamp(self, fields.Datetime.now())
        hour = now.hour
        if hour < 12:
            return _('Good morning')
        if hour < 17:
            return _('Good afternoon')
        return _('Good evening')

    # ------------------------------------------------------------------ suggestions
    @api.model
    def _suggestions(self, today, visits):
        """Clinics worth adding to today, drawn from the executive's own beats.

        This is the whole point of measuring coverage. A report saying "eleven clinics
        are overdue" gets read once a month by a manager; the same fact offered as "you
        are out today and these two are on your round" gets acted on. So the suggestion
        appears where the work happens rather than in an analysis menu.

        Ordered by value at stake, not by days elapsed — a clinic that never ordered and
        a clinic that ordered heavily until it went quiet are both "overdue", and only
        one of them is worth the trip.
        """
        beats = self.env['lab.beat'].search([('user_id', '=', self.env.uid)])
        mine = beats.partner_ids
        if not mine:
            return []
        already = visits.mapped('partner_id').ids
        rows = self.env['lab.coverage'].search(
            [('partner_id', 'in', mine.ids),
             ('partner_id', 'not in', (already or [0])),
             ('status', 'in', ('due', 'overdue', 'at_risk', 'never'))],
            order='value_90d desc, days_since desc', limit=SUGGEST_LIMIT)
        labels = dict(self.env['lab.coverage']._fields['status'].selection)
        return [{
            'partner_id': r.partner_id.id,
            'clinic': r.partner_id.display_name,
            'city': r.partner_id.city or '',
            'status': r.status,
            'status_label': labels.get(r.status, ''),
            'days': r.days_since or 0,
            'value': r.value_90d,
            'declining': r.declining,
        } for r in rows]

    # ------------------------------------------------------------------ track a work
    # "Where is my patient's appliance?" asked at the clinic door. The executive
    # cannot read sale.order at all - by design, an order carries pricing they are
    # not shown - so this reads it under sudo and hands back only what answers the
    # question, for THEIR routes and nothing else. (client, 2026-08-24)
    TRACK_LIMIT = 25

    @api.model
    def track_search(self, query='', partner_id=None):
        """Uninvoiced work on this person's routes.

        Searchable by order number, patient, clinic AND the work itself: a doctor
        at the door says "the Hawley for Meera" far more often than an order
        number, and `product_names` is filled on 800 of the 815 open jobs.
        `partner_id` narrows to one clinic - what the "here" chip sends when the
        executive is standing in a visit. (client, 2026-08-26)
        """
        query = (query or '').strip()
        routes = self.env.user.fw_route_ids.ids
        carried = self._track_carried_ids()
        if not routes and not carried:
            # Same rule as everywhere else in field work: no route, nothing to show.
            return {'rows': [], 'no_route': True, 'query': query}

        domain = [
            ('state', 'in', ('sale', 'done')),
            '|',
            # Only what is still open on the books on this person's routes. 19,940
            # of the orders on file are invoiced and finished with.
            '&', ('invoice_status', '!=', 'invoiced'),
                 '|', ('partner_id.team_id', 'in', routes),
                      ('partner_id.commercial_partner_id.team_id', 'in', routes),
            # And the work in their bag, whichever route its clinic is on and
            # invoiced or not: a carrier is asked about the box in their hand.
            # (client, 2026-09-15)
            ('id', 'in', carried),
        ]
        if partner_id:
            domain.append(('partner_id', 'child_of', int(partner_id)))
        if query:
            domain += ['|', '|', '|', ('name', 'ilike', query),
                       ('patient', 'ilike', query),
                       ('partner_id.name', 'ilike', query),
                       ('product_names', 'ilike', query)]
        # Oldest first, not newest: the job somebody is about to be asked about is
        # the one that has been waiting longest, and a phone shows five rows.
        # (client, 2026-08-25)
        orders = self.env['sale.order'].sudo().search(
            domain, order='date_order asc', limit=self.TRACK_LIMIT)
        return {
            'rows': [self._track_row(o) for o in orders],
            'query': query,
            'more': len(orders) == self.TRACK_LIMIT,
            'partner_id': int(partner_id) if partner_id else False,
            # The clinic the executive is actually standing in, if they are checked
            # into a visit right now. One tap then answers "what is pending for
            # THIS doctor" - the question that is really being asked.
            'here': self._track_here(),
        }

    @api.model
    def _track_carried_ids(self):
        """Orders on the boxes this person is carrying now, if deliveries exist.

        Deliveries are handed to whoever is going that way, so a carrier is asked
        about work on routes that are not theirs - and Track order, scoped to the
        route alone, showed nothing for the box in their hand. (client, 2026-09-15)
        """
        if 'lab.delivery' not in self.env:
            return []
        deliveries = self.env['lab.delivery'].sudo().search([
            ('executive_id', '=', self.env.uid), ('direction', '=', 'out'),
            ('state', 'in', ('assigned', 'out')), ('sale_order_id', '!=', False)])
        return deliveries.mapped('sale_order_id').ids

    @api.model
    def _track_here(self):
        """The clinic of the visit this person is checked into, if any."""
        visit = self.env['lab.visit'].search([
            ('user_id', '=', self.env.uid),
            ('date', '=', fields.Date.context_today(self)),
            ('state', '=', 'open'),
        ], limit=1)
        if not visit:
            return False
        return {'id': visit.partner_id.id,
                'name': visit.partner_id.sudo().display_name}

    @api.model
    def track_detail(self, order_id):
        """The journey of one job: where it has been, and when.

        Scoped the same way the search is - an order id is easy to guess, and the
        list this opens from is already confined to the person's routes, so the
        detail must be too. (client, 2026-08-25)
        """
        routes = self.env.user.fw_route_ids.ids
        order = self.env['sale.order'].sudo().browse(int(order_id)).exists()
        if not order:
            return {'ok': False}
        team = order.partner_id.team_id or order.partner_id.commercial_partner_id.team_id
        if team.id not in routes and order.id not in self._track_carried_ids():
            return {'ok': False}

        steps = [{'label': 'Order confirmed', 'when': self._track_when(order.date_order),
                  'done': True}]
        productions = self._track_productions(order)
        if productions:
            finished = productions.filtered(lambda m: m.state == 'done')
            steps.append({
                'label': 'In the lab (%d job%s)' % (len(productions),
                                                    '' if len(productions) == 1 else 's'),
                'when': self._track_when(min(productions.mapped('create_date') or [False])),
                'done': True})
            steps.append({
                'label': 'Work finished',
                'when': self._track_when(max(finished.mapped('date_finished') or [False])),
                'done': len(finished) == len(productions)})
        deliveries = self._track_deliveries(order)
        deliveries = deliveries.filtered(lambda d: d.direction == 'out') \
            if deliveries is not None else None
        if deliveries:
            out = deliveries.filtered(lambda d: d.state in ('assigned', 'out'))
            done = deliveries.filtered(lambda d: d.state == 'delivered')
            steps.append({'label': 'With %s' % (', '.join(
                (out | done).mapped('executive_id.name')) or 'the round'),
                'when': self._track_when(min(deliveries.mapped('create_date') or [False])),
                'done': True})
            steps.append({'label': 'Delivered to the clinic',
                          'when': self._track_when(max(done.mapped('delivered_datetime') or [False])),
                          'done': bool(done)})
        invoices = order.invoice_ids.filtered(lambda m: m.state == 'posted')
        steps.append({'label': 'Invoiced', 'done': bool(invoices),
                      'when': self._track_when(min(invoices.mapped('invoice_date') or [False]))})
        return {
            'ok': True,
            'id': order.id,
            'name': order.name,
            'patient': order.patient or '',
            'clinic': order.partner_id.display_name,
            'phone': order.partner_id.phone or '',
            # The lab keeps a WhatsApp number per clinic; prefer it, fall back to
            # the phone — through _wa_source, because that field only exists where
            # lab_whatsapp is installed. res.partner has no `mobile` field in
            # Odoo 19.
            # Digits stripped HERE, not in the template: a regex literal inside a
            # t-attf attribute ("/[^0-9]/g") is a compile error in QWeb and takes
            # the whole My Day template down. (2026-08-25)
            'whatsapp': ''.join(ch for ch in (_wa_source(order.partner_id) or '')
                                if ch.isdigit()),
            'promised': self._track_when(order.commitment_date),
            'steps': steps,
            # Who made it and who finished it, per operation.
            'people': self._track_people(productions),
            # For a refresh: the list line as it stands NOW, so the row can be
            # patched in place instead of re-running a search that blanks the
            # list and drops the open panel - and the moment it was read, so
            # "as of 10:42" is a fact on the screen. (client, 2026-09-09)
            'row': self._track_row(order),
            'read_at': fields.Datetime.context_timestamp(
                self, fields.Datetime.now()).strftime('%H:%M'),
            # The one line an executive reads out at the door or sends on.
            'summary': self._track_summary(order),
        }

    @api.model
    def _track_productions(self, order):
        """The order's lab jobs, or None where manufacturing is not installed.

        None, not an empty recordset of a model that may not exist: this module
        does not depend on mrp, so `self.env['mrp.production']` is exactly what
        cannot be reached on a lab without it - the same trap the delivery guard
        fell into. (2026-09-09)
        """
        if 'mrp_production_ids' not in order._fields:
            return None
        return order.mrp_production_ids.filtered(lambda m: m.state != 'cancel')

    # How many operations are worth naming on a phone. A case with thirty steps is
    # a scroll, not an answer.
    TRACK_PEOPLE_LIMIT = 12

    @api.model
    def _track_people(self, productions):
        """Who made this case, step by step - and who finished it.

        The two are separate fields and separate people: at a polishing or packing
        bench the lab records who FINISHED the work as well as who did it
        (lab_workcenter_scan). "Who made this one?" is asked at the door, and
        the answer used to be a phone call to the lab. Only steps with a name are
        listed - an operation nobody has touched says nothing. (client, 2026-09-09)
        """
        if not productions:
            return []
        workorders = productions.mapped('workorder_ids') \
            if 'workorder_ids' in productions._fields else None
        if not workorders or 'bench_user_id' not in workorders._fields:
            return []
        rows = []
        has_finisher = 'finisher_user_id' in workorders._fields
        for wo in workorders.sorted(lambda w: (w.production_id.id, w.sequence, w.id)):
            technician = wo.bench_user_id.name or ''
            finisher = (wo.finisher_user_id.name or '') if has_finisher else ''
            if not (technician or finisher):
                continue
            rows.append({
                'operation': wo.name or '',
                'station': wo.workcenter_id.display_name or '',
                'technician': technician,
                'finisher': finisher,
                'done': wo.state == 'done',
            })
            if len(rows) >= self.TRACK_PEOPLE_LIMIT:
                break
        return rows

    @api.model
    def _track_deliveries(self, order):
        """The order's boxes, or None on a lab that never installed lab_delivery.

        The guard used to fall back to ``self.env['lab.delivery'].browse()`` -
        which is the one thing that cannot work when the module is absent: the
        MODEL is missing too, so the fallback raised KeyError and took Track
        order down on exactly the install it was written to protect. Nothing,
        not an empty recordset of a model that does not exist. (2026-09-09)
        """
        return order.delivery_ids if 'delivery_ids' in order._fields else None

    @api.model
    def _track_when(self, value):
        if not value:
            return ''
        return fields.Datetime.context_timestamp(
            self, fields.Datetime.to_datetime(value)).strftime('%d %b') \
            if not isinstance(value, str) else value

    @api.model
    def _track_summary(self, order):
        stage, _tone = self._track_stage(order)
        bits = [order.name]
        if order.patient:
            bits.append(order.patient)
        bits.append(stage.lower())
        if order.commitment_date:
            bits.append('promised %s' % self._track_when(order.commitment_date))
        return ' - '.join(bits)

    @api.model
    def _track_row(self, order):
        """One line of the answer: what it is, and where it has got to."""
        stage, tone = self._track_stage(order)
        today = fields.Date.context_today(self)
        waiting = (today - order.date_order.date()).days if order.date_order else 0
        return {
            'id': order.id,
            'name': order.name,
            'patient': order.patient or '',
            'clinic': order.partner_id.display_name,
            # The appliance, so the executive can confirm it is the right job
            # before answering - "the Hawley", not just "Meera's".
            'work': (order.product_names or '')[:60],
            'date': order.date_order and order.date_order.strftime('%d %b') or '',
            'stage': stage,
            'tone': tone,
            # How long the doctor has been waiting: the thing that decides whether
            # this is a polite update or an apology.
            'waiting': waiting,
            'phone': order.partner_id.phone or '',
            'promised': self._track_when(order.commitment_date),
            'late': bool(order.commitment_date
                         and order.commitment_date < fields.Datetime.now()),
            'invoice_status': dict(
                order._fields['invoice_status']._description_selection(self.env)
            ).get(order.invoice_status, order.invoice_status or ''),
        }

    @api.model
    def _track_stage(self, order):
        """The furthest point the work has actually reached.

        Read newest-fact-first: a delivered box is delivered whatever the production
        record says, and work still being made is more useful to report than the fact
        that the order was confirmed a week ago.
        """
        deliveries = self._track_deliveries(order)
        if deliveries:
            if deliveries.filtered(lambda d: d.state == 'delivered'):
                return 'Delivered', 'good'
            if deliveries.filtered(lambda d: d.state in ('assigned', 'out')):
                return 'Out for delivery', 'mid'
        productions = self._track_productions(order)
        if productions and all(m.state == 'done' for m in productions):
            return 'Ready', 'good'
        if productions:
            return 'In the lab', 'mid'
        return 'Confirmed', 'wait'

    # ------------------------------------------------------- doctors on my round
    # The executive could see the clinics they had a VISIT for and nothing else:
    # to answer "who else is on this road, and do they owe us anything" they had
    # to ask the office. This is that list - their own routes, as cards, with the
    # two money facts that decide whether the call is worth making, their own
    # stars on top, and a button that turns a card into today's visit.
    # No form view on purpose: an executive opening res.partner gets a page of
    # accounting fields they cannot read. (client, 2026-09-02)
    # Everyone on the route, not a first page of them: an executive looking for
    # a doctor searched a list that stopped at 60 and had to guess whether the
    # name was missing or merely further down. The biggest route on this
    # database has 921 partners and their figures take about a quarter of a
    # second, so the ceiling is only here to stop a mis-set route sending a
    # phone a payload it cannot draw. (client, 2026-09-17)
    CLINIC_LIMIT = 2000

    @api.model
    def get_clinics(self, query='', favourites_only=False):
        """Everyone on this person's routes, favourites first.

        Money is read through the Collections countback for the clinics on the
        page ONLY (`partner_ids=`): per clinic the FIFO is self-contained, so a
        narrowed run returns exactly the full run's rows, and this stays a
        phone-sized query instead of a whole-ledger scan.
        """
        query = (query or '').strip()
        routes = self.env.user.fw_route_ids.ids
        if not routes:
            return {'rows': [], 'no_route': True, 'query': query,
                    'favourites_only': bool(favourites_only), 'more': False}

        favourite_ids = self.env['lab.clinic.favourite'].my_partner_ids()
        # Everyone whose Sales Route is one of theirs - what the Contacts list
        # shows for the route - not only partners flagged as clinics. The flag is
        # set when an order is confirmed, so every doctor on the route who had
        # not ordered yet was missing: PLKD has 238 partners and Field Work showed
        # 101. (client, 2026-09-17)
        domain = [('team_id', 'in', routes)]
        if favourites_only:
            domain.append(('id', 'in', favourite_ids or [0]))
        if query:
            domain += ['|', '|', '|', ('name', 'ilike', query),
                       ('city', 'ilike', query), ('street', 'ilike', query),
                       ('phone', 'ilike', query)]
        # sudo for the READ: the route leaf above is the scope, and the partner
        # rule can still hide a clinic the office has just moved between rounds
        # - which took the whole screen down with an AccessError before.
        Partner = self.env['res.partner'].sudo()
        # Favourites first, then alphabetical: the star is the executive's own
        # ordering and it must survive the search, not only the empty list.
        found = Partner.search(domain, order='name', limit=self.CLINIC_LIMIT + 1)
        more = len(found) > self.CLINIC_LIMIT
        found = found[:self.CLINIC_LIMIT]
        stars = set(favourite_ids)
        # Concatenated as RECORDSETS, not lists: the money and order helpers
        # take .ids, and a plain list of records has none.
        ordered = found.filtered(lambda p: p.id in stars) \
            + found.filtered(lambda p: p.id not in stars)

        money = self._clinic_open_money(ordered)
        orders = self._clinic_open_orders(ordered)
        visited_today = set(self.env['lab.visit'].search([
            ('user_id', '=', self.env.uid),
            ('date', '=', fields.Date.context_today(self)),
            ('state', '!=', 'cancel')]).mapped('partner_id').ids)
        rows = [{
            'id': p.id,
            'name': p.display_name,
            'address': ', '.join(bit for bit in (
                p.street, p.street2, p.city) if bit) or '',
            'phone': p.phone or '',
            # Through _wa_number, not raw digits: a ten-digit Indian number
            # with no country code opens an EMPTY wa.me chat rather than
            # failing, which is the worst of both worlds. Through _wa_source
            # for the number itself, because `whatsapp_number` is only there
            # when lab_whatsapp is installed.
            'whatsapp': _wa_number(_wa_source(p), p) or '',
            'route': p.team_id.name or p.commercial_partner_id.team_id.name or '',
            'open_receivable': money.get(p.id, 0.0),
            'open_orders': orders.get(p.id, {}).get('count', 0),
            'open_order_value': orders.get(p.id, {}).get('value', 0.0),
            'favourite': p.id in stars,
            'visited_today': p.id in visited_today,
            'map_url': self._clinic_map_url(p),
        } for p in ordered]
        return {
            'rows': rows,
            'query': query,
            'favourites_only': bool(favourites_only),
            'favourites': len(favourite_ids),
            'more': more,
            'no_route': False,
        }

    @api.model
    def _clinic_open_money(self, partners):
        """{partner_id: open receivable} through the countback.

        Residuals are dead on this ledger (nothing is ever reconciled), so this
        asks lab.collection.performance like every other screen that shows
        what a clinic owes. Absent module, absent figure - never a wrong one.
        """
        if not partners or 'lab.collection.performance' not in self.env:
            return {}
        debits = self.env['lab.collection.performance'].sudo()._open_debits(
            self.env.company, partner_ids=partners.ids)
        out = {}
        for debit in debits:
            out[debit['partner_id']] = out.get(debit['partner_id'], 0.0) \
                + debit['open']
        return {pid: round(value, 2) for pid, value in out.items()}

    @api.model
    def _clinic_open_orders(self, partners):
        """{partner_id: {count, value}} of work still open on the books.

        Same definition as the order tracker: confirmed and not yet fully
        invoiced. sudo because an executive cannot read sale.order at all -
        it carries pricing they are not shown - and only two aggregates
        come back.
        """
        if not partners:
            return {}
        rows = self.env['sale.order'].sudo()._read_group(
            [('partner_id', 'in', partners.ids),
             ('state', 'in', ('sale', 'done')),
             ('invoice_status', '!=', 'invoiced')],
            ['partner_id'], ['__count', 'amount_total:sum'])
        return {partner.id: {'count': count, 'value': value or 0.0}
                for partner, count, value in rows}

    @api.model
    def _clinic_map_url(self, partner):
        """The same `geo:` link the visit cards carry, so one clinic opens the
        same way wherever the executive taps it."""
        url, _address = _map_url_and_address(partner)
        return url or ''

    @api.model
    def toggle_favourite(self, partner_id):
        """Star or unstar a clinic for the person asking."""
        return self.env['lab.clinic.favourite'].toggle(partner_id)

    @api.model
    def add_stop(self, partner_id, day=None):
        """Add an unplanned clinic to today, from a suggestion card."""
        today = fields.Date.to_date(day) if day else fields.Date.context_today(self)
        # sudo for the GUARD only. The route rule can hide a clinic an executive is
        # legitimately standing in - covering someone else's round - and reading
        # `is_clinic` through it turned "add this stop" into an AccessError. The
        # suggestion cards that lead here are already route-scoped; this read
        # decides one thing only, whether the record is a clinic at all.
        # (client, 2026-08-26)
        partner = self.env['res.partner'].sudo().browse(int(partner_id))
        # A clinic, or anyone on this person's own routes: the Doctors / Clinics
        # panel lists the whole route, including doctors who have not ordered yet
        # and so were never flagged as clinics. (client, 2026-09-17)
        on_my_route = partner.exists() and (
            partner.team_id or partner.commercial_partner_id.team_id
        ).id in self.env.user.fw_route_ids.ids
        if not partner.exists() or not (partner.is_clinic or on_my_route):
            raise UserError(_("That is not a clinic on your round."))
        existing = self.env['lab.visit'].search([
            ('user_id', '=', self.env.uid), ('date', '=', today),
            ('partner_id', '=', partner.id), ('state', '!=', 'cancel')], limit=1)
        if existing:
            # Pressing twice on a slow connection is the normal case, not the exception.
            return existing.id
        beat = self.env['lab.beat'].search(
            [('user_id', '=', self.env.uid), ('partner_ids', 'in', partner.id)], limit=1)
        visit = self.env['lab.visit'].create({
            'partner_id': partner.id,
            'user_id': self.env.uid,
            'date': today,
            'beat_id': beat.id or False,
            'purpose': 'round',
        })
        return visit.id
