# -*- coding: utf-8 -*-
{
    'name': 'Lab Migration (Odoo 17 → 19 data sync)',
    'version': '19.0.2.0.0',
    'category': 'Technical',
    'summary': 'Sync master data + opening balances from an Odoo 17 database into Odoo 19',
    'description': """
Odoo 17 → Odoo 19 data bridge
=============================
Connects directly to Arabian Dental Lab's Odoo 17 PostgreSQL database and brings
its data into this Odoo 19 database — masters, documents, ledger, reconciliation,
cheques and attachments — WITHOUT its custom modules. Their fields are mapped onto
the lab suite's own (patient, shade → colour, jaw → U/L, quadrants → teeth,
technicians, districts → tags, doctors → contacts).

* Every synced record keeps its source id in ``x_src_id`` (indexed) so a re-sync
  updates in place (idempotent) and relations are remapped to the NEW ids.
* Two-pass engine: create/update scalars first, then resolve every
  many2one / many2many by the target's ``x_src_id``.
* Documents are written, not replayed; invoices are posted and reconciled the
  way the source ledger was.
""",
    'author': 'ISPG',
    'depends': [
        'sale_custom',
        'lab_reports',
        'lab_finance_ops',
        'account',
        'stock',
        'mrp',
        'purchase',
        'hr',
        'l10n_in',
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
