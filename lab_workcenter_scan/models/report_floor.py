# -*- coding: utf-8 -*-
"""Shared ground for every sheet the floor carries.

Four helpers, and each one exists because printing without it produces a document that
is confidently wrong:

`_live_domain`   89.6% of this lab's 23,536 "open" manufacturing orders — 21,099 of them —
                 sit on a sales order whose outgoing delivery is already DONE. The
                 appliance is on the doctor's shelf; the order was simply never closed
                 after the migration. Any sheet built on `state not in (done, cancel)`
                 therefore prints mostly ghosts, and the first supervisor who chases one
                 finds a signed delivery note and never picks the sheet up again. The
                 live set is 2,437 orders across 807 clinics, and that is what floor
                 paper must show. (measured 2026-08-26)

`_patient_label` 397 of 23,442 cases have no patient at all and 9,010 carry leading
                 spaces or a trailing parenthetical. A sheet that prints them raw looks
                 like a fault in the printer.

`_route_label`   Route names are typed inconsistently — but `TVM` and `TVM 2` are two
                 different vans, not one route typed twice, and so are `CLT 1` and
                 `CLT 2`. Whitespace and case are folded; digits never are. Folding them
                 would put one van's boxes on another van's manifest.

`_days_in_lab`   `date_deadline` is empty on all 24,659 orders and `commitment_date` on
                 all 23,442, so nothing here can honestly say "overdue". Age is a fact;
                 lateness would be an invention. `date_finished` looks populated and is
                 not a completion date — 13,001 orders finish at exactly 09:00 — so it is
                 never used as one.
"""
from datetime import timedelta

from odoo import api, fields, models
from odoo.tools import SQL

# A sheet is read standing up. Past roughly this many rows it stops being a worklist and
# becomes a report nobody finishes, so every sheet states the true total beside the cap.
FLOOR_ROWS = 40


