# -*- coding: utf-8 -*-
"""Adds an indexed ``x_odoo10_id`` cross-reference to every model the migration
syncs. It is the key used for idempotent upsert and for remapping relations to
the new Odoo 19 ids."""
from odoo import fields, models


class MigrationX10(models.AbstractModel):
    _name = 'migration.x10'
    _description = 'Odoo 10 cross-reference'

    x_odoo10_id = fields.Integer(string='Odoo 10 ID', index=True, copy=False)


class MigrationMap(models.Model):
    """Odoo 10 id -> Odoo 19 id map. Unlike the single per-record ``x_odoo10_id``,
    this supports MANY v10 ids mapping to ONE v19 record (e.g. v19 shares
    account.account across companies by code, so both companies' v10 accounts
    resolve to the same v19 account)."""
    _name = 'migration.map'
    _description = 'Odoo 10 → 19 id map'
    _rec_name = 'odoo10_id'

    dst_model = fields.Char('Model', index=True, required=True)
    odoo10_id = fields.Integer('Odoo 10 ID', index=True, required=True)
    odoo19_id = fields.Integer('Odoo 19 ID', required=True)

    _map_unique = models.Constraint('unique(dst_model, odoo10_id)',
                                    'One mapping per (model, Odoo 10 id).')


def _x10(model_name):
    """Build a small model class that mixes x_odoo10_id into an existing model."""
    return type(
        'X10_' + model_name.replace('.', '_'),
        (models.Model,),
        {'_name': model_name, '_inherit': [model_name, 'migration.x10'], '__module__': __name__},
    )


# Master-data models that receive an Odoo 10 reference.
X10_MODELS = [
    'res.company', 'res.partner', 'res.partner.bank', 'res.bank', 'res.users',
    'uom.uom',
    'account.account', 'account.journal', 'account.tax', 'account.tax.group',
    'account.payment.term', 'account.fiscal.position',
    'product.category', 'product.template', 'product.product', 'product.supplierinfo',
    'crm.team', 'stock.warehouse', 'stock.location',
    'mrp.bom', 'mrp.bom.line', 'mrp.workcenter', 'mrp.routing.workcenter',
    'product.colour', 'send.through', 'product.appliance.style',
    # documents migrated after the opening cut (see migration_txn.py)
    'sale.order', 'sale.order.line', 'account.move',
    # the opening's itemised lines: each carries the v10 receivable/payable line it
    # stands for, so a later edit in the old system can be matched to it one item
    # at a time instead of rebuilding every opening entry (client, 2026-09-18)
    'account.move.line',
    'mrp.production', 'stock.picking', 'purchase.order',
    # keyed so a re-run can update a purchase line in place instead of replacing
    # it, which would orphan the bill lines and receipts pointing at it
    'purchase.order.line',
]

_generated = [_x10(m) for m in X10_MODELS]
