# -*- coding: utf-8 -*-
"""20.0.1.2.0 -> 20.0.2.0.0, after the new code loads.

* Each existing scan becomes a register with one patient line holding its patient,
  what was asked for, the product, the delivery date, the urgency and its order (the
  old columns are still on the table here; Odoo drops them only after loading).
* The files that were on the scan become its Files.
* Registers ordered before Ordered On existed are dated by their first order.
* The seeded transfer platforms learn their share notifications - on a row still
  holding the value it was seeded with, so nothing the lab typed is overwritten
  (the channels are noupdate: the XML seeds fresh installs only).
* Raw inserts compute nothing: the stored totals are recomputed through the ORM.

(Ortho's 19.0.2.0.0, 19.0.2.1.0 and 19.0.2.5.0 in one step. Its later migrations
convert per-register prints and colour existing machines - neither ever existed here.)
"""
from odoo import SUPERUSER_ID, api

SEEDS = (
    # xmlid, old link pattern, new link pattern, new sender pattern
    ('channel_wetransfer', 'wetransfer.com', 'wetransfer.com/downloads, we.tl/', None),
    ('channel_google_drive', 'drive.google.com',
     'drive.google.com/drive/folders, drive.google.com/file/d, drive.google.com/open', 'drive-shares'),
    ('channel_dropbox', 'dropbox.com', 'dropbox.com/scl/, dropbox.com/s/, dropbox.com/sh/',
     'no-reply@dropbox.com'),
)


def _column(cr, column):
    cr.execute("SELECT 1 FROM information_schema.columns WHERE table_name='lab_scan' AND column_name=%s",
               (column,))
    return bool(cr.fetchone())


def _lines(cr):
    if not _column(cr, 'patient'):
        return

    def col(name, default='NULL'):
        return 's."%s"' % name if _column(cr, name) else default

    cr.execute(f"""
        INSERT INTO lab_scan_line
               (scan_id, sequence, patient_name, description, product_id, date_delivery,
                priority, arch, sale_order_id, company_id, create_uid, create_date, write_uid, write_date)
        SELECT s.id, 10, s.patient, {col('appliance')}, {col('product_id')},
               {col('delivery_requested')},
               CASE WHEN {col('urgent', 'FALSE')} THEN '1' ELSE '0' END,
               'both', {col('sale_order_id')}, s.company_id,
               s.create_uid, s.create_date, s.write_uid, s.write_date
          FROM lab_scan s
         WHERE NOT EXISTS (SELECT 1 FROM lab_scan_line l WHERE l.scan_id = s.id)
           AND (s.patient IS NOT NULL OR {col('appliance')} IS NOT NULL
                OR {col('sale_order_id')} IS NOT NULL)
    """)
    cr.execute("""
        UPDATE sale_order o SET scan_line_id = l.id
          FROM lab_scan_line l
         WHERE l.sale_order_id = o.id AND o.scan_line_id IS NULL
    """)


def _seeds(cr):
    for xmlid, old_link, new_link, sender in SEEDS:
        cr.execute("""
            UPDATE lab_scan_channel c SET link_match = %s
              FROM ir_model_data d
             WHERE d.module = 'lab_scan_register' AND d.name = %s
               AND d.model = 'lab.scan.channel' AND d.res_id = c.id AND c.link_match = %s
        """, (new_link, xmlid, old_link))
        if sender:
            cr.execute("""
                UPDATE lab_scan_channel c SET sender_match = %s
                  FROM ir_model_data d
                 WHERE d.module = 'lab_scan_register' AND d.name = %s
                   AND d.model = 'lab.scan.channel' AND d.res_id = c.id
                   AND (c.sender_match IS NULL OR c.sender_match = '')
            """, (sender, xmlid))


def migrate(cr, version):
    _lines(cr)
    # files that were on the scan become its Files
    cr.execute("""
        INSERT INTO lab_scan_attachment_rel (scan_id, attachment_id)
        SELECT a.res_id, a.id FROM ir_attachment a
          JOIN lab_scan s ON s.id = a.res_id
         WHERE a.res_model = 'lab.scan' AND a.res_field IS NULL
        ON CONFLICT DO NOTHING
    """)
    cr.execute("""
        UPDATE lab_scan s
           SET date_ordered = sub.first_order
          FROM (SELECT scan_id, MIN(create_date) AS first_order
                  FROM sale_order WHERE scan_id IS NOT NULL GROUP BY scan_id) sub
         WHERE sub.scan_id = s.id AND s.state = 'done' AND s.date_ordered IS NULL
    """)
    _seeds(cr)
    env = api.Environment(cr, SUPERUSER_ID, {})
    env.invalidate_all()
    lines = env['lab.scan.line'].search([])
    lines.modified(['patient_name', 'date_delivery', 'priority', 'sale_order_id', 'attachment_ids',
                    'scan_id'])
    env['lab.scan'].search([]).modified(['attachment_ids', 'line_ids'])
    env.flush_all()
