# -*- coding: utf-8 -*-
"""Drive the Odoo 17 -> 19 migration from an Odoo shell, phase by phase.

Usage (from the odoo checkout, against the TARGET database):

    export MIG_FILESTORE=$PWD/backups/adl_20260923/filestore/adl_prod   # env BEFORE the pipe:
    printf 'exec(open("projects/arabian_dental/tools/run_migration.py").read())' | \
      instances/arabian_dental/venv/bin/python odoo19/odoo-bin shell \
        -c instances/arabian_dental/arabian_dental.conf -d arabian_dental_live --no-http

    (`MIG_X=... printf ... | python` sets the variable on printf only; export it.)

Environment:
    MIG_SRC_DB        source database name        (default adl_prod_v17)
    MIG_SRC_HOST/PORT/USER/PASSWORD                (default 127.0.0.1/5432/odoo/odoo)
    MIG_FILESTORE     path to the source filestore directory (attachments)
    MIG_PHASES        comma list: master,config,opening,numbering,gl,ops,inventory,timestamps
    MIG_INVENTORY_AS_OF on-hand as of this date (default today)
                      (default: all, in that order)
    MIG_FROM          documents from this date   (default 2025-01-01 = everything)
    MIG_LIMIT         smoke-test limit per phase (default 0 = all)

Every phase commits as it goes (the module batches its own commits), so a run
that stops can be resumed with MIG_PHASES set to what is left.
"""
import logging
import os
import time

_logger = logging.getLogger('run_migration')
logging.getLogger('odoo.addons.lab_migration').setLevel(logging.INFO)

phases = [p.strip() for p in os.environ.get(
    'MIG_PHASES', 'master,config,opening,numbering,gl,ops,inventory,timestamps').split(',') if p.strip()]
from_date = os.environ.get('MIG_FROM', '2025-01-01')

Backend = env['migration.backend'].sudo()
backend = Backend.search([('name', '=', 'Odoo 17 Source')], limit=1)
vals = {
    'name': 'Odoo 17 Source',
    'db_host': os.environ.get('MIG_SRC_HOST', '127.0.0.1'),
    'db_port': int(os.environ.get('MIG_SRC_PORT', '5432')),
    'db_name': os.environ.get('MIG_SRC_DB', 'adl_prod_v17'),
    'db_user': os.environ.get('MIG_SRC_USER', 'odoo'),
    'db_password': os.environ.get('MIG_SRC_PASSWORD', 'odoo'),
    # only when given: a phase run without it must not blank the path for the next
    # Everything the source holds is inside the document window, so the opening
    # (balances up to the day before) carries nothing and every entry arrives as
    # the entry it was. Move the two dates to cut over on a balance instead.
    'opening_date': os.environ.get('MIG_OPENING', '2024-12-31'),
    'opening_open_item_years': 0,
    'txn_from_date': from_date,
    'txn_limit': int(os.environ.get('MIG_LIMIT', '0')),
    'txn_only_new': os.environ.get('MIG_RESUME', '0') == '1',
}
if os.environ.get('MIG_FILESTORE'):
    vals['src_filestore'] = os.environ['MIG_FILESTORE']
if backend:
    backend.write(vals)
else:
    backend = Backend.create(vals)
env.cr.commit()
backend = backend._migration_env()

def show(title, lines):
    print('\n=== %s ===' % title)
    for line in (lines if isinstance(lines, (list, tuple)) else str(lines).split('\n')):
        print(line)

t0 = time.time()
for phase in phases:
    started = time.time()
    print('\n>>> phase: %s' % phase)
    if phase == 'master':
        cache, stats = backend._run(__import__(
            'odoo.addons.lab_migration.models.migration_spec', fromlist=['ENTITY_SPECS']).ENTITY_SPECS)
        env.cr.commit()
        show('MASTER DATA', stats)
    elif phase == 'config':
        show('CONFIGURATION', backend._sync_configuration()); env.cr.commit()
    elif phase == 'opening':
        show('OPENING', backend._opening_accounting() + backend._opening_inventory()); env.cr.commit()
    elif phase == 'numbering':
        backend.action_continue_invoice_numbering(); env.cr.commit()
        show('NUMBERING', backend.log)
    elif phase == 'gl':
        stats = backend._txn_run(['share_records', 'sale_orders', 'purchases', 'invoices',
                                  'journal_entries', 'reconcile', 'cheques'])
        show('DOCUMENTS (GL)', stats)
    elif phase == 'ops':
        stats = backend._txn_run(['manufacturing', 'pickings', 'stock_moves', 'move_lines',
                                  'material_requests', 'attachments'])
        show('DOCUMENTS (OPS)', stats)
    elif phase == 'bill_links':
        show('BILL LINKS', backend._txn_run(['bill_links']))
    elif phase == 'stock':
        show('STOCK', backend._txn_run(['pickings', 'stock_moves', 'move_lines']))
    elif phase == 'material_requests':
        show('MATERIAL REQUESTS', backend._txn_run(['material_requests']))
    elif phase == 'inventory':
        # on-hand as of the dump, per department store, once the transfers are in
        backend.write({'inventory_as_of': os.environ.get('MIG_INVENTORY_AS_OF')
                       or time.strftime('%Y-%m-%d')})
        show('INVENTORY', backend._opening_inventory()); env.cr.commit()
    elif phase == 'timestamps':
        backend.action_sync_timestamps(); env.cr.commit()
        show('TIMESTAMPS', backend.log)
    else:
        print('unknown phase %r, skipped' % phase)
    print('<<< %s took %.0fs' % (phase, time.time() - started))
print('\nALL DONE in %.0fs' % (time.time() - t0))