class ReportLabFloor(models.AbstractModel):
    _name = 'report.lab.floor'
    _description = 'Shop Floor Report Helpers'

    # ------------------------------------------------------------------ what is real
    @api.model
    def _shipped_orders(self):
        """Sales orders whose delivery is finished — every box gone, none outstanding.

        Two conditions, and the second is not decoration. Testing only "has a done
        outgoing picking" marks the whole order shipped as soon as ANY line ships, so on
        a two-appliance case where one item went last week and the other is still being
        made, the unmade one is declared delivered: it vanishes from the bench sheets and
        the doctor's sheet, and turns up on the close-out list telling the office to
        close an appliance nobody has started. 113 cases on this database are in exactly
        that position, and 104 of them have their own appliance sitting on a picking that
        has not gone out. (measured 2026-08-26)

        Read under sudo and returned as a subquery, deliberately. As a negated `any` leaf
        this is narrowed by the READER's record rules on sale.order — so a user who
        cannot see every order silently gets a different, larger "still in the lab" set
        than a manager looking at the same sheet. What is in the lab is a fact about the
        lab, not about who is asking.
        """
        return self.env['sale.order'].sudo()._search([
            ('picking_ids', 'any', [('picking_type_id.code', '=', 'outgoing'),
                                    ('state', '=', 'done')]),
            ('picking_ids', 'not any', [('picking_type_id.code', '=', 'outgoing'),
                                        ('state', 'not in', ('done', 'cancel'))]),
            # A case the doctor sent back is not a case the doctor has. Without this,
            # a fully returned order still satisfies "shipped and nothing pending", so
            # it stays off the floor sheets and sits on the close-out worklist waiting
            # to be finished against an appliance that is physically on the bench.
            ('picking_ids', 'not any', [('picking_type_id.code', '=', 'incoming'),
                                        ('state', '=', 'done')]),
        ])

    @api.model
    def _live_domain(self):
        """Manufacturing orders that are genuinely still in the lab."""
        return [('state', 'not in', ('done', 'cancel')),
                ('sale_id', 'not in', self._shipped_orders())]

    @api.model
    def _closeout_domain(self):
        """The other side of the same coin: work the doctor already has, still open here.

        Not a floor sheet — an office worklist. It is the largest single distortion in
        this database and the only way to remove it is to close the orders.
        """
        return [('state', 'not in', ('done', 'cancel')),
                ('sale_id', 'in', self._shipped_orders())]

    @api.model
    def _live_picking_domain(self):
        """Boxes that still have to go out."""
        return [('picking_type_id.code', '=', 'outgoing'),
                ('state', 'not in', ('done', 'cancel'))]

    # ------------------------------------------------------------------ labels
    @api.model
    def _patient_label(self, order, fallback=''):
        name = (order.patient or '').strip() if order else ''
        return name or fallback or ''

    @api.model
    def _open_steps_by_arch(self, productions):
        """The step each PIECE is waiting at: one row per case per arch.

        `_first_open_step` answers "where is this case", which is one place by
        definition. A case sold as Upper & Lower is two pieces travelling the
        benches separately, and the bench has to see both: the upper may be at
        Polishing while the lower is still at the wire. Same rule as the
        per-case helper - the FIRST unfinished step, never every step - applied
        once per arch. (client, 2026-09-12)

        Returns a list of rows shaped like the per-case helper's values, each
        carrying its own `arch` ('' on a single-arch case).
        """
        if not productions:
            return []
        Workorder = self.env['mrp.workorder']
        query = Workorder._search([('production_id', 'in', productions.ids),
                                   ('state', 'not in', ('done', 'cancel'))])
        Workorder.flush_model()
        # `taken` and `gone` are read here because this query already has the
        # rows. Asking the ORM the same two questions afterwards cost 65ms of a
        # 430ms board: `mrp.workorder`'s own order joins resource_calendar_leaves
        # and sorts, to answer two booleans about two thousand ids.
        # (measured 2026-09-12)
        #
        # Keep comments OUT of the SQL below. SQL() collapses the query onto one
        # line before sending it, so a `--` comment eats everything after it -
        # which is how the FROM clause disappeared the first time I wrote this.
        #
        # COALESCE so a single-arch case groups as one piece: NULL never equals
        # NULL in a DISTINCT ON, but '' does.
        self.env.cr.execute(SQL(
            """
            SELECT DISTINCT ON (wo.production_id, COALESCE(wo.arch, ''))
                   wo.id, wo.production_id, wo.workcenter_id, wo.name,
                   wo.sequence, wo.duration_expected, COALESCE(wo.arch, ''),
                   wo.accepted_at IS NOT NULL, wo.handed_over_at IS NOT NULL
              FROM mrp_workorder wo
             WHERE wo.id IN %s
             ORDER BY wo.production_id, COALESCE(wo.arch, ''), wo.sequence, wo.id
            """, query.subselect()))
        rows = self.env.cr.fetchall()
        names = {}
        station_ids = {row[2] for row in rows if row[2]}
        if station_ids:
            names = {w.id: w.display_name for w in self.env['mrp.workcenter']
                     .with_context(active_test=False).browse(list(station_ids))}
        return [{
            'id': row[0],
            'production_id': (row[1], ''),
            'workcenter_id': (row[2], names.get(row[2], '')) if row[2] else False,
            'name': row[3] or '',
            'sequence': row[4] or 0,
            'duration_expected': float(row[5] or 0.0),
            'arch': row[6] or '',
            'taken': row[7],
            'gone': row[8],
        } for row in rows]

    @api.model
    def _arch_labels(self):
        """U/L is a RELATED selection, so `_fields['ul'].selection` is a FUNCTION, not a
        list — calling dict() on it raises. fields_get resolves it the way the client
        does, and returns nothing at all when sale_custom is absent."""
        Production = self.env['mrp.production']
        if 'ul' not in Production._fields:
            return {}
        return dict(Production.fields_get(['ul'])['ul'].get('selection') or [])

    @api.model
    def _arch_label(self, production, labels=None):
        if 'ul' not in production._fields or not production.ul:
            return ''
        return (labels if labels is not None else self._arch_labels()).get(
            production.ul, '')

    @api.model
    def _step_note(self, station, step):
        """The operation name, but only when it says something the station does not.

        Most operations in this lab are named after the bench they run at, and the two
        strings differ only in spacing — "Wire Bending / Adams Clasp" against
        "Wire Bending/ Adams Clasp". Printed together they read as a duplication fault
        rather than as detail, so ALL whitespace is dropped for the comparison: the
        difference is a space before a slash, not a run of spaces, and collapsing runs
        alone leaves the duplicate on the page.
        """
        tidy = lambda text: ''.join((text or '').split()).casefold()
        return '' if tidy(station) == tidy(step) else (step or '')

    @api.model
    def _route_key(self, team):
        """The identity of a route, for grouping.

        ALL whitespace is folded, so 'TVM 2' and 'TVM2' — the same van, typed two ways —
        group together instead of printing as two blocks with two driver signatures.
        Digits are never touched: 'TVM' and 'TVM 2' are two different vans carrying two
        different sets of boxes, and folding them would put one van's work on the other's
        manifest. (measured 2026-08-26: TVM 197 boxes, TVM 2 169, TVM2 1)
        """
        return ''.join((team.name or '').split()).upper() if team else ''

    @api.model
    def _route_label(self, team):
        """What a route is called on paper — tidied, but recognisably itself."""
        return ' '.join((team.name or '').split()).upper() if team else ''

    @api.model
    def _days_in_lab(self, production):
        """Calendar days since the case was ordered.

        `mrp.production.date_start` equals the sales order date on 24,650 of 24,651
        orders here, so it is a true arrival date rather than a schedule.
        """
        start = production.date_start or production.create_date
        if not start:
            return 0
        return max(0, (fields.Datetime.now() - start).days)

    @api.model
    def _age_cutoff(self, days):
        return fields.Datetime.now() - timedelta(days=days or 0)

    # ------------------------------------------------------------------ furniture
    @api.model
    def _printed_stamp(self):
        """Who printed this, and when.

        A bench cannot tell today's sheet from Tuesday's on a noticeboard without it, and
        two sheets of the same queue printed an hour apart are otherwise identical.
        """
        # 90 of this lab's 111 internal users have no timezone set, and
        # context_timestamp falls back to UTC for them — so the sheet would be stamped
        # five and a half hours behind the clock on the wall, on a document whose whole
        # purpose is telling today's copy from yesterday's. (measured 2026-08-26)
        # The one lab timezone, kept at lab.station._lab_tz.
        now = self.env['lab.station']._lab_time(fields.Datetime.now())
        return {'at': now.strftime('%d/%m/%Y %H:%M'), 'by': self.env.user.name}

    @api.model
    def _first_open_step(self, productions):
        """The operation each case is actually waiting at, keyed by production id.

        A station's queue is NOT every unfinished work order sitting against it: an order
        routed Wire Bending → Acrylisation → Trimming → Polishing has all four unfinished
        from the moment it is created, so counting them all puts the same case in four
        queues at once and shows Acrylisation 23,269 jobs deep when nothing has reached
        it. The queue is the FIRST unfinished step, and only that. (measured 2026-08-26)
        """
        if not productions:
            return {}
        # NOT sudo: this feeds the station column of sheets a bench user can print, and
        # a superuser read here would hand them the operations of work centres their own
        # record rule exists to hide — and would poison the shared field cache with
        # rule-exempt values for the rest of the transaction. `_search` applies the
        # reader's own rules, and the SQL below only ever narrows what it returned.
        #
        # DISTINCT ON rather than reading every row and keeping the first in Python:
        # the lab's live set is ~2,000 cases but ~8,000 open operations, and this is
        # the single most-called helper on every board. Measured 258 ms -> 40 ms.
        # (client, 2026-09-10)
        Workorder = self.env['mrp.workorder']
        query = Workorder._search([('production_id', 'in', productions.ids),
                                   ('state', 'not in', ('done', 'cancel'))])
        Workorder.flush_model()
        self.env.cr.execute(SQL(
            """
            SELECT DISTINCT ON (wo.production_id)
                   wo.id, wo.production_id, wo.workcenter_id, wo.name,
                   wo.sequence, wo.duration_expected
              FROM mrp_workorder wo
             WHERE wo.id IN %s
             ORDER BY wo.production_id, wo.sequence, wo.id
            """, query.subselect()))
        rows = self.env.cr.fetchall()
        names = {}
        station_ids = {row[2] for row in rows if row[2]}
        if station_ids:
            # The reader's own rights here too, for the reason above: the station
            # names go on a sheet they can print.
            names = {w.id: w.display_name for w in self.env['mrp.workcenter']
                     .with_context(active_test=False).browse(list(station_ids))}
        # The same shape search_read returned, so every caller reads unchanged.
        return {row[1]: {
            'id': row[0],
            'production_id': (row[1], ''),
            'workcenter_id': (row[2], names.get(row[2], '')) if row[2] else False,
            'name': row[3] or '',
            'sequence': row[4] or 0,
            'duration_expected': float(row[5] or 0.0),
        } for row in rows}
