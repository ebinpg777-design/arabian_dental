# -*- coding: utf-8 -*-
"""Scan files and chatter are kept for a while, not for ever.

A register's scans and a plate's sliced file are the bulk of the filestore - a single
.ctb is often over 100 MB - and three months on the case is long made: the order has
its own copy of every scan (`action_create_orders` copies them onto it), and the
plate's figures stay on the plate. So the files of a register or a print order, and
its chatter, are removed once the record is older than the months set in Settings
(client, 2026-10-14: "remove attachments from scan registers and print orders and
the chatters of scan register and print order after three months of the date of
creation - the 3 months can be configurable").

What goes: the files the record holds - its Files list, what was posted in its
chatter, a plate's sliced file - and every chatter message. What stays: the record
itself, its patients and figures, its activities and followers, and the plate's
small preview image (a field of the plate, not a file anybody attached).

A file that a NEWER record still uses is not deleted with the old one: a restarted
plate prints from its original's sliced file, and a scan can sit in two registers.
It is handed to the record still using it instead, so the old record is clean and
the new one keeps its file. The sale orders made from a register hold copies of
their own and are never touched.
"""
import logging

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.tools import SQL

_logger = logging.getLogger(__name__)

PARAM = 'lab_scan_register.retention_months'
DEFAULT_MONTHS = 3
BATCH = 50


