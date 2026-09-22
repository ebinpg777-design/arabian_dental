# -*- coding: utf-8 -*-
"""Everything known about one case, assembled in a single call (R18).

The question this answers is always asked under pressure: a doctor is on the phone
wanting to know where their patient's appliance is. Answering it today means the sale
order, then its manufacturing orders, then each work order, then the delivery, then the
invoice — six screens while somebody waits.

So this is one round trip. The screen makes no further requests: everything below is
gathered here and sent at once, because a second query per section is a second thing to
wait for on a bad connection.
"""
from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

# Child records are read as superuser on purpose. A CEO or office manager is entitled to
# the ANSWER — where the work is — without also being granted Manufacturing / Inventory
# rights they have no other use for. Nothing is written here, and the search itself is
# scoped by what the user could already find.
MAX_MATCHES = 12
# How many of the clinic's receipts to show: enough to recognise "we paid you
# last Tuesday", not a statement of account.
PAYMENT_ROWS = 8
# The two pieces of a case sold as Upper & Lower, said the short way the
# lab says them. (client, 2026-09-12)
ARCH_SHORT = {'upper': 'U', 'lower': 'L'}


class LabTrack(models.AbstractModel):
    _name = 'lab.track'
    _description = 'Track a Work'

    # ------------------------------------------------------------------ search
    @api.model
    def search_work(self, ref):
        """Resolve whatever was typed into one order, or offer the candidates."""
        return self._search_work(ref, self.env['sale.order'], self.get_work)

    @api.model
    def _search_work(self, ref, Order, open_order):
        ref = (ref or '').strip()
        if not ref:
            return {'found': False, 'empty': True}

        order = self._resolve(ref, Order)
        if not order:
            matches = self._candidates(ref, Order)
            if len(matches) == 1:
                order = matches[0]
            elif matches:
                return {
                    'found': False,
                    'matches': [self._row(o) for o in matches],
                }
            else:
                return {'found': False, 'ref': ref}
        return open_order(order.id)

    @api.model
    def _resolve(self, ref, Order=None):
        """Exact hit on any reference a person might have in front of them.

        A manager reads out whatever is on the paper in their hand — the order number,
        the delivery note, the invoice, the rework slip. All of them lead to the same
        case, so all of them are accepted.
        """
        Order = self.env['sale.order'] if Order is None else Order
        order = Order.search([('name', '=', ref)], limit=1)
        if order:
            return order

        delivery = self.env['lab.delivery'].sudo().search([('name', '=', ref)], limit=1)
        if delivery:
            return delivery.sale_order_id

        invoice = self.env['account.move'].sudo().search(
            [('name', '=', ref), ('move_type', 'in', ('out_invoice', 'out_refund'))],
            limit=1)
        if invoice and invoice.line_ids.sale_line_ids:
            return invoice.line_ids.sale_line_ids[0].order_id
        # 176 of this database's 23,276 customer invoices carry no sale-line link at
        # all, and the link above is the only path the invoice number had. The order
        # still knows its own invoices, so ask from that end too rather than telling
        # somebody holding the invoice that it does not exist. (client, 2026-08-29)
        if invoice:
            order = Order.search([('invoice_ids', 'in', invoice.ids)], limit=1)
            if order:
                return order

        # The consignment number off the courier's sticker: the one reference the
        # person chasing a parcel actually has in their hand.
        Delivery = self.env['lab.delivery'].sudo()
        parcel = Delivery.search([('courier_awb', '=', ref)], limit=1)
        if parcel:
            return parcel.sale_order_id

        # The warehouse delivery note.
        picking = self.env['stock.picking'].sudo().search([('name', '=', ref)], limit=1)
        if picking and picking.sale_id:
            return picking.sale_id
        return Order.browse()

    # What the chips say. The raw keys leaked onto the screen as "in_lab", which
    # reads like a fault code to the person on the phone. (client, 2026-08-24)
    STAGE_LABELS = {
        'registered': 'Registered',
        'in_lab': 'In the lab',
        'ready': 'Ready',
        'sent': 'Out for delivery',
        'delivered': 'Delivered',
        'cancel': 'Cancelled',
    }

    @api.model
    def _row(self, o):
        """One search-result card: enough to recognise the case at a glance."""
        stage = o.portal_stage if 'portal_stage' in o._fields else ''
        return {
            'id': o.id, 'name': o.name,
            'patient': o.patient or '',
            'doctor': o.partner_id.display_name,
            'product': o.product_names or '',
            # `date_order` is a DATETIME: formatted raw, an order taken at
            # 02:00 local reads as the previous day. (client, 2026-09-12)
            'date': self._stamp(o.date_order, '%d %b %Y'),
            'stage': stage,
            'stage_label': self.STAGE_LABELS.get(stage, stage or ''),
        }

    @api.model
    def _candidates(self, ref, Order=None):
        """Partial reference, a patient name or the doctor — offer what it could be.

        The doctor matters as much as the patient: the person on the phone IS the
        doctor's receptionist, and "anything for Dr Susmitha" is how the question
        actually arrives. (client, 2026-08-24)
        """
        # The paper in somebody's hand is as often an invoice or a courier sticker as
        # it is an order: searching only the order number meant typing an invoice
        # number returned "nothing matches", which reads as "this case does not
        # exist". (client, 2026-08-29)
        Order = self.env['sale.order'] if Order is None else Order
        return Order.search(
            ['|', '|', '|', '|', '|',
             ('name', 'ilike', ref),
             ('patient', 'ilike', ref),
             ('partner_id', 'ilike', ref),
             ('invoice_ids.name', 'ilike', ref),
             ('delivery_ids.name', 'ilike', ref),
             ('delivery_ids.courier_awb', 'ilike', ref)],
            order='date_order desc', limit=MAX_MATCHES)

    @api.model
    def live_search(self, term):
        """Search-as-you-type: cards for whatever matches, newest first.

        Unlike search_work this never resolves to one record on its own - while
        somebody is still typing, jumping to a case they did not ask for is worse
        than showing the list narrowing down.
        """
        term = (term or '').strip()
        if len(term) < 2:
            return []
        return [self._row(o) for o in self._candidates(term)]

    # ------------------------------------------------------------------ payload
    @api.model
    def get_work(self, order_id):
        order = self.env['sale.order'].browse(order_id).exists()
        if not order:
            return {'found': False}
        # The order itself is still the user's to see or not: this method takes a bare id
        # from the browser, so without this a field executive could walk the id range and
        # read every clinic's patients and prices. Only the CHILDREN below are elevated.
        order.check_access('read')
        return self._work(order)

    # ------------------------------------------------------------------ floor
    # Track Order on the Station Board (client, 2026-09-17). The bench asks "where
    # is the rest of this case" with a job card in hand, and 47 of the 49 people
    # posted to a bench hold no Sales role; where record rules are on, get_work
    # refuses them. So the floor gets its own door: manufacturing users only, never a
    # link out to a record, and the money - price, invoices, the clinic's receipts -
    # left off for anyone without a Sales or Invoicing role. Nothing here writes.
    @api.model
    def _floor_orders(self):
        if not (self.env.su or self.env.user.has_group('mrp.group_mrp_user')):
            raise AccessError(_("Track Order on the Station Board is for manufacturing users."))
        return self.env['sale.order'].sudo()

    @api.model
    def floor_search_work(self, ref):
        return self._search_work(ref, self._floor_orders(), self.floor_get_work)

    @api.model
    def floor_live_search(self, term):
        Order = self._floor_orders()
        term = (term or '').strip()
        if len(term) < 2:
            return []
        return [self._row(o) for o in self._candidates(term, Order)]

    @api.model
    def floor_get_work(self, order_id):
        order = self._floor_orders().browse(order_id).exists()
        if not order:
            return {'found': False}
        user = self.env.user
        money = self.env.su or user.has_group('sales_team.group_sale_salesman') \
            or user.has_group('account.group_account_invoice')
        return self._work(order, money=bool(money))

    @api.model
    def _work(self, order, money=True):
        o = order.sudo()
        currency = o.currency_id

        return {
            'found': True,
            # False on the floor tracker for somebody with no Sales or Invoicing role:
            # prices, invoices and the clinic's receipts are not a bench's business.
            'money': money,
            # A tracking screen is a photograph, and the lab moves work while it is
            # open. Refresh takes another one; this says when this one was taken.
            # (client, 2026-09-09)
            'read_at': fields.Datetime.context_timestamp(
                self._tz_reader(), fields.Datetime.now()).strftime('%H:%M'),
            'order': {
                'id': o.id,
                'name': o.name,
                'patient': o.patient or '',
                'doctor': o.partner_id.display_name,
                'doctor_id': o.partner_id.id,
                'executive': (o.visit_id.user_id.name if o.visit_id
                              else (o.user_id.name or '')),
                'from_field': bool(o.visit_id),
                'date': self._stamp(o.date_order, '%d %b %Y %H:%M'),
                'priority': o.priority,
                'is_rework': o.is_rework,
                'state': o.state,
                'state_label': dict(o._fields['state'].selection).get(o.state, ''),
                'verification': o.verification_state,
                'verification_label': dict(
                    o._fields['verification_state'].selection).get(
                        o.verification_state, ''),
                'amount': o.amount_total if money else 0,
                'currency': currency.symbol or '',
                'stage': o.portal_stage if 'portal_stage' in o._fields else '',
                'stage_label': self.STAGE_LABELS.get(
                    o.portal_stage if 'portal_stage' in o._fields else '', ''),
                'operation': o.portal_operation if 'portal_operation' in o._fields else '',
                'progress': o.portal_progress if 'portal_progress' in o._fields else 0,
            },
            'timeline': self._timeline(o),
            'productions': self._productions(o),
            'deliveries': self._deliveries(o),
            'pickings': self._pickings(o),
            'invoices': self._invoices(o) if money else [],
            'payments': self._payments(o) if money else [],
            'reworks': self._reworks(o),
            'calls': self._calls(o),
        }

    # ------------------------------------------------------------------ sections
    @api.model
    def _tz_reader(self):
        """`self`, with a timezone guaranteed.

        129 of this database's 156 active users have no timezone set, and
        `context_timestamp` silently falls back to UTC for exactly those
        people - so converting through `self` alone would fix this screen for
        the 27 who have one and leave the rest reading the same wrong hour.
        Both company partners are blank too, so the lab's own zone is the last
        resort. The floor sheet already does this. (measured 2026-09-12)
        """
        if self.env.context.get('tz') or self.env.user.tz:
            return self
        return self.with_context(
            tz=self.env.company.partner_id.tz or 'Asia/Kolkata')

    @api.model
    def _stamp(self, value, fmt='%d %b %H:%M'):
        """A stamp in the reader's own clock.

        Odoo keeps datetimes in UTC, so formatting one straight off the record
        puts this lab 5.5 hours behind itself: a bench that finished at 14:58
        was shown as having finished at 09:28, which reads as the morning's
        work. (client, 2026-09-12)

        Dates are left exactly as they are. A date carries no time to convert,
        and shifting one would move an invoice dated the 1st back to the 31st -
        `datetime` is a subclass of `date`, so the test must be this way round.
        """
        if not value:
            return ''
        if isinstance(value, datetime):
            value = fields.Datetime.context_timestamp(self._tz_reader(), value)
        return value.strftime(fmt)

    @api.model
    def _hours_since(self, value):
        """Whole hours from `value` until now, or 0 when there is no stamp."""
        if not value:
            return 0
        delta = fields.Datetime.now() - value
        return max(0, int(delta.total_seconds() // 3600))

    @api.model
    def _timeline(self, o):
        """The case's own history, in the order it happened."""
        events = []
        events.append({'icon': 'fa-pencil-square-o', 'label': _('Registered'),
                       'when': self._stamp(o.create_date), 'done': True,
                       'detail': o.create_uid.name or ''})
        if o.verification_state in ('to_verify', 'on_hold', 'verified', 'rejected'):
            events.append({'icon': 'fa-paper-plane', 'label': _('Submitted for checking'),
                           'when': '', 'done': True, 'detail': ''})
        if o.verification_state == 'on_hold':
            events.append({'icon': 'fa-pause-circle', 'label': _('Pending information'),
                           'when': self._stamp(o.hold_date), 'done': False,
                           'detail': dict(o._fields['hold_reason'].selection).get(
                               o.hold_reason, '')})
        if o.verification_state == 'verified':
            events.append({'icon': 'fa-check-circle', 'label': _('Verified'),
                           'when': self._stamp(o.verification_date), 'done': True,
                           'detail': o.verified_by_id.name or ''})
        if o.state in ('sale', 'done'):
            events.append({'icon': 'fa-handshake-o', 'label': _('Confirmed'),
                           'when': '', 'done': True, 'detail': ''})
        productions = o.mrp_production_ids.filtered(lambda m: m.state != 'cancel')
        if productions:
            started = min((m.date_start for m in productions if m.date_start),
                          default=False)
            events.append({'icon': 'fa-cogs', 'label': _('In production'),
                           'when': self._stamp(started), 'detail':
                               _('%s job(s)', len(productions)),
                           'done': all(m.state == 'done' for m in productions)})
        for delivery in o.delivery_ids.filtered(
                lambda d: d.state != 'cancel' and d.direction == 'out'):
            if delivery.out_datetime:
                events.append({'icon': 'fa-truck', 'label': _('Out for delivery'),
                               'when': self._stamp(delivery.out_datetime), 'done': True,
                               'detail': delivery.executive_id.name or ''})
            if delivery.delivered_datetime:
                events.append({'icon': 'fa-flag-checkered', 'label': _('Delivered'),
                               'when': self._stamp(delivery.delivered_datetime),
                               'done': True,
                               'detail': delivery.near_place_name or _('to the clinic')})
        for invoice in o.invoice_ids.filtered(lambda i: i.state == 'posted'):
            events.append({'icon': 'fa-file-text-o', 'label': _('Invoiced'),
                           'when': self._stamp(invoice.invoice_date, '%d %b %Y'),
                           'done': True, 'detail': invoice.name})
        return events

    @api.model
    def _productions(self, o):
        rows = []
        # Built once and translated, not rebuilt from the raw selection per step.
        wo_labels = dict(self.env['mrp.workorder']._fields['state']
                         ._description_selection(self.env))
        Production = self.env['mrp.production']
        ul_labels = (dict(Production._fields['ul']._description_selection(self.env))
                     if 'ul' in Production._fields else {})
        for mo in o.mrp_production_ids.filtered(lambda m: m.state != 'cancel'):
            workorders = []
            for wo in mo.workorder_ids.sorted(lambda w: (w.sequence, w.id)):
                workorders.append({
                    'name': wo.name,
                    'station': wo.workcenter_id.display_name or '',
                    'state': wo.state,
                    'state_label': wo_labels.get(wo.state, ''),
                    'started': self._stamp(wo.date_start),
                    'finished': self._stamp(wo.date_finished),
                    # How long the step took, and how long it has been sitting
                    # there if it has not finished. A chip that says only
                    # "Blocked" cannot tell a doctor when to expect the case.
                    # (client, 2026-09-09)
                    'minutes': int(round(wo.duration or 0)),
                    'waiting_h': self._hours_since(
                        wo.date_start if wo.state == 'progress' else wo.create_date)
                    if wo.state not in ('done', 'cancel') else 0,
                    # Who made it, and - at a bench that records the two separately
                    # (lab_workcenter_scan's is_finisher) - who finished it. Both
                    # guarded: this module does not depend on that one.
                    # (client, 2026-09-09)
                    # WHICH PIECE, on a case sold as Upper & Lower. Such a case
                    # is made as two chains inside one job, so this rail shows
                    # the same bench names twice and nothing said which run was
                    # which. Guarded: the arch is lab_workcenter_scan's, and
                    # this module does not depend on it. (client, 2026-09-12)
                    'arch': (ARCH_SHORT.get(wo.arch)
                             if 'arch' in wo._fields else '') or '',
                    'person': (wo.bench_user_id.name
                               if 'bench_user_id' in wo._fields else '') or '',
                    'finisher': (wo.finisher_user_id.name
                                 if 'finisher_user_id' in wo._fields else '') or '',
                })
            done = len([w for w in workorders if w['state'] == 'done'])
            rows.append({
                'id': mo.id,
                'name': mo.name,
                'product': mo.product_id.display_name or '',
                'qty': mo.product_qty,
                'state': mo.state,
                'state_label': dict(mo._fields['state'].selection).get(mo.state, ''),
                # U, L or UL - what was ordered. The office quotes this back to
                # the doctor, and on a UL case it explains why the rail below
                # runs through every bench twice. (client, 2026-09-12)
                'ul': (ul_labels.get(mo.ul, '') if 'ul' in mo._fields else ''),
                'workorders': workorders,
                'done': done,
                'total': len(workorders),
                'progress': int(done / len(workorders) * 100) if workorders else (
                    100 if mo.state == 'done' else 0),
            })
        return rows

    @api.model
    def _deliveries(self, o):
        return [{
            'id': d.id,
            'name': d.name,
            'state': d.state,
            'state_label': dict(d._fields['state'].selection).get(d.state, ''),
            'executive': d.executive_id.name or '',
            'scheduled': self._stamp(d.scheduled_date),
            'delivered': self._stamp(d.delivered_datetime),
            'outcome': dict(d._fields['delivery_outcome'].selection).get(
                d.delivery_outcome, ''),
            'near_place': d.near_place_name or '',
            'received_by': d.received_by or '',
            'delayed': d.is_delayed,
            'delay_hours': round(d.delay_hours or 0.0, 1),
            'emergency': d.is_emergency,
            # A couriered parcel's answer is the consignment, not the executive.
            'mode': d.delivery_mode,
            'courier': d.courier_id.name or '',
            'awb': d.courier_awb or '',
            'tracking_url': d.courier_tracking_url or '',
            'courier_status': dict(d._fields['courier_status'].selection).get(
                d.courier_status, ''),
            'courier_last_at': self._stamp(d.courier_last_event_at),
            'courier_expected': self._stamp(d.courier_expected_date, '%d %b %Y'),
            'courier_stale': d.courier_is_stale,
        } for d in o.delivery_ids.filtered(lambda d: d.direction == 'out')]

    @api.model
    def _pickings(self, o):
        """The warehouse's own delivery note, which is a different piece of paper from
        the field delivery above.

        `lab.delivery` is the executive's round; `stock.picking` is what the store
        packed and when. 27,162 of this database's pickings carry a sale order, so the
        question "has it actually left the building?" has an answer here that the field
        record cannot give — and often the picking is done while no field delivery was
        ever raised. (client, 2026-08-29)
        """
        pickings = o.picking_ids.filtered(
            lambda p: p.picking_type_id.code == 'outgoing') or o.picking_ids
        return [{
            'id': p.id,
            'name': p.name,
            'state': p.state,
            'state_label': dict(p._fields['state'].selection).get(p.state, ''),
            'scheduled': self._stamp(p.scheduled_date),
            'done_at': self._stamp(p.date_done),
            'origin': p.origin or '',
            'backorder': p.backorder_id.name or '',
            'weight': round(p.shipping_weight or 0.0, 2),
            'lines': [{
                'product': m.product_id.display_name,
                'quantity': m.quantity,
                'uom': m.product_uom.name or '',
            } for m in p.move_ids[:8]],
        } for p in pickings]

    @api.model
    def _open_by_move(self, o):
        """What each invoice still owes, from the Collections countback.

        `amount_residual` is dead on this database — 4 reconciled lines out of
        1,88,462 — so an invoice reads "owed" for its whole face value forever, which
        is exactly the number a doctor rings up to argue about. Collections already
        works the truth out by paying receipts against receivable debits oldest-first
        per clinic; this borrows that answer rather than forming a second opinion.
        Returns {} when the Collections module is not installed, and the screen then
        says nothing rather than something wrong. (client, 2026-08-29)
        """
        if not self.env.registry.get('lab.collection.performance'):
            return {}
        partner = o.partner_id.commercial_partner_id or o.partner_id
        if not partner:
            return {}
        try:
            items = self.env['lab.collection.performance'].sudo()._populate_open_items(
                o.company_id or self.env.company, partner_ids=[partner.id])
            return {item.move_id.id: item.open_amount for item in items}
        except Exception:                       # noqa: BLE001 - never break the screen
            return {}

    @api.model
    def _invoices(self, o):
        open_by_move = self._open_by_move(o)
        rows = []
        for i in o.invoice_ids:
            open_amount = open_by_move.get(i.id)
            rows.append({
                'id': i.id,
                'name': i.name,
                'date': self._stamp(i.invoice_date, '%d %b %Y'),
                'due': self._stamp(i.invoice_date_due, '%d %b %Y'),
                'amount': i.amount_total,
                'residual': i.amount_residual,
                # The honest figure, and whether there IS one: a paid invoice simply
                # drops out of the countback, so 0.0 and "unknown" must not look alike.
                'open_amount': open_amount or 0.0,
                'open_known': open_amount is not None,
                'state': i.state,
                'payment_state': i.payment_state or '',
                'currency': i.currency_id.symbol or '',
            })
        return rows

    @api.model
    def _payments(self, o):
        """What the clinic has actually paid, newest first.

        Deliberately the CLINIC's receipts and not "this invoice's payments": with no
        reconciliation on this database nothing ties a receipt to an invoice, and a
        list claiming otherwise would be invented. Receipts here are journal entries
        rather than account.payment rows (2 of those exist in total), so this reads
        the receivable credits themselves. The screen labels them as the clinic's.
        (client, 2026-08-29)
        """
        partner = o.partner_id.commercial_partner_id or o.partner_id
        if not partner:
            return []
        lines = self.env['account.move.line'].sudo().search(
            [('partner_id', '=', partner.id),
             ('account_id.account_type', '=', 'asset_receivable'),
             ('parent_state', '=', 'posted'),
             ('credit', '>', 0)],
            order='date desc, id desc', limit=PAYMENT_ROWS)
        return [{
            'id': line.id,
            'move_id': line.move_id.id,
            'name': line.move_id.name or '',
            'date': self._stamp(line.date, '%d %b %Y'),
            'journal': line.journal_id.name or '',
            'amount': line.credit,
            'currency': (line.company_currency_id.symbol
                         or line.currency_id.symbol or ''),
            'label': line.name or '',
            # A receipt dated after this order was raised is the one somebody on the
            # phone is usually asking about.
            'after_order': bool(o.date_order and line.date
                                and line.date >= o.date_order.date()),
        } for line in lines]

    @api.model
    def _reworks(self, o):
        """Both directions of the rework link, which lives on the order itself now.

        `lab.rework` was a model of its own until lab_rework 19.0.2.0.0; a rework is
        simply a sale order with `is_rework` set and `rework_origin_id` pointing at the
        job it remakes.
        """
        Order = self.env['sale.order'].sudo()
        related = Order.search(['|', ('rework_origin_id', '=', o.id), ('id', '=', o.rework_origin_id.id)])
        states = dict(Order._fields['state'].selection)
        return [{
            'id': r.id,
            'name': r.name,
            'reason': r.rework_reason_id.name or r.rework_note or '',
            'state': r.state,
            'state_label': states.get(r.state, ''),
            'is_origin': r.rework_origin_id.id == o.id,
            'other_order': (o.name if r.rework_origin_id.id == o.id else r.name) or '',
        } for r in related]

    @api.model
    def _calls(self, o):
        logs = self.env['lab.doctor.call.log'].sudo().search(
            [('order_id', '=', o.id)], order='call_datetime desc', limit=10)
        return [{
            'when': self._stamp(l.call_datetime),
            'response': dict(l._fields['response'].selection).get(l.response, ''),
            'answered': l.response == 'answered',
            'by': l.called_by_id.name or '',
            'remarks': l.remarks or '',
        } for l in logs]

    # ------------------------------------------------------------------ the floor
    # How many stations the landing page names, and how many jobs under each. A
    # floor view is a glance, not a queue: the counts tell the truth about the
    # size, the rows show the ones that matter now. (client, 2026-09-09)
    FLOOR_STATIONS = 12
    FLOOR_ROWS = 6
    # The browser may ask for more rows of a station, never for the whole floor.
    FLOOR_ROWS_MAX = 50

    @api.model
    def _live_production_ids(self):
        """The jobs genuinely still in the lab, or None where nobody can tell.

        Most "open" manufacturing orders on this database sit on a sales order
        whose delivery is already signed for - the appliance has been on the
        doctor's shelf for weeks and the job was never closed. report.lab.floor
        owns that carve-out; every floor figure here stands on the same set, or
        "standing longest" is a list of things that left in April.
        (client, 2026-09-09)
        """
        if 'report.lab.floor' not in self.env.registry:
            return None
        Floor = self.env['report.lab.floor']
        return self.env['mrp.production'].sudo().search(Floor._live_domain()).ids

    @api.model
    def _here_sql(self, has_scan):
        """The CTE every floor figure is built on: each live case, once, at the
        first step of it that is not finished. Returns (sql, params).

        The trap this exists to avoid: a case has a step at every station it
        will ever pass through, all of them open until it gets there. Counting
        open work orders per station therefore reported the whole backlog at
        every bench - 4,669 at Acrylisation, the same 4,669 at Trimming, the
        same names in the same order - which is true of the rows and false
        about the floor.
        """
        # Raw SQL reads the table, not the ORM's cache: write down anything
        # pending first, or a job created a moment ago is not on the floor.
        self.env['mrp.workorder'].flush_model()
        self.env['mrp.production'].flush_model()
        live = self._live_production_ids()
        accepted = 'w.accepted_at' if has_scan else 'NULL::timestamp'
        tech = 'w.bench_user_id' if has_scan else 'NULL::integer'
        carve = "AND p.id = ANY(%(live)s)" if live is not None else ""
        sql = """
            here AS (
                SELECT DISTINCT ON (w.production_id)
                       w.id, w.production_id, w.workcenter_id, w.name AS operation,
                       w.create_date, %s AS accepted_at, %s AS bench_user_id
                  FROM mrp_workorder w
                  JOIN mrp_production p ON p.id = w.production_id
                 WHERE w.state NOT IN ('done', 'cancel')
                   AND p.state NOT IN ('done', 'cancel', 'draft')
                   AND w.workcenter_id IS NOT NULL
                   %s
                 ORDER BY w.production_id, w.sequence, w.id
            )""" % (accepted, tech, carve)
        return sql, {'live': list(live) if live is not None else []}

    @api.model
    def floor_now(self):
        """The lab as it stands: where every live case is RIGHT NOW.

        One query with a window function rather than a search per station: the
        lab has tens of thousands of open steps and this is a landing page.
        """
        empty = {'stations': [], 'oldest': [], 'started_today': 0,
                 'finished_today': 0, 'waiting_today': 0, 'total_open': 0,
                 'as_of': self._clock_now(), 'has_floor': False}
        if 'mrp.workorder' not in self.env.registry:
            return empty
        Workorder = self.env['mrp.workorder']
        has_scan = 'accepted_at' in Workorder._fields
        here, params = self._here_sql(has_scan)
        self.env.cr.execute("""
            WITH %s
            SELECT workcenter_id,
                   count(*) FILTER (WHERE accepted_at IS NOT NULL) AS working,
                   count(*) FILTER (WHERE accepted_at IS NULL) AS waiting,
                   min(create_date) AS oldest
              FROM here
             GROUP BY workcenter_id
             ORDER BY count(*) DESC
        """ % here, params)
        groups = self.env.cr.fetchall()
        if not groups:
            return empty
        names = {w.id: w.display_name for w in self.env['mrp.workcenter'].sudo().browse(
            [g[0] for g in groups])}
        stations = [{
            'id': wc_id,
            'name': names.get(wc_id, ''),
            'working': working,
            'waiting': waiting,
            'total': working + waiting,
            'oldest_h': self._hours_since(oldest),
        } for wc_id, working, waiting, oldest in groups[:self.FLOOR_STATIONS]]
        total_open = sum(g[1] + g[2] for g in groups)
        for station in stations:
            station['share'] = round(station['total'] * 100.0 / total_open) \
                if total_open else 0
        start, end = self._today_window()
        started = finished = waiting_today = 0
        if has_scan:
            started = Workorder.sudo().search_count(
                [('accepted_at', '>=', start), ('accepted_at', '<', end)])
            waiting_today = Workorder.sudo().search_count(
                [('state', 'not in', ('done', 'cancel')), ('accepted_at', '=', False),
                 ('create_date', '>=', start), ('create_date', '<', end)])
        finished_field = 'handed_over_at' if 'handed_over_at' in Workorder._fields \
            else 'date_finished'
        finished = Workorder.sudo().search_count(
            [(finished_field, '>=', start), (finished_field, '<', end)])
        return {
            'stations': stations,
            'oldest': self._floor_oldest(has_scan),
            'started_today': started,
            'finished_today': finished,
            'waiting_today': waiting_today,
            'total_open': total_open,
            'as_of': self._clock_now(),
            'has_floor': True,
        }

    @api.model
    def station_jobs(self, workcenter_id, limit=None):
        """The cases standing at one station, in progress first.

        Fetched when a station is opened rather than with the page: twelve
        stations of six rows is a payload nobody reads, and the number that
        matters - how many are there - is already on the row. The station is
        applied AFTER the case's current step is found, never before: filtering
        the steps to one bench first made every later step of a case look like
        the case standing there. (2026-09-09)
        """
        if 'mrp.workorder' not in self.env.registry:
            return []
        try:
            limit = int(limit or self.FLOOR_ROWS)
        except (TypeError, ValueError):
            limit = self.FLOOR_ROWS
        limit = max(1, min(limit, self.FLOOR_ROWS_MAX))
        Workorder = self.env['mrp.workorder'].sudo()
        has_scan = 'accepted_at' in Workorder._fields
        here, params = self._here_sql(has_scan)
        params['wc'] = int(workcenter_id)
        # No LIMIT here: the rows the reader may not see are dropped afterwards, and a
        # limit taken first would leave them with fewer rows than the station holds.
        self.env.cr.execute("""
            WITH %s
            SELECT id FROM here
             WHERE workcenter_id = %%(wc)s
             ORDER BY accepted_at DESC NULLS LAST, create_date, id
        """ % here, params)
        rows = self._readable_jobs([row[0] for row in self.env.cr.fetchall()], limit)
        return [self._floor_row(w, waiting=not (has_scan and w.accepted_at))
                for w in rows]

    @api.model
    def _floor_oldest(self, has_scan, limit=8):
        """The cases that have stood still longest, wherever they are.

        The one list on this page that says what to DO: a floor view that only
        reports where things are lets the oldest case go on being the oldest.
        """
        if not has_scan:
            return []
        here, params = self._here_sql(has_scan)
        self.env.cr.execute("""
            WITH %s
            SELECT id FROM here
             WHERE accepted_at IS NULL
             ORDER BY create_date, id
        """ % here, params)
        rows = self._readable_jobs([row[0] for row in self.env.cr.fetchall()],
                                   min(limit, self.FLOOR_ROWS_MAX))
        return [dict(self._floor_row(w, waiting=True),
                     station=w.workcenter_id.display_name or '') for w in rows]

    @api.model
    def _readable_jobs(self, workorder_ids, limit):
        """The first `limit` of these steps whose order the reader may open.

        The floor is found as superuser - where the work stands is not a question of
        Manufacturing rights - but each row names the patient, the doctor and the
        order, which get_work already refuses for an order the user cannot read. Left
        unchecked, a field executive whose rules stop at their own route was handed
        every live case in the lab from this page. A step with no order behind it
        needs read access to manufacturing orders instead.

        Checked a slice at a time: someone who may read everything costs one slice,
        and nobody pays to check 4,000 steps to show six.
        """
        Workorder = self.env['mrp.workorder'].sudo()
        if self.env.su:
            return Workorder.browse(workorder_ids[:limit])
        Order = self.env['sale.order']
        bare_ok = self.env['mrp.production'].has_access('read')
        kept = []
        for start in range(0, len(workorder_ids), 200):
            chunk = Workorder.browse(workorder_ids[start:start + 200])
            orders = chunk.production_id.sale_id
            readable = set(Order.browse(orders.ids)._filtered_access('read').ids)
            for wo in chunk:
                order = wo.production_id.sale_id
                if (order.id in readable) if order else bare_ok:
                    kept.append(wo.id)
                    if len(kept) >= limit:
                        return Workorder.browse(kept)
        return Workorder.browse(kept)

    @api.model
    def _clock_now(self):
        return fields.Datetime.context_timestamp(
            self._tz_reader(), fields.Datetime.now()).strftime('%H:%M')

    @api.model
    def _today_window(self):
        """The reader's own day, as the UTC pair a datetime column is stored in.

        Through _tz_reader, not `user.tz or 'UTC'`: most users have no tz, and a UTC
        day started the lab's "today" at 05:30 - the night shift's work was
        yesterday's.
        """
        reader = self._tz_reader()
        start = reader.env.tz.localize(datetime.combine(
            fields.Date.context_today(reader), time.min))
        return (start.astimezone(pytz.utc).replace(tzinfo=None),
                (start + timedelta(days=1)).astimezone(pytz.utc).replace(tzinfo=None))

    @api.model
    def _floor_row(self, wo, waiting):
        """One job on a bench, in the words the floor uses to recognise it."""
        production = wo.production_id
        order = production.sale_id if production else self.env['sale.order']
        since = wo.create_date if waiting else (wo.accepted_at
                                                if 'accepted_at' in wo._fields
                                                else wo.date_start)
        return {
            'id': wo.id,
            'order_id': order.id or False,
            'production': production.name if production else '',
            'order': order.name or '',
            'patient': order.patient or '',
            'doctor': order.partner_id.display_name if order else '',
            'operation': wo.name or '',
            'technician': (wo.bench_user_id.name
                           if 'bench_user_id' in wo._fields else '') or '',
            'since': self._stamp(since, '%H:%M'),
            'hours': self._hours_since(since),
            # Whether the bench has taken it - not whether it has a name on it.
            # An accepted job with nobody named yet read as "waiting".
            'waiting': bool(waiting),
            'urgent': order.priority in ('urgent', 'emergency') if order else False,
        }

    @api.model
    def recent_works(self, limit=8):
        """Something useful on an empty screen: what the lab is working on now.

        A search box with nothing under it teaches people the screen is only for when
        you already know the number.
        """
        orders = self.env['sale.order'].search(
            [('state', 'in', ('sale', 'done'))], order='date_order desc', limit=limit)
        return [self._row(o) for o in orders]
