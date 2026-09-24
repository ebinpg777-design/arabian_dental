# -*- coding: utf-8 -*-
"""Dynamic Dashboards - one recorded value of a number card.

A number is only a point in time. Recording it once a day turns a card into a
history: a sparkline that says how the number got here, an audit of what the
board showed last month, and the rising edge a threshold notification needs.
"""
from odoo import api, fields, models

from .dashboard_item import NUMBER_KINDS


class DashboardSnapshot(models.Model):
    _name = 'dashboard.snapshot'
    _description = 'Dashboard Card History Point'
    _order = 'day desc, id desc'

    item_id = fields.Many2one(
        'dashboard.item', string='Card', required=True, ondelete='cascade', index=True)
    board_id = fields.Many2one(related='item_id.board_id', store=True, string='Dashboard')
    day = fields.Date(required=True, default=fields.Date.context_today, index=True)
    value = fields.Float(required=True)

    @api.model
    def _record(self, item, value, day=None):
        """One point per card per day: a second capture overwrites the first."""
        day = day or fields.Date.context_today(self)
        existing = self.search([('item_id', '=', item.id), ('day', '=', day)], limit=1)
        if existing:
            existing.value = value
            return existing
        return self.create({'item_id': item.id, 'day': day, 'value': value})

    @api.model
    def _capturable_items(self):
        """Number cards whose value is the same for everybody.

        A card that answers differently per reader ("only mine", ``uid`` in
        its domain) has no single number to record, so it is left out.
        """
        Item = self.env['dashboard.item']
        items = Item.search([('kind', 'in', NUMBER_KINDS), ('board_id.active', '=', True)])
        return items.filtered(lambda item: not item.only_mine and 'uid' not in (item.domain or ''))

    @api.model
    def _as_recorder(self, item):
        """The identity a card is recorded as: its owner for a personal board,
        the superuser for a shared one - whoever gives *the* number."""
        owner = item.board_id.owner_id
        return item.with_user(owner) if owner else item.sudo()

    @api.model
    def _cron_capture(self):
        """Nightly: record every capturable number card once."""
        for item in self._capturable_items():
            try:
                payload = self._as_recorder(item).compute_values('all')
            except Exception:  # noqa: BLE001 - one broken card must not stop the night
                continue
            if payload.get('error'):
                continue
            self._record(item, float(payload.get('value') or 0.0))
        return True
