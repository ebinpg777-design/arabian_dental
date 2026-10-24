# -*- coding: utf-8 -*-
"""The 3D printers: the machines the plates are printed on.

Each machine gets its number by itself when it is created, and a sticker with that
number for its side. A plate (a print order) is planned on a machine while it is being
assembled and enters the machine when its print starts; a machine prints one plate at
a time, so what it is doing is one of three things - printing a plate (and when that
plate is expected off), free, or down for maintenance. The machine keeps the record:
the plate running now, the plates waiting for it, every plate that came off it, the
models that printed and the models that failed on it, how long its plates take and
how close ChituBox's estimates came.
"""
import base64
import io
from datetime import datetime, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError

try:
    import qrcode
except ImportError:                                   # pragma: no cover
    qrcode = None
try:
    import barcode as pybarcode
    from barcode.writer import ImageWriter
except ImportError:                                   # pragma: no cover
    pybarcode = ImageWriter = None

TECHNOLOGIES = [('resin', 'Resin (LCD / DLP / SLA)'), ('fdm', 'Filament (FDM)'), ('other', 'Other')]
STATES = [('ready', 'Ready'), ('maintenance', 'Under Maintenance'), ('retired', 'Retired')]
RECENT_DAYS = 30
# a plate with no sliced-file estimate still running this many times the machine's
# typical print is running long (print_order.py imports this)
RUNNING_LONG_FACTOR = 2.0
# the plate states that mean "came off the machine": the machine's record is made of them
CLOSED_STATES = ('washing', 'done', 'failed')
# of those, the ones with usable models
PRINTED_STATES = ('washing', 'done')
EPOCH = datetime(1970, 1, 1)


