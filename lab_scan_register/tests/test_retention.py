# -*- coding: utf-8 -*-
"""Files and chatter of old registers and print orders are removed; nothing else is.

(client, 2026-10-14: "remove attachments from scan registers and print orders and the
chatters of scan register and print order after three months of the date of creation
- the 3 months can be configurable")
"""
from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests import tagged

from .common import ORDER, ScanCase
from .test_slice_file import ctb

PARAM = 'lab_scan_register.retention_months'


@tagged('post_install', '-at_install')
class TestRetention(ScanCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ir.config_parameter'].sudo().set_int(PARAM, 3)
        cls.Retention = cls.env['lab.scan.retention']

    def _age(self, record, months, days=1):
        """As if the record had been created `months` (and a day) ago."""
        self.env.flush_all()
        when = fields.Datetime.now() - relativedelta(months=months, days=days)
        self.env.cr.execute('UPDATE "%s" SET create_date = %%s WHERE id = %%s' % record._table, (when, record.id))
        record.invalidate_recordset(['create_date'])

    def _messages(self, record):
        return self.env['mail.message'].search_count([('model', '=', record._name), ('res_id', '=', record.id)])

    def _upload(self, data, name='plate.ctb'):
        return self.env['ir.attachment'].create({'name': name, 'raw': data, 'mimetype': 'application/octet-stream'})

    def _filled_register(self):
        scan = self._register(patients=(('Tanvi', 'upper'), ('Aarav', 'lower')))
        listed = self._file('upper.stl', scan=scan)
        patient = self._file('lower.stl', scan=scan)
        scan.attachment_ids = [(4, listed.id)]
        scan.line_ids[1].attachment_ids = [(4, patient.id)]
        posted = self._file('photo.jpg', scan=scan)
        scan.message_post(body='the bite photo', attachment_ids=posted.ids)
        return scan, listed | patient | posted

    # ------------------------------------------------------------ registers
    def test_an_old_register_loses_its_files_and_chatter_and_nothing_else(self):
        scan, files = self._filled_register()
        self.assertTrue(scan.has_attachments and self._messages(scan))
        self._age(scan, 3)
        self.Retention._cron_clear_old()
        self.assertTrue(scan.exists() and len(scan.line_ids) == 2, "the register and its patients stay")
        self.assertFalse(files.exists(), "its files are gone")
        self.assertFalse(scan.attachment_ids or scan.line_ids.attachment_ids)
        self.assertEqual((scan.attachment_count, scan.has_attachments), (0, False), "and its Files count says so")
        self.assertEqual(self._messages(scan), 0, "its chatter is gone")

    def test_a_younger_register_keeps_everything(self):
        scan, files = self._filled_register()
        self._age(scan, 2, days=20)
        self.Retention._cron_clear_old()
        self.assertEqual(files.exists(), files)
        self.assertTrue(self._messages(scan))

    def test_the_months_are_a_setting_and_nought_keeps_everything(self):
        scan, files = self._filled_register()
        self._age(scan, 4)
        self.env['ir.config_parameter'].sudo().set_int(PARAM, 6)
        self.Retention._cron_clear_old()
        self.assertEqual(files.exists(), files, "four months is inside six")
        self.env['ir.config_parameter'].sudo().set_int(PARAM, 0)
        self._age(scan, 40)
        self.Retention._cron_clear_old()
        self.assertEqual(files.exists(), files, "0 keeps everything")
        self.env['ir.config_parameter'].sudo().set_int(PARAM, 3)
        self.Retention._cron_clear_old()
        self.assertFalse(files.exists())

    def test_the_orders_made_from_a_register_keep_their_copies(self):
        scan = self._to_printed(self._register())
        scan.action_create_orders()
        order = scan.line_ids.sale_order_id
        copies = self.env['ir.attachment'].search([('res_model', '=', 'sale.order'), ('res_id', '=', order.id)])
        self.assertTrue(copies)
        self._age(scan, 3)
        self.Retention._cron_clear_old()
        self.assertFalse(scan.attachment_ids)
        self.assertEqual(copies.exists(), copies, "the order's own copies are not the register's")
        self.assertTrue(copies[0].raw, "and their content is still there")

    def test_a_scan_a_younger_register_still_lists_becomes_that_registers(self):
        old, _files = self._filled_register()
        shared = old.attachment_ids[0]
        young = self._register()
        young.attachment_ids = [(4, shared.id)]
        self.assertEqual((shared.res_model, shared.res_id), (old._name, young.id),
                         "the last register to list a scan owns it")
        shared.res_id = old.id                      # owned by the old one, listed by both
        self._age(old, 3)
        self.Retention._cron_clear_old()
        self.assertTrue(shared.exists(), "a file still in use is not deleted")
        self.assertEqual((shared.res_model, shared.res_id), (young._name, young.id), "it is the younger register's now")
        self.assertIn(shared, young.attachment_ids)
        self.assertNotIn(shared, old.attachment_ids)

    def test_many_old_registers_are_cleared_in_batches(self):
        scans = self.env['lab.scan']
        for _i in range(5):
            scan, _files = self._filled_register()
            self._age(scan, 5)
            scans |= scan
        self.Retention._cron_clear_old(batch=2)
        self.assertFalse(scans.attachment_ids)
        self.assertFalse(sum(self._messages(s) for s in scans))
        self.assertFalse(self.Retention._due_ids('lab.scan', self.Retention._cutoff()), "nothing is read again")

    # ------------------------------------------------------------ print orders
    def test_an_old_plate_loses_its_sliced_file_and_chatter_but_keeps_its_figures(self):
        scan = self._queued()
        plate = self._plate(scan.line_ids)
        upload = self._upload(ctb(), 'a.ctb')
        plate.slice_attachment_id = upload
        self.assertEqual(plate.layer_count, 500)
        self._age(plate, 3)
        self.Retention._cron_clear_old()
        self.assertFalse(upload.exists())
        self.assertFalse(plate.slice_attachment_id)
        self.assertEqual((plate.layer_count, plate.slice_filename), (500, 'a.ctb'), "the plate's figures stay")
        self.assertEqual(self._messages(plate), 0)

    def test_a_restart_keeps_the_sliced_file_it_prints_from(self):
        scan = self._queued()
        plate = self._plate(scan.line_ids)
        upload = self._upload(ctb(), 'a.ctb')
        plate.slice_attachment_id = upload
        self._start(plate)
        restart = self._fail(plate, restart=True)
        self.assertEqual(restart.slice_attachment_id, upload, "shared, not copied")
        self._age(plate, 3)
        self.Retention._cron_clear_old()
        self.assertTrue(upload.exists(), "the restart still prints from it")
        self.assertEqual((upload.res_model, upload.res_id), (ORDER, restart.id))
        self.assertEqual(restart.slice_attachment_id, upload)
        self.assertFalse(plate.slice_attachment_id)

    # ------------------------------------------------------------ settings
    def test_the_settings_say_what_would_go(self):
        scan, _files = self._filled_register()
        self._age(scan, 4)
        settings = self.env['res.config.settings'].create({})
        self.assertEqual(settings.scan_retention_months, 3)
        self.assertIn('register(s)', settings.scan_retention_preview)
        self.assertNotIn('0 register(s)', settings.scan_retention_preview)
        settings.scan_retention_months = 0
        self.assertIn('Nothing is removed', settings.scan_retention_preview)

    def test_the_cron_exists_and_runs_nightly(self):
        cron = self.env.ref('lab_scan_register.cron_clear_old_files')
        self.assertTrue(cron.active)
        self.assertEqual((cron.interval_number, cron.interval_type), (1, 'days'))
