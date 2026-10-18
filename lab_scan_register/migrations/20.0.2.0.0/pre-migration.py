# -*- coding: utf-8 -*-
"""20.0.1.2.0 -> 20.0.2.0.0, before the new code loads: one register, many patients.

Ortho's redesign (its 19.0.2.0.0) brought over to a lab database that already has the
one-scan-one-patient register installed. The header fields get the standard names,
renamed in place - column, ir.model.fields row and its xmlid - so their values and
tracking history carry over instead of being dropped with the old field and recreated
empty. The states take the standard keys. The per-patient columns (patient,
appliance, product, delivery date, urgency, order) stay where they are for the
post-migration to move into patient lines.
"""

RENAMES = {
    'received_on': 'date_received',
    'posted_by': 'sender_name',
    'posted_phone': 'sender_phone',
    'confirmed_by_id': 'confirm_user_id',
    'confirmed_on': 'date_confirmed',
    'acknowledged_on': 'date_acknowledged',
    'link': 'download_url',
    'link_expires': 'download_expiry',
    'file_count': 'attachment_count',
    'file_size': 'attachment_size',
    'files_received': 'has_attachments',
    'notes': 'note',
    'pasted_message': 'message_text',
    'external_ref': 'ref',
}
STATES = {'new': 'draft', 'ordered': 'done', 'cancelled': 'cancel'}


def _column(cr, table, column):
    cr.execute("SELECT 1 FROM information_schema.columns WHERE table_name=%s AND column_name=%s",
               (table, column))
    return bool(cr.fetchone())


def migrate(cr, version):
    if not _column(cr, 'lab_scan', 'id'):
        return
    for old, new in RENAMES.items():
        if _column(cr, 'lab_scan', old) and not _column(cr, 'lab_scan', new):
            cr.execute(f'ALTER TABLE lab_scan RENAME COLUMN "{old}" TO "{new}"')
        cr.execute("UPDATE ir_model_fields SET name=%s WHERE model='lab.scan' AND name=%s "
                   "AND NOT EXISTS (SELECT 1 FROM ir_model_fields WHERE model='lab.scan' AND name=%s)",
                   (new, old, new))
        cr.execute("UPDATE ir_model_data SET name=%s WHERE module='lab_scan_register' "
                   "AND model='ir.model.fields' AND name=%s "
                   "AND NOT EXISTS (SELECT 1 FROM ir_model_data WHERE module='lab_scan_register' AND name=%s)",
                   ('field_lab_scan__' + new, 'field_lab_scan__' + old, 'field_lab_scan__' + new))
    for old, new in STATES.items():
        cr.execute("UPDATE lab_scan SET state=%s WHERE state=%s", (new, old))
