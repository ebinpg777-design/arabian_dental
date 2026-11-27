# -*- coding: utf-8 -*-
"""Confirm Orders: one screen to find an order by its number and confirm it.

The orders the field executives raise arrive as quotations, and one person at the
office confirms them (client, 2026-11-27: "Krishnasanth is doing the confirmation of
SOs created from field executives. Create a widget for searching and confirming
orders by typing the SO number ... very user friendly, because the user is not that
much expert in Odoo"). This is that screen's server half: it says, in plain words,
what an order is and whether it can be confirmed, and it confirms through the order's
own Confirm - every check the Confirm button makes (doctor waiting, company warning,
credit, amendment, verification when switched on) is made here too, and nothing is
done with more rights than the person has.
"""
import re

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import format_amount, format_date

WAITING = ('to_verify', 'on_hold')
RUSH = {'emergency': 'danger', 'urgent': 'warning'}


class LabOrderConfirmDesk(models.AbstractModel):
    _name = 'lab.order.confirm.desk'
    _description = 'Confirm Orders screen'

    # ------------------------------------------------------------------ access
    @api.model
    def _check_desk(self):
        user = self.env.user
        if not (user.has_group('lab_order_control.group_order_confirm_desk')
                or user.has_group('base.group_system')):
            raise AccessError(_("The Confirm Orders screen is not open to you."))

    # ------------------------------------------------------------------ finding
    @api.model
    def _normalise(self, text):
        """What was typed or scanned, as an order number: "81488", "s 81488" and
        "S81488" all mean S81488."""
        token = re.sub(r'\s+', '', text or '').upper()
        return 'S' + token if token.isdigit() else token

    @api.model
    def find(self, text):
        self._check_desk()
        raw = re.sub(r'\s+', '', text or '')
        token = self._normalise(text)
        if not token:
            return {'order': False, 'choices': []}
        Order = self.env['sale.order']
        exact = Order.search([('name', '=ilike', token)], limit=1)
        if not exact and raw.isdigit():
            # a trial or rework number typed without its letters (TR001, RE...)
            exact = Order.search([('name', '=ilike', '%' + raw)], limit=2)
            exact = exact if len(exact) == 1 else Order
        if exact:
            return {'order': self._card(exact), 'choices': []}
        found = Order.search([('name', 'ilike', raw or token)], order='id desc', limit=8)
        if len(found) == 1:
            return {'order': self._card(found), 'choices': []}
        return {'order': False, 'choices': [self._row(o) for o in found], 'query': token}

    @api.model
    def open_order(self, order_id):
        self._check_desk()
        order = self.env['sale.order'].browse(int(order_id)).exists()
        if not order:
            raise UserError(_("That order no longer exists."))
        return self._card(order)

    @api.model
    def waiting(self, limit=50):
        """The executives' orders still waiting to be confirmed, oldest first."""
        self._check_desk()
        domain = [('state', 'in', ('draft', 'sent')), ('verification_state', 'in', WAITING)]
        Order = self.env['sale.order']
        orders = Order.search(domain, order='date_order asc, id asc', limit=limit)
        return {'count': Order.search_count(domain), 'rows': [self._row(o) for o in orders]}

    # ------------------------------------------------------------------ confirming
    @api.model
    def confirm(self, order_id):
        """Confirm through the order's own Confirm. Never raises for a reason the
        person should read: the reason comes back as words for the screen."""
        self._check_desk()
        order = self.env['sale.order'].browse(int(order_id)).exists()
        if not order:
            return {'error': _("That order no longer exists."), 'card': False}
        if order.state not in ('draft', 'sent'):
            return {'error': self._not_confirmable(order), 'card': self._card(order)}
        try:
            with self.env.cr.savepoint():
                result = order.action_confirm()
        except (UserError, ValidationError, AccessError) as error:
            return {'error': str(error.args[0]) if error.args else str(error),
                    'card': self._card(order)}
        if isinstance(result, dict) and result.get('type'):
            # a question to answer first (the company's confirmation warning)
            return {'action': result, 'card': self._card(order)}
        return {'done': order.state == 'sale', 'card': self._card(order)}

    # ------------------------------------------------------------------ words
    @api.model
    def _clinic(self, order):
        partner = order.partner_id
        return partner.commercial_partner_id.display_name or partner.display_name or ''

    @api.model
    def _row(self, order):
        return {
            'id': order.id,
            'name': order.name,
            'clinic': self._clinic(order),
            'patient': (order.patient or '') if 'patient' in order._fields else '',
            'by': (order.entered_by_id or order.create_uid).name or '',
            'date': format_date(self.env, order.date_order) if order.date_order else '',
            'amount': format_amount(self.env, order.amount_total, order.currency_id),
            'state': order.state,
            'on_hold': order.verification_state == 'on_hold',
            'rush': order.priority if order.priority in RUSH else '',
        }

    @api.model
    def _not_confirmable(self, order):
        if order.state == 'sale':
            return _("%s is already confirmed.", order.name)
        if order.state == 'cancel':
            return _("%s is cancelled. It cannot be confirmed.", order.name)
        return _("%s cannot be confirmed now.", order.name)

    @api.model
    def _card(self, order):
        order.ensure_one()
        state_words = {
            'draft': _("Waiting for confirmation"), 'sent': _("Waiting for confirmation"),
            'sale': _("Confirmed"), 'cancel': _("Cancelled"),
        }
        lines = []
        for line in order.order_line.filtered(lambda l: not l.display_type):
            lines.append({
                'product': line.product_id.display_name or line.name or '',
                'qty': ('%g' % line.product_uom_qty),
                'jaw': dict(line._fields['ul'].selection).get(line.ul, '') if 'ul' in line._fields and line.ul else '',
                'teeth': (line.teeth or '') if 'teeth' in line._fields else '',
                'shade': line.color_scheme.display_name if 'color_scheme' in line._fields and line.color_scheme else '',
            })
        flags = []
        if order.priority in RUSH:
            flags.append({'tone': RUSH[order.priority],
                          'text': _("EMERGENCY - the patient is waiting") if order.priority == 'emergency'
                          else _("Urgent")})
        if order.verification_state == 'on_hold':
            flags.append({'tone': 'warning', 'text': _("Pending information from the doctor")})
        if 'lab_amending' in order._fields and order.lab_amending:
            flags.append({'tone': 'warning', 'text': _("Being amended")})
        if hasattr(order, '_doctor_tickets_in_the_way'):
            tickets = order._doctor_tickets_in_the_way()
            if tickets:
                flags.append({'tone': 'danger', 'text': _(
                    "Waiting for the doctor: %s. Close the ticket at the Doctor Call Desk first.",
                    ', '.join(tickets.mapped('name')))})
        if 'is_trial' in order._fields and order.is_trial:
            flags.append({'tone': 'info', 'text': _("Trial - not charged")})
        if 'is_rework' in order._fields and order.is_rework:
            flags.append({'tone': 'info', 'text': _("Rework")})
        if 'is_3d_model_print' in order._fields and order.is_3d_model_print:
            flags.append({'tone': 'info', 'text': _("Scan case - 3D Model Print")})
        if order.state in ('draft', 'sent'):
            credit = order._credit_limit_message()
            if credit:
                flags.append({'tone': 'warning', 'text': credit})
            if not lines:
                flags.append({'tone': 'danger', 'text': _("This order has no items.")})
        return {
            'id': order.id,
            'name': order.name,
            'state': order.state,
            'state_label': state_words.get(order.state, order.state),
            'can_confirm': order.state in ('draft', 'sent') and bool(lines),
            'why_not': '' if order.state in ('draft', 'sent') else self._not_confirmable(order),
            'clinic': self._clinic(order),
            'route': order.team_id.name or '',
            'patient': (order.patient or '') if 'patient' in order._fields else '',
            'by': (order.entered_by_id or order.create_uid).name or '',
            'date': format_date(self.env, order.date_order) if order.date_order else '',
            'amount': format_amount(self.env, order.amount_total, order.currency_id),
            'lines': lines,
            'flags': flags,
        }
