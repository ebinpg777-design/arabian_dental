# -*- coding: utf-8 -*-
{
    'name': 'Lab Migration (Odoo 10 → 19 data sync)',
    'version': '19.0.1.2.0',
    'category': 'Technical',
    'summary': 'Sync master data + opening balances from an Odoo 10 database into Odoo 19',
    'description': """
Odoo 10 → Odoo 19 data bridge
=============================
Connects directly to an Odoo 10 PostgreSQL database and syncs master data and
opening balances into this Odoo 19 database.

* Every synced record keeps its Odoo 10 id in ``x_odoo10_id`` (indexed) so a
  re-sync updates in place (idempotent) and relations are remapped to the NEW
  Odoo 19 ids automatically.
* Two-pass engine: create/update scalars first, then resolve every
  many2one / many2many / one2many by the target's ``x_odoo10_id``.
* Opening balances: accounting (per account & partner as of a cutoff date) and
  inventory (on-hand per product/location as of the cutoff).
""",
    'author': 'ISPG',
    'depends': [
        'sale_custom',
        'lab_reports',
        'account',
        'stock',
        'mrp',
    ],
    'data': [
        'security/lab_migration_security.xml',
        'security/ir.model.access.csv',
        'views/migration_views.xml',
    ],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
