# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.tools import SQL
from odoo.exceptions import UserError

# States where moving the job is meaningless: the work is over.
CLOSED = ('done', 'cancel')


class MrpWorkorder(models.Model):
    """Moving a job to another station, by looking at it.

    The alternative on a shop floor is a technician with gloves and a dusty screen
    finding one work centre in a dropdown of forty. The scan is not a shortcut for that
    — it is the difference between the move being recorded and the job quietly being
    done at the wrong station with the system still claiming otherwise.
    """
    _inherit = 'mrp.workorder'

    def button_start(self, raise_on_invalid_state=False):
        """Start a step without letting core try to UNPLAN the job.

        `button_start` propagates the moment of the start up to the order:

            if wo.production_id.state != 'progress':
                wo.production_id.write({'date_start': fields.Datetime.now()})

        and `mrp.production.write` reads a bare `date_start` as a RESCHEDULE — so it
        unplans the order to rebuild the slots (mrp_production.py:1046). `button_unplan`
        refuses outright once any step is finished:

            "Some work orders are already done, so you cannot unplan this
             manufacturing order."

        Every job in this lab is `is_planned` — that flag only means some step once had
        a start and an end, which `button_start` itself writes — so accepting step three
        of five raised that error and the bench could not scan at all. (client,
        2026-09-09)

        `force_date` is core's own word for "this date is bookkeeping, not a reschedule":
        `mrp.workorder.write` sets it two lines away when it propagates the same dates
        (mrp_workorder.py:528). A lab that runs off scans has no slots to rebuild, and
        the choice here is only ever between keeping the plan and refusing the scan.

        It is NOT applied to a finished or cancelled order, because the same flag also
        waves through core's "You cannot move a manufacturing order once it is cancelled
        or done" — a real guard, and not one this is meant to lift.
        """
        steps = self.filtered(
            lambda wo: wo.production_id.state not in ('done', 'cancel'))
        if steps:
            super(MrpWorkorder, steps.with_context(force_date=True)).button_start(
                raise_on_invalid_state=raise_on_invalid_state)
        rest = self - steps
        if rest:
            super(MrpWorkorder, rest).button_start(
                raise_on_invalid_state=raise_on_invalid_state)

    def action_scan_move(self):
        """Opens the camera. The client calls `move_to_scanned_workcenter` with the code."""
        self.ensure_one()
        return {'type': 'ir.actions.client', 'tag': 'lab_wc_scan',
                'params': {'workorder_id': self.id, 'name': self.display_name}}

    def move_to_scanned_workcenter(self, code):
        """Reassign these work orders to the station whose label was scanned."""
        self.ensure_one()
        workcenter = self._workcenter_from_code(code)
        # Posted at the bench the job is LEAVING. Where it goes is a different matter —
        # a job is pushed to a station you do not work at all day long.
        self._check_bench_access(_('move work'))

        if self.state in CLOSED:
            raise UserError(_(
                "%(wo)s is already %(state)s, so it cannot be moved.",
                wo=self.display_name, state=dict(
                    self._fields['state'].selection).get(self.state, self.state)))
        if workcenter == self.workcenter_id:
            # Not an error: scanning the station you are already at is the natural way
            # to check you are in the right place.
            return {'moved': False, 'workcenter': workcenter.display_name,
                    'message': _("%s is already at this station.", self.display_name)}

        previous = self.workcenter_id
        label = self.display_name
        # The destination's name elevated: a bench-bound user is pushing the job
        # to a station their record rule hides.
        target = workcenter.sudo().display_name
        # Keyed on the CLOCK, not on `state`. state is computed, and a work order can
        # hold an open time record while still reading as 'ready' or 'pending' — that
        # timer is the thing that would bill the new station for work done at the old
        # one, so it is the thing to test.
        running = self.time_ids.filtered(lambda t: not t.date_end)
        was_running = bool(running)
        if was_running:
            # end_all, not button_pending: core's button_pending closes only the
            # CURRENT user's timer, and the one running is usually the
            # technician's, not the lead's who scans the move. (review, 2026-09-15)
            self.end_all()

        # Moving a job is a HAND-OVER, and must leave the same trail as one.
        #
        # Without this the job left the sender's board entirely and arrived at the
        # destination already marked accepted — by whoever accepted it at the previous
        # bench. Nobody at the new station had taken it on, so it appeared in "On the
        # bench" rather than "Arrived", the receiving bench was never prompted to accept
        # it, and the sender's "Waiting to be taken" column — the one that exists
        # precisely to catch work in nobody's hands — stayed empty. (client, 2026-08-27)
        #
        # Written elevated, after the access check on the bench it is leaving: core's
        # write reads the new station's resource, which a bench-bound user cannot
        # read, so a legitimate push to the next bench died as an access error.
        self.sudo().write({
            'workcenter_id': workcenter.id,
            'accepted_at': False,
            'accepted_by_id': False,
            'bench_user_id': False,
            'finisher_user_id': False,
            'handed_from_workcenter_id': previous.id,
            'handed_from_user_id': self.env.uid,
            'handed_over_by_id': self.env.uid,
            'handed_over_at': fields.Datetime.now(),
        })
        # `mrp.workorder` carries no chatter of its own, so the trail goes on the
        # manufacturing order — which is the record a supervisor actually follows, and
        # where a job moving between stations belongs anyway.
        if self.production_id:
            self.production_id.message_post(body=_(
                "%(wo)s moved from %(previous)s to %(target)s by scan.",
                wo=label,
                previous=previous.display_name or _('no station'),
                target=target))
        return {
            'moved': True,
            'workcenter': target,
            'was_running': was_running,
            'message': _("%(wo)s moved to %(wc)s.", wo=label, wc=target),
        }

    def _workcenter_from_code(self, code):
        code = (code or '').strip()
        if not code:
            raise UserError(_("Nothing was scanned."))
        # sudo for the LOOKUP only, as the job scan does: the bench record rule hides
        # every station but the user's own, and the station a job is pushed TO is
        # usually one they do not work at - so a bench-bound user was told "No work
        # centre carries the code" for a label on the wall. Access is still checked
        # on the bench the job is leaving. (review, 2026-09-15)
        Workcenter = self.env['mrp.workcenter'].sudo()
        # The scan code first, then the human code and the name, so a station labelled
        # before this module was installed still answers to its own printed code.
        workcenter = Workcenter.search([('scan_code', '=', code)], limit=1) \
            or Workcenter.search([('code', '=', code)], limit=1) \
            or Workcenter.search([('name', '=ilike', code)], limit=1)
        if not workcenter:
            raise UserError(_(
                "No work centre carries the code %s. Scan the label on the station, or "
                "print labels from Manufacturing → Work Centres.", code))
        if self.company_id and workcenter.company_id \
                and workcenter.company_id != self.company_id:
            raise UserError(_(
                "%(wc)s belongs to %(other)s, and this job belongs to %(mine)s.",
                wc=workcenter.display_name, other=workcenter.company_id.display_name,
                mine=self.company_id.display_name))
        return workcenter.with_env(self.env)


