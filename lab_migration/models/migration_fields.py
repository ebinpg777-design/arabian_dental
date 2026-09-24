# -*- coding: utf-8 -*-
"""Adds an indexed ``x_src_id`` cross-reference to every model the migration
syncs. It is the key used for idempotent upsert and for remapping relations to
the new Odoo 19 ids."""
from odoo import fields, models


class MigrationXref(models.AbstractModel):
    _name = 'migration.xref'
    _description = 'Odoo 17 cross-reference'

    x_src_id = fields.Integer(string='Odoo 17 ID', index=True, copy=False)


class MigrationMap(models.Model):
    """Odoo 17 id -> Odoo 19 id map. Unlike the single per-record ``x_src_id``,
    this supports MANY v17 ids mapping to ONE v19 record (e.g. v19 shares
    account.account across companies by code, so both companies' v17 accounts
    resolve to the same v19 account)."""
    _name = 'migration.map'
    _description = 'Odoo 17 → 19 id map'
    _rec_name = 'src_id'

    dst_model = fields.Char('Model', index=True, required=True)
    src_id = fields.Integer('Odoo 17 ID', index=True, required=True)
    dst_id = fields.Integer('Odoo 19 ID', required=True)

    _map_unique = models.Constraint('unique(dst_model, src_id)',
                                    'One mapping per (model, Odoo 17 id).')


def _xref(model_name):
    """Build a small model class that mixes x_src_id into an existing model."""
    return type(
        'XREF_' + model_name.replace('.', '_'),
        (models.Model,),
        {'_name': model_name, '_inherit': [model_name, 'migration.xref'], '__module__': __name__},
    )


# Master-data models that receive an Odoo 17 reference.
XREF_MODELS = [
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
    # the opening's itemised lines: each carries the v17 receivable/payable line it
    # stands for, so a later edit in the old system can be matched to it one item
    # at a time instead of rebuilding every opening entry (client, 2026-09-18)
    'account.move.line',
    'mrp.production', 'stock.picking', 'purchase.order',
    # keyed so a re-run can update a purchase line in place instead of replacing
    # it, which would orphan the bill lines and receipts pointing at it
    'purchase.order.line',
    # Odoo 17 source: the lab's own masters that map onto the suite's
    'res.partner.category', 'account.fiscal.position', 'stock.location',
    'hr.department', 'hr.job', 'hr.employee', 'stock.move', 'stock.move.line',
    'stock.warehouse', 'stock.picking.type', 'stock.warehouse.orderpoint',
    'lab.cheque', 'ir.attachment',
    # the chatter: keyed so a re-run adds what is missing instead of doubling
    # every thread
    'mail.message',
]

_generated = [_xref(m) for m in XREF_MODELS]