class Printer(models.Model):
    _name = 'lab.scan.printer'
    _description = '3D Printer'
    _inherit = ['image.mixin', 'mail.thread']
    _order = 'sequence, code, id'
    _rec_names_search = ['name', 'code']

    name = fields.Char('Machine Name', required=True, tracking=True)
    code = fields.Char('Number', readonly=True, copy=False, index=True, default='New',
                       help="Given in order when the machine is created, and printed on its sticker.")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    # Every machine its own colour, so its tag is told apart at a glance on the
    # plates. Picked for a new machine from the colours least in use. (client,
    # 2026-10-14: "different colours for different machines for the machine tag")
    color = fields.Integer('Colour', default=0,
                           help="The colour of the machine's tag on the print orders.")
    company_id = fields.Many2one('res.company', string='Company', required=True, index=True,
                                 default=lambda s: s.env.company)
    state = fields.Selection(STATES, string='Status', default='ready', required=True, tracking=True,
                             help="Only a machine that is Ready takes a plate.")
    technology = fields.Selection(TECHNOLOGIES, string='Technology', default='resin', required=True)
    brand = fields.Char('Make & Model', help="As on the machine: Phrozen Sonic Mini 8K, Elegoo Mars 4…")
    serial_no = fields.Char('Serial Number')
    location = fields.Char('Location', help="Where it stands - the room, the bench.")
    user_id = fields.Many2one('res.users', string='In Charge', tracking=True,
                              help="Gets the activity when a plate on this machine is past its expected off time.")
    date_purchased = fields.Date('Purchased On')
    typical_hours = fields.Float('Typical Print (hours)', default=3.0,
                                 help="How long a plate usually takes on this machine. The clock of a plate "
                                      "started without a sliced file uses it, and such a plate still running "
                                      "at twice it is flagged as running long.")
    plate_capacity = fields.Integer('Models per Plate', default=8,
                                    help="How many arches fit on one plate. The New Print Order dialog shows "
                                         "'9 of 8 models' past it - a hint, never a block.")
    # what ChituBox writes into the sliced file, so a file sliced for another machine
    # can be noticed before the print starts (a warning, never a block - §2.1 _compute_profile)
    profile_name = fields.Char('ChituBox Profile',
                               help="The machine name exactly as ChituBox writes it into the file, "
                                    "e.g. Phrozen Sonic Mini 8K. Blank = no check.")
    bed_x_mm = fields.Float('Bed X (mm)', digits=(6, 2))
    bed_y_mm = fields.Float('Bed Y (mm)', digits=(6, 2))
    resolution_x = fields.Integer('Resolution X (px)')
    resolution_y = fields.Integer('Resolution Y (px)')
    note = fields.Html('Notes')

    # ------------------------------------------------------------- the record
    order_ids = fields.One2many('lab.scan.print.order', 'printer_id', string='Print Orders')
    running_order_ids = fields.One2many('lab.scan.print.order', 'printer_id', string='Plate on the Machine',
                                        domain=[('state', '=', 'printing')])
    queued_order_ids = fields.One2many('lab.scan.print.order', 'printer_id', string='Up Next',
                                       domain=[('state', '=', 'draft')])
    washing_order_ids = fields.One2many('lab.scan.print.order', 'printer_id', string='In Wash & Cure',
                                        domain=[('state', '=', 'washing')])
    # what Start Next Plate starts: a plate planned on this machine, else the first
    # ready plate nobody planned - plates are made without a machine now, and a free
    # machine must still be able to take one (review, 2026-10-14)
    next_order_id = fields.Many2one('lab.scan.print.order', string='Next Plate', compute='_compute_next_order')
    running_order_id = fields.Many2one('lab.scan.print.order', string='Plate Printing Now',
                                       compute='_compute_stats', search='_search_running_order_id',
                                       help="The one plate on the machine. The database allows one per "
                                            "machine at a time.")
    running_count = fields.Integer('Printing Now', compute='_compute_stats', search='_search_running_count',
                                   help="1 while a plate is on the machine, else 0.")
    is_busy = fields.Boolean('Busy', compute='_compute_stats')
    expected_free_at = fields.Datetime('Free At', compute='_compute_stats',
                                       help="When the running plate is expected off; blank when free.")
    status_text = fields.Char('Status Line', compute='_compute_stats',
                              help="'Printing PRINT/2026/0012 · off 06:40', 'Free' or 'Free · 2 queued' - "
                                   "the one line the machine card and the dialogs show.")
    queued_count = fields.Integer('Queued', compute='_compute_stats',
                                  help="Plates planned on this machine and still being plated.")
    print_count = fields.Integer('Plates', compute='_compute_stats',
                                 help="Every plate that came off this machine, printed or failed.")
    done_count = fields.Integer('Plates Printed', compute='_compute_stats',
                                help="Plates that came off with at least one good model.")
    failed_count = fields.Integer('Plates Failed', compute='_compute_stats',
                                  help="Plates aborted mid-print or with nothing usable on them.")
    plate_failure_rate = fields.Float('Plate Failure Rate (%)', compute='_compute_stats', digits=(5, 1))
    model_done_count = fields.Integer('Models Printed', compute='_compute_stats',
                                      help="Patients' models that came off this machine clean.")
    model_failed_count = fields.Integer('Models Failed', compute='_compute_stats',
                                        help="Patients' models that failed on this machine, whole plate or not.")
    failure_rate = fields.Float('Failure Rate (%)', compute='_compute_stats', digits=(5, 1),
                                help="Share of models that failed: failed / (printed + failed). A plate "
                                     "that loses two of eight counts as two failures, not one.")
    done_recent = fields.Integer('Plates Printed, Last 30 Days', compute='_compute_stats')
    failed_recent = fields.Integer('Plates Failed, Last 30 Days', compute='_compute_stats')
    hours_recent = fields.Float('Printing Hours, Last 30 Days', compute='_compute_stats', digits=(8, 1))
    resin_ml_recent = fields.Float('Resin, Last 30 Days (mL)', compute='_compute_stats', digits=(8, 1),
                                   help="Sum of the plates' resin figures - from the sliced files, or typed.")
    estimate_accuracy = fields.Float('Actual vs Estimate (%)', compute='_compute_stats', digits=(5, 0),
                                     help="Average of actual print time over ChituBox's estimate, for plates "
                                          "that had a sliced file read. 100 = spot on; 115 = runs 15% long.")
    last_print_date = fields.Datetime('Last Plate Off', compute='_compute_stats')

    # ------------------------------------------------------------- the sticker
    sticker_qr = fields.Binary('QR Code', compute='_compute_sticker', attachment=False)
    sticker_barcode = fields.Binary('Barcode', compute='_compute_sticker', attachment=False)

    _code_company_uniq = models.Constraint('unique (code, company_id)',
                                           "Two machines cannot carry the same number.")

    # ------------------------------------------------------------------ computes
    @api.depends('order_ids.state', 'order_ids.date_start', 'order_ids.date_end', 'order_ids.duration_hours',
                 'order_ids.resin_ml', 'order_ids.estimated_hours', 'order_ids.expected_end',
                 'order_ids.slice_parsed', 'order_ids.line_ids.result', 'order_ids.line_ids.printed_models',
                 'order_ids.line_ids.failed_models', 'typical_hours')
    def _compute_stats(self):
        """One pass over the plates and one over the models, both grouped by machine;
        a search for the running plates (one per machine) because the card needs the
        record itself, not a count."""
        Order = self.env['lab.scan.print.order']
        Line = self.env['lab.scan.print.order.line']
        since = fields.Datetime.now() - timedelta(days=RECENT_DAYS)
        mine = [('printer_id', 'in', self.ids)]
        counts = {(printer.id, state): n for printer, state, n in Order._read_group(
            mine, ['printer_id', 'state'], ['__count'])}
        recent = {(printer.id, state): (n, hours or 0.0, ml or 0.0) for printer, state, n, hours, ml
                  in Order._read_group(mine + [('state', 'in', CLOSED_STATES), ('date_start', '>=', since)],
                                       ['printer_id', 'state'], ['__count', 'duration_hours:sum', 'resin_ml:sum'])}
        last = {printer.id: when for printer, when in Order._read_group(
            mine + [('state', 'in', CLOSED_STATES)], ['printer_id'], ['date_end:max'])}
        # models are arches: a patient whose lower failed printed one and lost one
        models_ = {printer.id: (ok or 0, lost or 0) for printer, ok, lost in Line._read_group(
            mine + ['|', ('printed_models', '>', 0), ('failed_models', '>', 0)], ['printer_id'],
            ['printed_models:sum', 'failed_models:sum'])}
        running = {order.printer_id.id: order for order in Order.search(mine + [('state', '=', 'printing')])}
        # actual over estimate, plate by plate: a ratio of sums would let one long plate
        # hide ten short ones
        ratios = {}
        for row in Order.search_read(
                mine + [('state', 'in', CLOSED_STATES), ('slice_parsed', '=', True),
                        ('estimated_hours', '>', 0), ('duration_hours', '>', 0)],
                ['printer_id', 'duration_hours', 'estimated_hours']):
            ratios.setdefault(row['printer_id'][0], []).append(row['duration_hours'] / row['estimated_hours'])
        for printer in self:
            order = running.get(printer.id)
            printer.running_order_id = order
            printer.running_count = 1 if order else 0
            printer.is_busy = bool(order)
            printer.expected_free_at = order.expected_end if order else False
            printer.queued_count = counts.get((printer.id, 'draft'), 0)
            done = counts.get((printer.id, 'washing'), 0) + counts.get((printer.id, 'done'), 0)
            failed = counts.get((printer.id, 'failed'), 0)
            printer.done_count = done
            printer.failed_count = failed
            printer.print_count = done + failed
            printer.plate_failure_rate = round(failed * 100.0 / (done + failed), 1) if done + failed else 0.0
            ok, lost = models_.get(printer.id, (0, 0))
            printer.model_done_count = ok
            printer.model_failed_count = lost
            printer.failure_rate = round(lost * 100.0 / (ok + lost), 1) if ok + lost else 0.0
            rows = [recent.get((printer.id, state), (0, 0.0, 0.0)) for state in CLOSED_STATES]
            printer.done_recent = sum(n for n, _h, _ml in rows[:2])
            printer.failed_recent = rows[2][0]
            printer.hours_recent = round(sum(h for _n, h, _ml in rows), 1)
            printer.resin_ml_recent = round(sum(ml for _n, _h, ml in rows), 1)
            own = ratios.get(printer.id)
            printer.estimate_accuracy = round(sum(own) * 100.0 / len(own)) if own else 0.0
            printer.last_print_date = last.get(printer.id)
            if order:
                printer.status_text = _("Printing %(plate)s · off %(at)s", plate=order.name,
                                        at=self._clock_text(order.expected_end))
            elif printer.queued_count:
                printer.status_text = _("Free · %d queued", printer.queued_count)
            else:
                printer.status_text = _("Free")

    def _search_running_count(self, operator, value):
        if operator not in ('=', '!=', '>', '>=', '<', '<='):
            raise NotImplementedError()
        busy = self.env['lab.scan.print.order']._read_group(
            [('state', '=', 'printing')], ['printer_id'], ['__count'])
        matching = [printer.id for printer, n in busy if self._compare(n, operator, value)]
        if self._compare(0, operator, value):
            # machines with no plate running are not in the group at all
            idle = self.with_context(active_test=False).search([('id', 'not in', [p.id for p, _n in busy])])
            matching += idle.ids
        return [('id', 'in', matching)]

    def _search_running_order_id(self, operator, value):
        Order = self.env['lab.scan.print.order']
        if operator in ('=', '!=') and not value:
            busy = Order.search([('state', '=', 'printing')]).printer_id.ids
            return [('id', 'not in' if operator == '=' else 'in', busy)]
        if operator not in ('=', '!=', 'in', 'not in'):
            raise NotImplementedError()
        positive = '=' if operator in ('=', '!=') else 'in'
        matched = Order.search([('state', '=', 'printing'), ('id', positive, value)]).printer_id.ids
        return [('id', 'in' if operator in ('=', 'in') else 'not in', matched)]

    @staticmethod
    def _compare(n, operator, value):
        return {'=': n == value, '!=': n != value, '>': n > value, '>=': n >= value,
                '<': n < value, '<=': n <= value}[operator]

    @api.model
    def _clock_text(self, when):
        """'06:40' on the lab's clock, for a UTC datetime: the off-time people say
        to each other, not a UTC stamp that is 5½ hours out."""
        if not when:
            return _("unknown")
        tz = self.env['lab.scan']._lab_tz()
        return pytz.utc.localize(when.replace(tzinfo=None)).astimezone(tz).strftime('%H:%M')

    @api.depends('code')
    def _compute_sticker(self):
        for printer in self:
            code = printer.code if printer.code and printer.code != 'New' else False
            printer.sticker_qr = self._qr_png(code) if code and qrcode else False
            printer.sticker_barcode = self._code128_png(code) if code and pybarcode else False

    @staticmethod
    def _qr_png(value):
        """Drawn here rather than through /report/barcode: that route depends on a
        raster backend that is not on every server, and a sticker that looks right on
        screen and prints blank is worse than none."""
        img = qrcode.make(value, box_size=6, border=1)
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        # base64 text: Odoo 20's Binary fields refuse plain bytes
        return base64.b64encode(buf.getvalue()).decode()

    @staticmethod
    def _code128_png(value):
        buf = io.BytesIO()
        pybarcode.get('code128', value, writer=ImageWriter()).write(
            buf, options={'write_text': False, 'module_height': 7.0, 'quiet_zone': 1.0, 'dpi': 300})
        return base64.b64encode(buf.getvalue()).decode()

    @api.depends('name', 'code')
    def _compute_display_name(self):
        for printer in self:
            code = printer.code if printer.code and printer.code != 'New' else ''
            printer.display_name = '%s · %s' % (code, printer.name) if code else printer.name

    # ------------------------------------------------------------------ crud
    @api.model_create_multi
    def create(self, vals_list):
        taken = []
        for vals in vals_list:
            if vals.get('code', 'New') == 'New':
                vals['code'] = self.env['ir.sequence'].next_by_code('lab.scan.printer') or 'New'
            if not vals.get('color'):
                vals['color'] = self._next_color(taken)
                taken.append(vals['color'])
        return super().create(vals_list)

    @api.model
    def _next_color(self, taken=()):
        """The tag colour least in use among the machines (1-11; 0 is no colour)."""
        used = [c for c in self.with_context(active_test=False).search([]).mapped('color') if c] + list(taken)
        return min(range(1, 12), key=lambda c: (used.count(c), c))

    @api.model
    def _by_code(self, text, company=None):
        """The machine a scanned sticker or a typed number names: its number first,
        exactly; failing that, a name that matches exactly one machine."""
        text = (text or '').strip()
        if not text:
            return self.browse()
        domain = [('company_id', '=', (company or self.env.company).id)]
        found = self.search(domain + [('code', '=ilike', text)], limit=1)
        if not found:
            found = self.search(domain + [('name', 'ilike', text)], limit=2)
            if len(found) != 1:
                return self.browse()
        return found

    @api.model
    def _suggest(self, company=None):
        """The machine to offer first: ready and free; among the busy ones the one
        free soonest; then the one that loses the fewest models - the free, reliable one."""
        ready = self.search([('state', '=', 'ready'), ('company_id', '=', (company or self.env.company).id)])
        if not ready:
            return self.browse()
        return min(ready, key=lambda p: (p.is_busy, p.expected_free_at or EPOCH, p.failure_rate, p.sequence, p.id))

    def _ensure_ready(self):
        self.ensure_one()
        if self.state != 'ready':
            raise UserError(_("%(machine)s is %(state)s and cannot take a plate — pick another machine.",
                              machine=self.display_name, state=dict(STATES)[self.state].lower()))

    def _ensure_free(self):
        """A machine prints one plate at a time; the database index says the same,
        but this names the plate in the way and when it is expected off."""
        self.ensure_one()
        order = self.running_order_id
        if order:
            raise UserError(_("%(machine)s is printing %(plate)s, expected off at %(at)s — take that plate "
                              "off first or pick another machine.",
                              machine=self.display_name, plate=order.name, at=self._clock_text(order.expected_end)))

    # ------------------------------------------------------------------ actions
    def action_print_sticker(self):
        # config=False: the sticker draws its own layout (see action_print_ticket)
        return self.env.ref('lab_scan_register.action_report_printer_sticker').report_action(self, config=False)

    def action_set_maintenance(self):
        self.write({'state': 'maintenance'})
        for printer in self.filtered('running_order_id'):
            # the plate on it goes on printing - maintenance only stops the next one starting
            printer.message_post(body=_("Taken down for maintenance with %s running on it.",
                                        printer.running_order_id.name))
        return True

    def action_set_ready(self):
        return self.write({'state': 'ready'})

    def _compute_next_order(self):
        Order = self.env['lab.scan.print.order']
        unplanned = {}
        for printer in self:
            order = printer.queued_order_ids.sorted(lambda o: (o.sequence, o.id))[:1]
            if not order:
                company = printer.company_id.id
                if company not in unplanned:
                    unplanned[company] = Order.search([
                        ('state', '=', 'draft'), ('printer_id', '=', False), ('company_id', '=', company),
                        ('is_ready', '=', True)], order='priority desc, sequence, id', limit=1)
                order = unplanned[company]
            printer.next_order_id = order

    def action_start_next(self):
        """Start Next Plate: the first plate planned on this machine - else the first
        ready plate with no machine yet, urgent first - goes into the Start dialog,
        sticker box focused."""
        self.ensure_one()
        order = self.next_order_id
        if not order:
            raise UserError(_("No plate is waiting for %s — plate one from Waiting for a Plate first.",
                              self.display_name))
        return {'type': 'ir.actions.act_window', 'name': _('Start Print'),
                'res_model': 'lab.scan.print.order.start', 'view_mode': 'form', 'target': 'new',
                'context': {'default_order_id': order.id, 'default_printer_id': self.id}}

    def _running_or_raise(self):
        self.ensure_one()
        if not self.running_order_id:
            raise UserError(_("Nothing is printing on %s.", self.display_name))
        return self.running_order_id

    def action_plate_off(self):
        """The plate on this machine came off: the running order's Plate Off dialog."""
        return self._running_or_raise().action_plate_off()

    def action_model_failed(self):
        """Some models came off the running plate; the rest go on printing."""
        return self._running_or_raise().action_model_failed()

    def action_plate_failed(self):
        return self._running_or_raise().action_print_failed()

    def action_view_running(self):
        order = self._running_or_raise()
        return {'type': 'ir.actions.act_window', 'name': order.name,
                'res_model': 'lab.scan.print.order', 'res_id': order.id, 'view_mode': 'form'}

    def action_view_queue(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': _('Up next — %s', self.display_name),
                'res_model': 'lab.scan.print.order', 'view_mode': 'list,kanban,form',
                'domain': [('printer_id', '=', self.id), ('state', '=', 'draft')],
                'context': {'create': False}}

    def action_view_orders(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': _('Plates — %s', self.display_name),
                'res_model': 'lab.scan.print.order', 'view_mode': 'list,kanban,pivot,graph,form',
                'domain': [('printer_id', '=', self.id)],
                'context': {'create': False, 'search_default_group_month': 1}}

    def action_view_failed(self):
        """The models that failed on this machine - lines, not plates: a plate that
        lost two of eight is two rows here, each with its own reason."""
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': _('Failed models — %s', self.display_name),
                'res_model': 'lab.scan.print.order.line', 'view_mode': 'list,pivot,graph',
                'domain': [('printer_id', '=', self.id), ('result', '=', 'failed')],
                'context': {'create': False, 'search_default_group_reason': 1}}