class MrpWorkorderHandover(models.Model):
    """Accepting a job, and passing it on.

    Odoo's own start/finish says what the LAB did; it does not say who took
    responsibility for the piece of work sitting on a bench. A job moved to another
    station — by scan or by dropdown — arrives with nobody having seen it, and the first
    anyone knows is that it never came back.

    So a move creates an obligation: the receiving station ACCEPTS. Until it does, the
    job shows on their board as incoming, and on the sending station's board as handed
    over but not yet taken.
    """
    _inherit = 'mrp.workorder'

    accepted_by_id = fields.Many2one('res.users', 'Accepted By', readonly=True, copy=False)
    accepted_at = fields.Datetime('Accepted', readonly=True, copy=False)
    handed_over_by_id = fields.Many2one('res.users', 'Handed Over By', readonly=True,
                                        copy=False)
    handed_over_at = fields.Datetime('Handed Over', readonly=True, copy=False)

    next_workorder_id = fields.Many2one(
        'mrp.workorder', compute='_compute_next_workorder', string='Goes To')
    # Computed alongside, NOT `related='next_workorder_id.workcenter_id'`: a related
    # built on a non-stored compute makes Odoo try to resolve the chain in SQL, and any
    # write that touches workcenter_id then dies with "cannot convert to SQL because it
    # is not stored".
    next_workcenter_id = fields.Many2one(
        'mrp.workcenter', compute='_compute_next_workorder', string='Next Station')
    is_accepted = fields.Boolean(compute='_compute_is_accepted', search='_search_is_accepted')

    @api.depends('accepted_at')
    def _compute_is_accepted(self):
        for wo in self:
            wo.is_accepted = bool(wo.accepted_at)

    def _search_is_accepted(self, operator, value):
        # A non-stored computed used in ANY domain needs this, or the station board's
        # own filters raise "cannot convert to SQL" the first time they are used.
        positive = bool(value) if operator == '=' else not bool(value)
        return [('accepted_at', '!=' if positive else '=', False)]

    @api.depends('needed_by_workorder_ids.state', 'production_id.workorder_ids.sequence',
                 'arch', 'production_id.workorder_ids.arch')
    def _compute_next_workorder(self):
        """Where this job goes when this station is finished with it.

        Explicit dependencies first, because a routing that declares them means it. A
        lab that runs a plain sequence — which most do — falls back to the next
        operation by sequence, so the board still says where the piece is going instead
        of leaving the head to guess.
        """
        for wo in self:
            # On a two-arch case the upper and the lower are separate chains of
            # the same routing, so "the next step" is the next step OF THIS
            # ARCH. Without this the board walks a piece straight out of the
            # upper chain and into the lower one. (client, 2026-09-12)
            def same_arch(w, this=wo):
                return not this.arch or w.arch == this.arch

            following = wo.needed_by_workorder_ids.filtered(
                lambda w: w.state not in ('done', 'cancel')
                and same_arch(w)).sorted('sequence')
            if not following and wo.production_id:
                following = wo.production_id.workorder_ids.filtered(
                    lambda w: w.id != wo.id and w.state not in ('done', 'cancel')
                    and same_arch(w)
                    and (w.sequence, w.id) > (wo.sequence, wo.id)).sorted(
                        lambda w: (w.sequence, w.id))
            wo.next_workorder_id = following[:1].id or False
            wo.next_workcenter_id = following[:1].workcenter_id.id or False

    # ------------------------------------------------------------------ actions
    def action_station_accept(self):
        """This station takes the job on."""
        # Same repair as the scan, for the board's own Accept button.
        self.env['mrp.production']._lab_repair_premature_to_close(
            self.production_id.ids)
        for wo in self:
            wo._check_bench_access(_('accept work'))
            if wo.state in CLOSED:
                raise UserError(_("%s is already finished.", wo.display_name))
            if not wo.accepted_at:
                wo.write({'accepted_by_id': self.env.uid,
                          'accepted_at': fields.Datetime.now()})
                if wo.production_id:
                    wo.production_id.message_post(body=_(
                        "%(wo)s accepted at %(wc)s by %(who)s.",
                        wo=wo.display_name, wc=wo.workcenter_id.display_name,
                        who=self.env.user.name))
            # Start the clock too: a job accepted but not started reads as idle on every
            # capacity report the lab has.
            if wo.state not in ('progress',):
                wo.button_start(raise_on_invalid_state=False)
        return True

    def action_station_handover(self):
        """Finish here and pass the job to the next station."""
        for wo in self:
            wo._check_bench_access(_('hand work on'))
            if wo.state in CLOSED:
                raise UserError(_("%s is already finished.", wo.display_name))
            if not wo.accepted_at:
                raise UserError(_(
                    "%s has not been accepted at this station yet, so there is nothing "
                    "to hand over.", wo.display_name))
            # A job leaves a bench with a name on it. Handing on work "nobody" did is
            # how the lab lost track of who made what: the time lands on the station,
            # the person is unknown, and a redo has nobody to learn from. So the board
            # will not finish a step until the lead has said whose it was.
            # (client, 2026-08-29)
            if not wo.bench_user_id:
                raise UserError(_(
                    "%s has no technician named. Choose who did the work under "
                    "\"Technician\" before handing it on — the station lead gives out the work.",
                    wo.display_name))
            # The same rule for the second name, where the bench asks for one.
            if wo.workcenter_id.is_finisher and not wo.finisher_user_id:
                raise UserError(_(
                    "%(wo)s has no finishing technician named, and %(wc)s records "
                    "one. Choose who finished it before handing it on.",
                    wo=wo.display_name, wc=wo.workcenter_id.display_name))
            following = wo.next_workorder_id
            wo.button_finish()
            wo.write({'handed_over_by_id': self.env.uid,
                      'handed_over_at': fields.Datetime.now()})
            # The receiving job remembers where it came from. Without this the sending
            # head has no way to see that nobody picked it up, which is the whole gap
            # this is meant to close.
            if following:
                following.write({'handed_from_workcenter_id': wo.workcenter_id.id,
                                 'handed_from_user_id': self.env.uid})
            if wo.production_id:
                wo.production_id.message_post(body=_(
                    "%(wo)s finished at %(wc)s and handed to %(next)s.",
                    wo=wo.display_name, wc=wo.workcenter_id.display_name,
                    next=following.workcenter_id.sudo().display_name or _('nobody — last step')))
        return True


