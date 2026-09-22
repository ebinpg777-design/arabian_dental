# -*- coding: utf-8 -*-
import base64
import io

from odoo import _, api, fields, models
from odoo.exceptions import UserError

try:
    import qrcode
except ImportError:                                    # pragma: no cover
    qrcode = None


class MrpWorkcenter(models.Model):
    """A work centre you can point a camera at.

    The code is generated rather than typed. A station label is stuck to a machine and
    read by a phone across a workshop; leaving it to be invented by hand produces
    duplicates, spaces and lookalike characters, and the failure only shows up months
    later when two stations answer to the same scan.
    """
    _inherit = 'mrp.workcenter'

    scan_code = fields.Char(
        'Scan Code', copy=False, index=True, readonly=True,
        help="Printed on this station's QR label. Generated, so it is unique and "
             "unambiguous when read by a camera.")
    qr_image = fields.Binary('QR Label', compute='_compute_qr_image')
    # Who runs this station. The station screen opens on THEIR bench without being
    # told which one it is — a shared terminal that asks "which station are you?" every
    # morning gets answered wrong eventually.
    # A scan is one tap with gloves on, and the wrong card gets scanned. The board
    # now says what the scan will do and waits to be told to do it. On by default
    # because the lab asked for it; a bench clearing a tray of forty can turn it
    # off and keep the old one-tap scan. (client, 2026-09-09)
    scan_confirm = fields.Boolean(
        'Confirm each scan', default=True,
        help="The board shows what a scan will do - accept the job, or finish it "
             "and hand it on - and does it only when the operator confirms. Turn "
             "off at a bench that scans in bulk.")
    # Some benches finish work somebody else made: polishing, packing, the last
    # check before a case goes back to the doctor. There the question "who did
    # this?" has two answers, and the lab wants both. Off everywhere by default -
    # a station that does not finish work is not asked. (client, 2026-09-09)
    is_finisher = fields.Boolean(
        'Names a finishing technician', default=False,
        help="Tick where the person who FINISHES a job is recorded separately from "
             "the technician who did it. The board then asks for the finishing "
             "technician before it will hand a job on from here.")
    head_user_ids = fields.Many2many(
        'res.users', 'mrp_workcenter_head_rel', 'workcenter_id', 'user_id',
        string='Production Leads',
        help="They run this station: its board opens on their screen by default, and "
             "they are the ones who decide which technician does which job. Everyone "
             "else posted here does the work but cannot give it out.")

    _scan_code_uniq = models.Constraint(
        'unique(scan_code)',
        'Two work centres cannot share a scan code — a scan would be ambiguous.')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals.setdefault('scan_code', self._next_scan_code())
        return super().create(vals_list)

    @api.model
    def _next_scan_code(self):
        return self.env['ir.sequence'].next_by_code('mrp.workcenter.scan') or False

    @api.depends('scan_code')
    def _compute_qr_image(self):
        for wc in self:
            wc.qr_image = wc._qr_png(wc.scan_code) if wc.scan_code else False

    @api.model
    def _qr_png(self, value, box_size=6):
        """A QR as PNG bytes, base64.

        Drawn with `qrcode` rather than Odoo's `/report/barcode` route: that path goes
        through reportlab's renderPM, which cannot produce a raster on this server
        (`rlPyCairo` is missing and needs system cairo headers to build). The native
        route fails at PRINT time with a broken image, so the label would look fine on
        screen and come out of the printer blank.
        """
        if not qrcode:
            raise UserError(_(
                "The 'qrcode' Python package is not installed, so station labels "
                "cannot be produced. Install it with: pip install qrcode"))
        img = qrcode.make(value, box_size=box_size, border=2)
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return base64.b64encode(buf.getvalue())

    open_workorder_count = fields.Integer(compute='_compute_open_count',
                                          string='Jobs Here')

    def _compute_open_count(self):
        counts = dict(self.env['mrp.workorder']._read_group(
            [('workcenter_id', 'in', self.ids),
             ('state', 'not in', ('done', 'cancel'))],
            ['workcenter_id'], ['__count']))
        for wc in self:
            wc.open_workorder_count = counts.get(wc, 0)

    def action_view_open_workorders(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Jobs at %s', self.display_name),
            'res_model': 'mrp.workorder', 'view_mode': 'list,form',
            'domain': [('workcenter_id', '=', self.id),
                       ('state', 'not in', ('done', 'cancel'))],
        }

    def action_assign_scan_codes(self):
        """Give codes to work centres that predate this module."""
        without = self.filtered(lambda w: not w.scan_code)
        for wc in without:
            wc.scan_code = wc._next_scan_code()
        return len(without)

    def action_print_labels(self):
        self.action_assign_scan_codes()
        return self.env.ref(
            'lab_workcenter_scan.action_report_workcenter_label').report_action(self)
