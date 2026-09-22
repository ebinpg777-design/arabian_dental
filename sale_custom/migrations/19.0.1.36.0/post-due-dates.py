# -*- coding: utf-8 -*-
"""The day every case already in hand is owed. (client, 2026-09-18)

The rule is two days from registration, and the same day when the case is urgent.
A case confirmed before this existed has no such date, so the Due for Invoicing
list would start empty and stay wrong for months. Written here in one statement,
from each order's own registration date, and only where nobody has set one.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    lead = env['sale.order'].LEAD_DAYS
    cr.execute("""
        UPDATE sale_order so
           SET date_due = (so.date_order AT TIME ZONE 'UTC' AT TIME ZONE %s)::date
                          + CASE WHEN so.priority = 'urgent'
                                   OR EXISTS (SELECT 1 FROM sale_order_line sol
                                               WHERE sol.order_id = so.id AND sol.is_urgent)
                                 THEN 0 ELSE %s END
         WHERE so.date_due IS NULL
           AND so.state IN ('sale', 'done')
           AND so.date_order IS NOT NULL
    """, ('Asia/Kolkata', lead))
    env.cr.execute("SELECT count(*) FROM sale_order WHERE date_due IS NOT NULL")
    [(filled,)] = env.cr.fetchall()
    env['ir.logging'].sudo().create({
        'name': 'sale_custom', 'type': 'server', 'level': 'INFO',
        'dbname': env.cr.dbname, 'message': 'Due dates written on %s orders' % filled,
        'path': __name__, 'func': 'migrate', 'line': '0',
    })