class MrpWorkorderResponsibility(models.Model):
    """Who is answerable for a job, and until when.

    Handing a job on is not the end of the sending head's responsibility — it ends when
    somebody at the receiving bench ACCEPTS it. Between those two moments the job is in
    nobody's hands, and that gap is where work is lost: the sender believes it is gone,
    the receiver has not seen it, and neither is looking.

    So the next station's work order carries a stamp of where it came from, and the
    SENDER's board keeps showing it until it is accepted.
    """
    _inherit = 'mrp.workorder'

    # Who at this bench is actually doing it. A station is not a person: a head with six
    # technicians and eleven jobs needs to say which is whose, or "at Trimming" means
    # only that it is somewhere in that room.
    bench_user_id = fields.Many2one(
        'res.users', string='Technician', copy=False, index='btree_not_null',
        help="The person at this work centre carrying out this job.")
    # When the lead gave it out, and who did. Without these, "the job sat for two days"
    # cannot be split into the part the lead is answerable for (nobody was given it) and
    # the part the technician is (they had it and did not start).
    bench_assigned_at = fields.Datetime('Given Out', readonly=True, copy=False)
    bench_assigned_by_id = fields.Many2one(
        'res.users', string='Given Out By', readonly=True, copy=False)
    # Who FINISHED it, where that is a different person from who made it - polishing
    # and packing benches. Asked for only where the work centre says so
    # (is_finisher), and cleared with the rest when the job moves or starts again:
    # it is a fact about this step at this bench. (client, 2026-09-09)
    finisher_user_id = fields.Many2one(
        'res.users', string='Finishing Technician', copy=False,
        index='btree_not_null',
        help="The person who finished this job, where the work centre records that "
             "separately from the technician who carried it out.")
    # ------------------------------------------------------------ whose work it is
    # A job has up to two people on it: the technician who carried it out and,
    # at a bench that records one, the technician who finished it. Both did work
    # on that job, so both count it - and a job somebody did AND finished is one
    # job, not two. Every screen that counts a person's day goes through the
    # methods below, so none of them can drift from the others.
    # (client, 2026-09-10)
    @api.model
    def _person_leaf(self, user_ids):
        """Domain fragment: the operations that belong to these people, either way."""
        ids = [int(u) for u in user_ids]
        return ['|', ('bench_user_id', 'in', ids), ('finisher_user_id', 'in', ids)]

    @api.model
    def _person_credit(self, start, end):
        """SQL: one row per (operation, person) for the day, and in which role.

        A technician is credited with a job on the day they were given it,
        accepted it or handed it on - one job, whichever of the three fell in
        the day. A finisher is credited on the day it was handed on, because
        that is the moment their name goes on it. Somebody who did AND finished
        the same job appears once, as its technician: the `<>` is what keeps the
        two roles from counting one job twice.
        """
        return SQL("""
            CROSS JOIN LATERAL (
                SELECT wo.bench_user_id AS person, TRUE AS as_tech
                 WHERE wo.bench_user_id IS NOT NULL
                   AND (wo.bench_assigned_at >= %(s)s AND wo.bench_assigned_at < %(e)s
                     OR wo.accepted_at      >= %(s)s AND wo.accepted_at      < %(e)s
                     OR wo.handed_over_at   >= %(s)s AND wo.handed_over_at   < %(e)s)
                UNION ALL
                SELECT wo.finisher_user_id, FALSE
                 WHERE wo.finisher_user_id IS NOT NULL
                   AND wo.finisher_user_id IS DISTINCT FROM wo.bench_user_id
                   AND wo.handed_over_at >= %(s)s AND wo.handed_over_at < %(e)s
            ) p
        """, s=start, e=end)

    @api.model
    def _day_counts_by_person_range(self, days, tz_name=None, user_ids=None,
                                    workcenter=None):
        """{(day, user_id): jobs} for a RANGE of days, in one pass.

        The same credit rule as `_day_counts_by_person`, asked once for a whole
        week instead of once per day. The targets report was running this
        LATERAL join seven times and then the stored recount ran it seven more,
        which was most of its half-second. (measured 2026-09-12)

        A job credited on two different days - given out on Monday, handed on on
        Wednesday - counts on both, exactly as asking day by day would: the
        stamps are exploded into local days and counted distinctly, never
        summed into one.

        The timezone defaults to the lab's (`lab.station._lab_tz`), the one
        `_day_window` is built on - never the caller's own, or a stored recount
        changed with whoever ran it. (review, 2026-09-15)
        """
        if not days:
            return {}
        wanted = [int(u) for u in user_ids] if user_ids is not None else None
        if wanted is not None and not wanted:
            return {}
        Station = self.env['lab.station']
        start, _e = Station._day_window(days[0])
        _s, end = Station._day_window(days[-1])
        self.flush_model()
        self.env.cr.execute(SQL(
            """
            SELECT p.day, p.person, count(DISTINCT wo.id) AS jobs
              FROM mrp_workorder wo
              CROSS JOIN LATERAL (
                  SELECT wo.bench_user_id AS person,
                         (stamp AT TIME ZONE 'UTC' AT TIME ZONE %(tz)s)::date AS day
                    FROM unnest(ARRAY[wo.bench_assigned_at, wo.accepted_at,
                                      wo.handed_over_at]) AS stamp
                   WHERE wo.bench_user_id IS NOT NULL
                     AND stamp >= %(s)s AND stamp < %(e)s
                  UNION ALL
                  SELECT wo.finisher_user_id,
                         (wo.handed_over_at AT TIME ZONE 'UTC' AT TIME ZONE %(tz)s)::date
                   WHERE wo.finisher_user_id IS NOT NULL
                     AND wo.finisher_user_id IS DISTINCT FROM wo.bench_user_id
                     AND wo.handed_over_at >= %(s)s AND wo.handed_over_at < %(e)s
              ) p
             WHERE TRUE %(people)s %(bench)s
             GROUP BY p.day, p.person
            """,
            tz=tz_name or Station._lab_tz().zone, s=start, e=end,
            people=SQL("AND p.person = ANY(%s)", wanted) if wanted is not None else SQL(""),
            bench=SQL("AND wo.workcenter_id = %s", workcenter.id) if workcenter else SQL(""),
        ))
        return {(day, person): jobs
                for day, person, jobs in self.env.cr.fetchall()}

    @api.model
    def _day_counts_by_person(self, start, end, user_ids=None, workcenter=None):
        """{user_id: jobs} for a day: what each person did or finished."""
        wanted = [int(u) for u in user_ids] if user_ids is not None else None
        if wanted is not None and not wanted:
            return {}
        # Raw SQL reads the table, not the cache: a stamp written in this
        # transaction and not yet flushed would simply be missing.
        self.flush_model()
        self.env.cr.execute(SQL(
            """
            SELECT p.person, count(*) AS jobs
              FROM mrp_workorder wo
              %(credit)s
             WHERE TRUE %(people)s %(bench)s
             GROUP BY p.person
            """,
            credit=self._person_credit(start, end),
            people=SQL("AND p.person = ANY(%s)", wanted) if wanted is not None else SQL(""),
            bench=SQL("AND wo.workcenter_id = %s", workcenter.id) if workcenter else SQL(""),
        ))
        return {person: jobs for person, jobs in self.env.cr.fetchall()}

    @api.model
    def _dft_mine_leaf(self, field_name, uid):
        """What "my work orders" means on a tile: either role, once.

        A filter tile with "Only My Records" narrows on one field. On this
        model that is half the answer: a job carries the technician who
        carried it out and, at a finishing bench, the technician who
        finished it, and a polisher's tile counted nothing. The tiles module
        asks the model, and the model answers with the same rule every other
        count on this floor uses. (client, 2026-09-10)
        """
        if field_name in ('bench_user_id', 'finisher_user_id'):
            return self._person_leaf([uid])
        return [(field_name, '=', uid)]

    @api.model
    def _counts_by_person(self, domain, user_ids=None):
        """{user_id: operations} over any domain, crediting both roles once each.

        For a period measured by hand-over - a week of finished work, say - where
        both roles are stamped by the same event, so no per-role window is needed.
        """
        wanted = [int(u) for u in user_ids] if user_ids is not None else None
        if wanted is not None and not wanted:
            return {}
        query = self.sudo()._search(domain)
        self.flush_model()
        self.env.cr.execute(SQL(
            """
            SELECT p.person, count(*) AS jobs
              FROM mrp_workorder wo
              CROSS JOIN LATERAL (
                   SELECT DISTINCT u AS person
                     FROM unnest(ARRAY[wo.bench_user_id, wo.finisher_user_id]) u
                    WHERE u IS NOT NULL
              ) p
             WHERE wo.id IN %(ids)s %(people)s
             GROUP BY p.person
            """,
            ids=query.subselect(),
            people=SQL("AND p.person = ANY(%s)", wanted) if wanted is not None else SQL(""),
        ))
        return {person: jobs for person, jobs in self.env.cr.fetchall()}

    # For the form: a field the view can test without reading through a
    # relation, which an `invisible=` expression cannot do.
    workcenter_is_finisher = fields.Boolean(
        related='workcenter_id.is_finisher', string='Bench Names a Finisher')

    # The case behind the operation, on the operation itself. A list of work
    # orders named only by station and product cannot be read by anyone in this
    # lab: they say a job out loud by its sales order, its doctor and its
    # patient, and looking each one up was the reason the list went unused.
    # Not stored - relateds over 125,000 rows are a migration, and a drill-down
    # list needs to READ them, not sort by them. (client, 2026-09-09)
    sale_order_id = fields.Many2one(
        related='production_id.sale_id', string='Case', readonly=True)
    clinic_id = fields.Many2one(
        related='production_id.sale_id.partner_id', string='Customer', readonly=True)
    patient = fields.Char(
        related='production_id.sale_id.patient', string='Patient', readonly=True)
    # When the case was ordered: the date every other desk dates the job by,
    # and the one that says at a glance which of two rows has waited longer.
    # (client, 2026-09-10)
    order_date = fields.Date(
        compute='_compute_order_date', string='Ordered',
        help="The day the case was ordered, in the lab's own time.")

    @api.depends('production_id.sale_id.date_order')
    def _compute_order_date(self):
        # A date, not the order's timestamp: the list is read for WHICH DAY
        # a case came in, and "Aug 20, 3:25 PM" is a column twice as wide
        # saying the same thing. The day HERE, not the day in UTC.
        for wo in self:
            when = wo.production_id.sale_id.date_order
            wo.order_date = self.env['lab.station']._lab_time(when).date() \
                if when else False

    # WHICH ARCH THIS JOB IS. A case sold as "UL" is one appliance in two
    # pieces: the upper and the lower are bent, acrylised, trimmed and polished
    # separately, often days apart, and either can be remade without the other.
    # The sale line says UL; the FLOOR needs two jobs, so the work orders are
    # split into two chains and each says which arch it is. Empty on every
    # single-arch case, where the sale line's own U/L is the whole answer.
    # (client, 2026-09-12)
    arch = fields.Selection(
        [('upper', 'Upper'), ('lower', 'Lower')], string='Arch',
        copy=False, index='btree_not_null',
        help="On a case sold as Upper & Lower, which of the two pieces this "
             "job makes. Empty when the case is a single arch.")

    handed_from_workcenter_id = fields.Many2one(
        'mrp.workcenter', string='Came From', readonly=True, copy=False,
        index='btree_not_null')
    handed_from_user_id = fields.Many2one(
        'res.users', string='Sent By', readonly=True, copy=False)

    # ------------------------------------------------------------------ access
    def _bench_role(self, workcenter=None):
        """What this user is allowed to do at a station: 'manager', 'lead', 'tech'
        or False.

        Checked in the METHODS and not only by record rules, because the rules are
        opt-in: somebody without the bench group still reaches these buttons from the
        work order form, and "who may hand a job on" is a rule about the work, not about
        which list you can see.

        Leadership is read from the STATION, not from a global group. A lab has a lead
        at Acrylisation and a different one at Wire Bending; `mrp.group_mrp_manager` is
        held by every manufacturing user here, so testing it would have meant "everybody
        is a lead" — which is how a job ends up reassigned by the person who did not
        want it. (client, 2026-08-26)
        """
        self and self.ensure_one()
        workcenter = (workcenter or self.workcenter_id).sudo()
        user = self.env.user
        if self.env.su or user.has_group(
                'lab_workcenter_scan.group_production_manager'):
            return 'manager'
        if user in workcenter.head_user_ids:
            return 'lead'
        if user in workcenter.users:
            return 'tech'
        # A manufacturing manager works anywhere — covering a shift or chasing a job
        # both mean reaching a bench you are not posted to. They still do not GIVE OUT
        # work: that is the station lead's call, and on this database the manager group
        # is held by every manufacturing user, so treating it as authority to reassign
        # would mean nobody's queue was their own.
        if user.has_group('mrp.group_mrp_manager'):
            return 'floor'
        # A station nobody has been posted to yet. Refusing everybody there would stop
        # the floor dead on the day this is installed, so any manufacturing user may
        # work at an unstaffed bench — but giving work OUT still needs a lead, which is
        # what makes posting people worth doing.
        if not workcenter.head_user_ids and not workcenter.users \
                and user.has_group('mrp.group_mrp_user'):
            return 'tech'
        return False

    def _check_bench_access(self, what, workcenter=None, leads_only=False):
        workcenter = workcenter or self.workcenter_id
        role = self._bench_role(workcenter)
        if not role:
            raise UserError(_(
                "You are not posted to %(wc)s, so you cannot %(what)s there. Ask an "
                "administrator to add you to that work centre.",
                wc=workcenter.display_name, what=what))
        if leads_only and role in ('tech', 'floor'):
            raise UserError(_(
                "Only the production lead of %(wc)s can %(what)s. A technician does the "
                "work; who does which job is the lead's call. Ask an administrator to "
                "add you to Production Leads on that work centre.",
                wc=workcenter.display_name, what=what))
        return role

    def action_assign_finisher(self, user_id):
        """Name who finished the job, at a bench that records that separately.

        Deliberately NOT leads-only: the technician standing at a polishing bench
        is the one who knows who finished it, and a rule that sends them to find
        the lead is a rule that gets worked around by naming the lead.
        """
        self.ensure_one()
        self._check_bench_access(_('name the finishing technician'))
        if not user_id:
            raise UserError(_("Choose who finished the job."))
        self.write({'finisher_user_id': int(user_id)})
        if self.production_id:
            self.production_id.message_post(body=_(
                "%(wo)s finished by %(who)s at %(wc)s.", wo=self.display_name,
                who=self.finisher_user_id.name, wc=self.workcenter_id.display_name))
        return True

    def _step_after(self):
        """The step this one hands to: the next of ITS OWN arch by (sequence, id).

        Not `next_workorder_id`, which skips finished steps - right for "where is
        it going", wrong for "has anyone built on this": once the next bench had
        accepted AND handed on, the undo guard looked straight past it and reset
        this step under a finished one. A step cancelled off its queue before
        anyone took it never received the job, so it is passed over.

        Returned elevated: the next bench is usually one a bench-bound user
        cannot read, and a hidden successor must not read as no successor.
        (review, 2026-09-15)
        """
        self.ensure_one()
        this = self.sudo()
        later = this.production_id.workorder_ids.filtered(
            lambda w: w.id != this.id
            and (w.arch or '') == (this.arch or '')
            and (w.sequence, w.id) > (this.sequence, this.id)
            and not (w.state == 'cancel' and not w.accepted_at and not w.handed_over_at))
        return later.sorted(lambda w: (w.sequence, w.id))[:1]

    def _undo_blocked_by(self):
        """The next step if it has already taken this job on, else an empty set."""
        following = self._step_after()
        if following and (following.accepted_at or following.handed_over_at
                          or following.state in CLOSED):
            return following
        return following.browse()

    def action_station_undo_scan(self):
        """Take back the last scan on this job — the one made by mistake.

        A scan is one tap with gloves on, and the wrong card gets scanned. Until
        now the only way back was a redo, which starts the whole case again from
        the first bench and tells the lab this case went wrong - a lie about a
        mis-scan, and a lie the redo reports then carry.

        What comes back is exactly what the scan wrote:
          handed on  -> un-finish it; the job is on this bench again, still
                        accepted, still under the same technician's name
          accepted   -> the acceptance and the clock it started

        It stops where the undo would rewrite somebody else's work: once the next
        bench has accepted the job, taking it back is their conversation to have.
        Every undo is written to the manufacturing order - a scan taken back
        silently is worse than the mis-scan. (client, 2026-09-09)
        """
        for wo in self:
            wo._check_bench_access(_('undo a scan'))
            production = wo.production_id
            if production and production.state in CLOSED:
                raise UserError(_(
                    "%s is closed, so a scan on it cannot be taken back.",
                    production.name))
            if wo.handed_over_at:
                blocker = wo._undo_blocked_by()
                if blocker:
                    raise UserError(_(
                        "%(wo)s has already been taken on at %(next)s. Ask them to "
                        "send it back rather than taking it out of their hands.",
                        wo=wo.display_name,
                        next=blocker.workcenter_id.display_name))
                # Elevated: it is the stamp this bench's own hand-over wrote, on a
                # step a bench-bound user usually cannot see.
                following = wo._step_after()
                # State first and alone: core refuses a change to `qty_produced`
                # while the operation still reads as done, and it judges the state
                # as it stands at the START of the write - so both in one dict is
                # refused however the dict is ordered. (see _lab_reset_for_redo)
                wo.write({'state': 'progress'})
                wo.write({'qty_produced': 0.0, 'handed_over_at': False,
                          'handed_over_by_id': False})
                if following:
                    following.write({'handed_from_workcenter_id': False,
                                     'handed_from_user_id': False})
                # Back on the bench means back on the clock: it was accepted here
                # and it is here again. Only if no record is still open, or the
                # undo leaves the job billing two timers at once.
                if not wo.time_ids.filtered(lambda t: not t.date_end):
                    wo.button_start(raise_on_invalid_state=False)
                body = _("Hand-over of %(wo)s at %(wc)s taken back by %(who)s — "
                         "the job is on the bench again.",
                         wo=wo.display_name, wc=wo.workcenter_id.display_name,
                         who=self.env.user.name)
            elif wo.accepted_at:
                # The timer this scan started, stopped: a job nobody is standing at
                # must not keep billing the bench.
                # Every open timer, not just this user's (see move_to_scanned_workcenter).
                if wo.time_ids.filtered(lambda t: not t.date_end):
                    wo.end_all()
                wo.write({'accepted_at': False, 'accepted_by_id': False})
                body = _("Acceptance of %(wo)s at %(wc)s taken back by %(who)s — "
                         "the job is waiting to be taken again.",
                         wo=wo.display_name, wc=wo.workcenter_id.display_name,
                         who=self.env.user.name)
            else:
                raise UserError(_(
                    "There is no scan to take back on %s: it has not been accepted "
                    "at this station.", wo.display_name))
            if production:
                production.message_post(body=body)
        return True

    def action_assign_bench_user(self, user_id):
        """Give the job to a named person at this bench.

        Works on any number of work orders at once: a lead handing out the morning's
        queue picks six jobs and one name, and doing that one job at a time is how a
        board stops being used.
        """
        user = self.env['res.users'].browse(int(user_id)) if user_id else False
        now = fields.Datetime.now()
        for wo in self:
            # Assigning is the lead's job: a technician doing the work should not be
            # able to push it onto a colleague.
            wo._check_bench_access(_('give out work'), leads_only=True)
            if wo.state in CLOSED:
                raise UserError(_("%s is finished.", wo.display_name))
            if user and wo.workcenter_id.users and user not in wo.workcenter_id.users \
                    and user not in wo.workcenter_id.head_user_ids:
                raise UserError(_(
                    "%(who)s does not work at %(wc)s. Add them to the work centre "
                    "first — a job assigned to somebody who is not there is a job "
                    "nobody picks up.",
                    who=user.name, wc=wo.workcenter_id.display_name))
            if wo.bench_user_id == user:
                continue
            wo.write({
                'bench_user_id': user.id if user else False,
                'bench_assigned_at': now if user else False,
                'bench_assigned_by_id': self.env.uid if user else False,
            })
            # sale_custom records the technician of record in `user_id`; keep it in step
            # when it exists, so the job card and every existing report stay right.
            if 'user_id' in wo._fields and user:
                wo.user_id = user.id
            if wo.production_id:
                wo.production_id.message_post(body=_(
                    "%(wo)s given to %(who)s at %(wc)s.",
                    wo=wo.display_name, who=user.name if user else _('nobody'),
                    wc=wo.workcenter_id.display_name))
        return True