class ScanRetention(models.AbstractModel):
    _name = 'lab.scan.retention'
    _description = 'Scan files and chatter retention'

    # ------------------------------------------------------------------ the rule
    @api.model
    def _months(self):
        return max(0, self.env['ir.config_parameter'].sudo().get_int(PARAM, DEFAULT_MONTHS))

    @api.model
    def _cutoff(self, months=None):
        """Records created before this lose their files and chatter; None when off."""
        months = self._months() if months is None else months
        return fields.Datetime.now() - relativedelta(months=months) if months > 0 else None

    @api.model
    def _due_ids(self, model, cutoff, limit=None):
        """Records created before `cutoff` that still hold a file or a message -
        the ones already cleared are not read again every night."""
        Model = self.env[model]
        holds = [
            SQL("EXISTS (SELECT 1 FROM ir_attachment a WHERE a.res_model = %s AND a.res_id = r.id"
                " AND a.res_field IS NULL)", model),
            SQL("EXISTS (SELECT 1 FROM mail_message m WHERE m.model = %s AND m.res_id = r.id)", model),
        ]
        if model == 'lab.scan':
            holds += [
                SQL("EXISTS (SELECT 1 FROM lab_scan_attachment_rel x WHERE x.scan_id = r.id)"),
                SQL("EXISTS (SELECT 1 FROM lab_scan_line l JOIN lab_scan_line_attachment_rel y"
                    " ON y.line_id = l.id WHERE l.scan_id = r.id)"),
            ]
        elif model == 'lab.scan.print.order':
            holds.append(SQL("r.slice_attachment_id IS NOT NULL"))
        self.env.flush_all()
        return [row[0] for row in self.env.execute_query(SQL(
            "SELECT r.id FROM %s r WHERE r.create_date < %s AND (%s) ORDER BY r.id %s",
            SQL.identifier(Model._table), cutoff, SQL(" OR ").join(holds),
            SQL("LIMIT %s", limit) if limit else SQL()))]

    # ------------------------------------------------------------------ the run
    @api.model
    def _cron_clear_old(self, batch=BATCH):
        """Nightly: clear what is past its months, oldest first, a batch at a time -
        the first run on a database that has kept everything can be long, and a
        batch committed is a batch that never has to be done again."""
        cutoff = self._cutoff()
        if not cutoff:
            return
        in_cron = bool(self.env.context.get('ir_cron_progress_id'))
        for model in ('lab.scan', 'lab.scan.print.order'):
            while True:
                ids = self._due_ids(model, cutoff, limit=batch)
                if not ids:
                    break
                self._clear(model, ids)
                if in_cron and not self.env['ir.cron']._commit_progress(len(ids)):
                    return          # out of time: the next run carries on
                if not in_cron and len(ids) < batch:
                    break

    @api.model
    def _clear(self, model, ids):
        """Remove the files and the chatter of these records (all past their months)."""
        records = self.env[model].sudo().browse(ids)
        self.env.flush_all()
        if model == 'lab.scan':
            removed = self._clear_scans(records)
        else:
            removed = self._clear_plates(records)
        messages = self.env['mail.message'].sudo().search([('model', '=', model), ('res_id', 'in', ids)])
        count = len(messages)
        messages.unlink()
        _logger.info("scan retention: %s - %d record(s), %d file(s), %d message(s) removed",
                     model, len(records), removed, count)
        return removed, count

    def _clear_scans(self, scans):
        Attachment = self.env['ir.attachment'].sudo()
        lines = scans.line_ids
        held = Attachment.search([('res_model', '=', 'lab.scan'), ('res_id', 'in', scans.ids),
                                  ('res_field', '=', False)])
        held |= Attachment.search([('res_model', '=', 'lab.scan.line'), ('res_id', 'in', lines.ids),
                                   ('res_field', '=', False)])
        held |= scans.attachment_ids.sudo() | lines.attachment_ids.sudo()
        # a scan another register still lists stays - and becomes that register's
        keep = {}
        if held:
            for att_id, scan_id in self.env.execute_query(SQL(
                    "SELECT attachment_id, MAX(scan_id) FROM lab_scan_attachment_rel"
                    " WHERE attachment_id IN %s AND scan_id NOT IN %s GROUP BY attachment_id",
                    tuple(held.ids), tuple(scans.ids))):
                keep[att_id] = scan_id
        for att_id, scan_id in keep.items():
            Attachment.browse(att_id).write({'res_model': 'lab.scan', 'res_id': scan_id})
        gone = held.filtered(lambda a: a.id not in keep)
        gone.unlink()
        # whatever the old registers and their patients still list is no longer theirs
        self.env.execute_query(SQL("DELETE FROM lab_scan_attachment_rel WHERE scan_id IN %s", tuple(scans.ids)))
        if lines:
            self.env.execute_query(SQL("DELETE FROM lab_scan_line_attachment_rel WHERE line_id IN %s",
                                       tuple(lines.ids)))
        # the Files count and size follow
        scans.invalidate_recordset(['attachment_ids'])
        lines.invalidate_recordset(['attachment_ids'])
        scans.modified(['attachment_ids'])
        lines.modified(['attachment_ids'])
        scans.flush_recordset()
        return len(gone)

    def _clear_plates(self, plates):
        Attachment = self.env['ir.attachment'].sudo()
        held = Attachment.search([('res_model', '=', 'lab.scan.print.order'), ('res_id', 'in', plates.ids),
                                  ('res_field', '=', False)])
        held |= plates.slice_attachment_id.sudo()
        # a restart prints from its original's sliced file: it stays, and is the restart's
        keep = {}
        if held:
            for att_id, plate_id in self.env.execute_query(SQL(
                    "SELECT slice_attachment_id, MAX(id) FROM lab_scan_print_order"
                    " WHERE slice_attachment_id IN %s AND id NOT IN %s GROUP BY slice_attachment_id",
                    tuple(held.ids), tuple(plates.ids))):
                keep[att_id] = plate_id
        for att_id, plate_id in keep.items():
            Attachment.browse(att_id).write({'res_model': 'lab.scan.print.order', 'res_id': plate_id})
        gone = held.filtered(lambda a: a.id not in keep)
        gone.unlink()
        # the plate keeps its figures (resin, layers, time) - only the file is gone
        self.env.execute_query(SQL("UPDATE lab_scan_print_order SET slice_attachment_id = NULL WHERE id IN %s",
                                   tuple(plates.ids)))
        plates.invalidate_recordset(['slice_attachment_id'])
        return len(gone)

    # ------------------------------------------------------------------ what it would do
    @api.model
    def _preview(self, months):
        """How much the rule would clear now, for the settings page."""
        cutoff = self._cutoff(months)
        if not cutoff:
            return {'scans': 0, 'plates': 0, 'files': 0, 'bytes': 0}
        scans = self._due_ids('lab.scan', cutoff)
        plates = self._due_ids('lab.scan.print.order', cutoff)
        files, size = 0, 0
        for model, ids in (('lab.scan', scans), ('lab.scan.print.order', plates)):
            if ids:
                n, b = self.env.execute_query(SQL(
                    "SELECT COUNT(*), COALESCE(SUM(file_size), 0) FROM ir_attachment"
                    " WHERE res_model = %s AND res_id IN %s AND res_field IS NULL", model, tuple(ids)))[0]
                files, size = files + n, size + b
        return {'scans': len(scans), 'plates': len(plates), 'files': files, 'bytes': size}


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    scan_retention_months = fields.Integer(
        'Keep scan files and chatter for (months)', config_parameter=PARAM, default=DEFAULT_MONTHS,
        help="A scan register's and a print order's files and chatter are removed once the record is "
             "this many months old. The register, its patients and the plate's figures stay; the sale "
             "orders keep their own copies of the scans. 0 keeps everything.")
    scan_retention_preview = fields.Char('Would clear now', compute='_compute_scan_retention_preview')

    @api.depends('scan_retention_months')
    def _compute_scan_retention_preview(self):
        Retention = self.env['lab.scan.retention']
        for settings in self:
            months = max(0, settings.scan_retention_months or 0)
            if not months:
                settings.scan_retention_preview = self.env._("Nothing is removed while this is 0.")
                continue
            p = Retention._preview(months)
            settings.scan_retention_preview = self.env._(
                "%(scans)s register(s) and %(plates)s print order(s) older than %(months)s month(s): "
                "%(files)s file(s), %(size)s.",
                scans=p['scans'], plates=p['plates'], months=months, files=p['files'],
                size=self.env['lab.scan']._human_size(p['bytes']) if p['bytes'] else '0 B')
