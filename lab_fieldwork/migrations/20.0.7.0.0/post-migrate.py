# -*- coding: utf-8 -*-
"""The movements pre-migrate.py set aside, booked as field cash (lab.cash.movement),
and the visits, door collections and handovers linked to them again."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    cr.execute("SELECT to_regclass('fw_mig_cash')")
    if not cr.fetchone()[0]:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Users = env['res.users'].with_context(active_test=False)
    Move = env['lab.cash.movement']
    cr.execute("""
        SELECT id, type, state, amount, date, partner_id, visit_id, handover_id,
               company_id, note, holder_partner_id
          FROM fw_mig_cash ORDER BY date, id""")
    booked = {}
    for (old_id, kind, state, amount, day, partner_id, visit_id, handover_id,
         company_id, note, holder) in cr.fetchall():
        user = Users.search([('partner_id', '=', holder)], limit=1)
        if not user or not amount or amount <= 0:
            continue
        booked[old_id] = Move.create({
            'user_id': user.id,
            'kind': kind,
            'state': 'posted' if state == 'posted' else 'cancelled',
            'amount': amount,
            'date': day,
            'partner_id': partner_id if kind == 'collection' else False,
            'visit_id': visit_id,
            'handover_id': handover_id,
            'company_id': company_id,
            'note': note,
        }).id
    for table, column, model, back in (('lab_visit', 'cash_txn_id', 'lab.visit', None),
                                       ('lab_cash_collection', 'cash_txn_id',
                                        'lab.cash.collection', 'collection_id'),
                                       ('lab_cash_handover', 'txn_id', 'lab.cash.handover', None)):
        cr.execute(f"SELECT id, fw_mig_{column} FROM {table} WHERE fw_mig_{column} IS NOT NULL")
        for rec_id, old_id in cr.fetchall():
            if old_id not in booked:
                continue
            env[model].browse(rec_id).write({column: booked[old_id]})
            if back:
                Move.browse(booked[old_id]).write({back: rec_id})
        cr.execute(f"ALTER TABLE {table} DROP COLUMN fw_mig_{column}")
    cr.execute("DROP TABLE fw_mig_cash")
    # Floats no longer open: there are none.
    env['ir.config_parameter'].search([('key', '=', 'lab_fieldwork.auto_float')]).unlink()
