# -*- coding: utf-8 -*-

from odoo.tests.common import TransactionCase

ORDER = 'lab.scan.print.order'


class PrintingSteps:
    """The printing room's steps the way the room takes them - through its dialogs,
    so a test exercises the same path a click does. Needs `self.printer`."""

    def _plate(self, scan_lines, printer=None, **vals):
        """Put these patients on a new plate (New Print Order); returns the plate."""
        wiz = self.env[ORDER + '.make'].with_context(
            active_model='lab.scan.line', active_ids=scan_lines.ids).create(
            dict({'printer_id': (printer or self.printer).id}, **vals))
        action = wiz.action_create()
        return self.env[ORDER].browse(action['res_id'])

    def _start(self, order, printer=None, **vals):
        """Onto the machine (Start Print)."""
        wiz = self.env[ORDER + '.start'].with_context(
            active_model=ORDER, active_ids=order.ids, active_id=order.id).create(
            dict({'printer_id': (printer or order.printer_id or self.printer).id}, **vals))
        wiz.action_start()
        return order

    def _off(self, order, failed=(), reason='adhesion', replate=False, **vals):
        """The plate came off (Plate Off). `failed`: patients (lab.scan.line) whose
        model was lost, or (patient, 'upper'|'lower'|'all', row_reason) tuples.
        Returns the re-plate, if one was made."""
        wiz = self.env[ORDER + '.off'].with_context(
            active_model=ORDER, active_ids=order.ids, active_id=order.id).create(
            dict({'reason': reason if failed else False, 'replate': replate}, **vals))
        for item in failed:
            patient, part, row_reason = (item, 'all', False) if not isinstance(item, tuple) \
                else (tuple(item) + (False, False))[:3]
            row = wiz.line_ids.filtered(lambda r: r.line_id.scan_line_id == patient)
            assert row, "%s is not on %s" % (patient.patient_name, order.name)
            row.write({'ok': False, 'failed_part': part or 'all', 'reason': row_reason or False})
        action = wiz.action_confirm()
        return self.env[ORDER].browse(action.get('res_id')) if action.get('res_id') else self.env[ORDER]

    def _fail(self, order, reason='power', restart=False, printer=None, **vals):
        """The whole plate failed mid-print (Print Failed). Returns the restart, if any."""
        wiz = self.env[ORDER + '.fail'].with_context(
            active_model=ORDER, active_ids=order.ids, active_id=order.id).create(
            dict({'reason': reason, 'restart': restart,
                  'printer_id': (printer or order.printer_id).id if restart else False}, **vals))
        action = wiz.action_confirm()
        return self.env[ORDER].browse(action.get('res_id')) if action.get('res_id') else self.env[ORDER]


class ScanCase(PrintingSteps, TransactionCase):
    """A clinic, its WhatsApp group, two products and a front-desk user."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.tz = 'Asia/Kolkata'
        cls.Scan = cls.env['lab.scan']
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Smile Point Dental Care Feroke', 'is_company': True, 'email': 'feroke@example.com'})
        cls.other_clinic = cls.env['res.partner'].create({'name': 'Kids Dental Clinic Calicut', 'is_company': True})
        cls.group = cls.env['lab.scan.channel'].create({
            'name': 'Smile Point Dental Care Feroke (WA)', 'kind': 'whatsapp', 'partner_id': cls.clinic.id})
        cls.hawley = cls.env['product.product'].create({
            'name': "Hawley's retainer", 'type': 'consu', 'list_price': 1000.0, 'sale_ok': True})
        cls.expander = cls.env['product.product'].create({
            'name': 'Bilateral expander', 'type': 'consu', 'list_price': 1500.0, 'sale_ok': True})
        cls.printer = cls.env['lab.scan.printer'].create({
            'name': 'Sonic Mini', 'brand': 'Phrozen Sonic Mini 8K', 'typical_hours': 3.0})
        cls.desk = cls.env['res.users'].create({
            'name': 'Front Desk', 'login': 'scan_front_desk',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('lab_scan_register.group_scan_user').id])]})

    def _register(self, patients=(('Tanvi', 'upper bilateral expansion'),), product=True, **vals):
        lines = [(0, 0, {'patient_name': name, 'description': desc,
                         'product_id': self.hawley.id if product else False})
                 for name, desc in patients]
        return self.Scan.create(dict({'channel_id': self.group.id, 'partner_id': self.clinic.id,
                                      'line_ids': lines}, **vals))

    def _file(self, name='upper.stl', size=1024, scan=None):
        vals = {'name': name, 'raw': b'x' * size}
        if scan:
            vals.update(res_model=scan._name, res_id=scan.id)
        return self.env['ir.attachment'].create(vals)

    def _queued(self, **vals):
        """A register sent for printing, its patients waiting for a plate."""
        scan = self._register(**vals)
        if not scan.attachment_ids and not scan.download_url:
            scan.attachment_ids = [(4, self._file().id)]
        scan.action_confirm()
        scan._send_to_print()
        return scan

    def _to_printed(self, scan):
        """Confirmed, on a plate, off clean and cured: ready for its orders."""
        if not scan.attachment_ids and not scan.download_url:
            scan.attachment_ids = [(4, self._file().id)]
        scan.action_confirm()
        order = self._start(self._plate(scan.line_ids))
        self._off(order)
        order.action_cure()
        return scan
