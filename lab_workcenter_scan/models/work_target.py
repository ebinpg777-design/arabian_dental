# -*- coding: utf-8 -*-
"""How much work a technician is meant to get through in a day, and how much they did.

A bench lead already knows who is carrying what right now, and the board already
counts what each person took or finished on a given day. What the floor could not
say was whether that number was GOOD. A target turns a count into an answer:
eleven is a fine day for one person on Acrylisation and a thin one for another on
Trimming, and only the manager who set the number knows which.

A target belongs to a person and a day. Its work centre is OPTIONAL, and the two
readings that matters:

  * with a bench   - "twelve at Trimming today", the number the station board holds
                     up against that bench's own count;
  * without one    - "twenty today, wherever you are", one figure for the person's
                     whole day across every bench they touch.

The board prefers the bench target when there is one and falls back to the day's,
so a lab can run either way - or both - without being made to choose up front.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class WorkTarget(models.Model):
    _name = 'lab.work.target'
    _description = "Technician's Daily Target"
    _order = 'date desc, user_id'
    _rec_name = 'user_id'

    date = fields.Date(required=True, index=True,
                       default=lambda self: self.env['lab.station']._lab_today())
    user_id = fields.Many2one('res.users', string='Technician', required=True,
                              index=True, ondelete='cascade')
    workcenter_id = fields.Many2one(
        'mrp.workcenter', string='Work Centre', index=True, ondelete='cascade',
        help="Leave empty for a target that covers the whole day, whichever bench "
             "the technician works at.")
    target = fields.Integer(
        string='Target', required=True, default=0,
        help="How many jobs this person is meant to take or finish on this day.")
    company_id = fields.Many2one('res.company', required=True,
                                 default=lambda self: self.env.company)

    # Stored, because a report has to be able to group and total it, and a count
    # over three date columns of a live table is not something to recompute on
    # every read. Kept fresh by `action_refresh`, by the nightly cron, and by the
    # station board for the day it is showing.
    done_count = fields.Integer(string='Done', readonly=True, copy=False)
    # `aggregator`, not `group_operator`: the old name has been deprecated since
    # Odoo 18 and warns on every registry load. (2026-09-12)
    achieved_pct = fields.Float(string='Achieved %', readonly=True, copy=False,
                                aggregator='avg')
    met = fields.Boolean(string='Target Met', readonly=True, copy=False)
    refreshed_at = fields.Datetime(string='Counted At', readonly=True, copy=False)

    def init(self):
        """One target per person per day per bench - including "no bench".

        A plain UNIQUE will not do it. `workcenter_id` is nullable, and in
        Postgres a NULL never equals another NULL, so a UNIQUE across it happily
        accepts a second whole-day target for the same person on the same day -
        two different numbers, and nothing to say which one the board should
        hold up. Two PARTIAL indexes, one for each side of that null, say what
        was actually meant. (client, 2026-09-09)
        """
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS lab_work_target_bench_uniq
                ON lab_work_target (date, user_id, workcenter_id, company_id)
             WHERE workcenter_id IS NOT NULL;
            CREATE UNIQUE INDEX IF NOT EXISTS lab_work_target_whole_day_uniq
                ON lab_work_target (date, user_id, company_id)
             WHERE workcenter_id IS NULL;
        """)

    @api.constrains('target')
    def _check_target(self):
        for row in self:
            if row.target < 0:
                raise ValidationError(_("A target cannot be negative."))

    # `_compute_display_name`, not `name_get`: Odoo 19 never calls the latter,
    # so every breadcrumb and many2one showed the bare technician's name.
    @api.depends('user_id.name', 'date', 'workcenter_id.display_name')
    def _compute_display_name(self):
        for row in self:
            where = row.workcenter_id.display_name or _('any bench')
            row.display_name = '%s · %s · %s' % (
                row.user_id.name or '', format(row.date or ''), where)

    # ------------------------------------------------------------------ counting
    @api.model
    def _done_for(self, day, user_ids, workcenter=None):
        """Jobs each of `user_ids` took or finished on `day`, per the board's own rule.

        Deliberately the same rule the station board counts by - the same day
        window, the same three stamps, and the same crediting of the finishing
        technician - so a target and the chip beside it can never disagree
        about what "a day's work" means.
        """
        if not user_ids:
            return {}
        start, end = self.env['lab.station']._day_window(day)
        return self.env['mrp.workorder'].sudo()._day_counts_by_person(
            start, end, list(user_ids), workcenter)

    def action_refresh(self):
        """Recount these rows. Safe to run at any time; it only reads work orders.

        Grouped by BENCH and counted over the whole span of days at once. It
        used to ask per (day, bench), so refreshing a week for one bench was
        seven round trips of a LATERAL join - and the targets report called
        this before doing the same work again for itself.
        (measured 2026-09-12)
        """
        by_bench = {}
        for row in self:
            by_bench.setdefault(row.workcenter_id, self.browse())
            by_bench[row.workcenter_id] |= row
        now = fields.Datetime.now()
        Workorder = self.env['mrp.workorder'].sudo()
        for workcenter, bench_rows in by_bench.items():
            days = sorted(set(bench_rows.mapped('date')))
            # The lab's day, never the refresher's: the hourly cron runs as a
            # user with no timezone, so the stored count flipped between UTC
            # and local with whoever refreshed last. (review, 2026-09-15)
            counts = Workorder._day_counts_by_person_range(
                days, user_ids=bench_rows.mapped('user_id').ids,
                workcenter=workcenter or None)
            for row in bench_rows:
                done = counts.get((row.date, row.user_id.id), 0)
                row.done_count = done
                # A FRACTION, not a number out of 100. The views draw this with
                # Odoo's percentage widget, which multiplies by 100 itself - so
                # storing 50.0 for half a day displayed as 5000%.
                # (client, 2026-09-14)
                row.achieved_pct = (done / row.target) if row.target else 0.0
                row.met = bool(row.target) and done >= row.target
                row.refreshed_at = now
        return True

    @api.model
    def _cron_refresh(self, days_back=2):
        """Keep the report honest without anybody pressing anything.

        Two days back by default: today is still moving, and yesterday can still
        gain a late hand-over from an evening shift.
        """
        today = self.env['lab.station']._lab_today()
        rows = self.search([('date', '>=', fields.Date.subtract(today, days=days_back))])
        rows.action_refresh()
        return len(rows)

    # ------------------------------------------------------------------ lookups
    @api.model
    def day_targets(self, day, user_ids=None):
        """{user_id: target} for a day across the whole floor.

        The whole-day target where one is set, else the bench targets added
        up - the report's rule, made available to the flow board, which reads
        a person's day across every bench they sat at. Nobody with a target
        of nothing is returned. (client, 2026-09-10)
        """
        domain = [('date', '=', day)]
        if user_ids is not None:
            if not user_ids:
                return {}
            domain.append(('user_id', 'in', list(user_ids)))
        whole, benches = {}, {}
        for row in self.sudo().search(domain):
            uid = row.user_id.id
            if row.workcenter_id:
                benches[uid] = benches.get(uid, 0) + row.target
            else:
                whole[uid] = row.target
        out = dict(benches)
        out.update(whole)
        return {uid: t for uid, t in out.items() if t}

    @api.model
    def targets_for(self, day, user_ids, workcenter=None, with_scope=False):
        """{user_id: target} for a day - the bench's own if set, else the day's.

        Used by the station board, which asks about one bench but must still show
        a person's whole-day target when that is the only one anybody set.

        With `with_scope`, each value is `(target, scope)` where scope is 'bench'
        or 'day'. The caller needs it because the two targets are not measured
        against the same thing: a bench target is held up against what the
        person did AT THIS BENCH, a whole-day target against what they did
        everywhere. Counting a whole-day target against one bench is how a
        technician who works across three of them was shown short at all three.
        (client, 2026-09-12)
        """
        if not user_ids:
            return {}
        user_ids = list(user_ids)
        rows = self.sudo().search([('date', '=', day), ('user_id', 'in', user_ids)])
        whole_day, at_bench = {}, {}
        for row in rows:
            if not row.workcenter_id:
                whole_day[row.user_id.id] = row.target
            elif workcenter and row.workcenter_id.id == workcenter.id:
                at_bench[row.user_id.id] = row.target
        if not with_scope:
            out = dict(whole_day)
            out.update(at_bench)
            return out
        # A ZERO is not a target. Set Daily Targets writes 0 for everybody the
        # manager did not give a number to, so treating it as a whole-day
        # target switched those people to counting across every bench and put
        # a globe on chips with no target at all - 3,065 such rows existed the
        # day this was found. (client, 2026-09-14)
        out = {uid: (t, 'day') for uid, t in whole_day.items() if t}
        # The bench's own number wins where there is one: it was set about THIS
        # bench, so it is answered by this bench's work.
        out.update({uid: (t, 'bench') for uid, t in at_bench.items() if t})
        return out

    @api.model
    def whole_day_people(self, day, user_ids, workcenter=None):
        """Of `user_ids`, those whose target for `day` is not tied to a bench.

        Their day is counted across every bench they touched, and the board's
        filter shows the same set - one rule, so the number and the list it
        opens can never disagree. (client, 2026-09-12)
        """
        scoped = self.targets_for(day, user_ids, workcenter, with_scope=True)
        return {uid for uid, (_target, scope) in scoped.items() if scope == 'day'}
