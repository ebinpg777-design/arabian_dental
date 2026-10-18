# -*- coding: utf-8 -*-
"""The print order: one build plate, several patients, one machine, printed once.

The printing room does not print registers; it prints plates. The operator
gathers the waiting patients' arches onto one plate in ChituBox, slices it to a
.ctb, loads one machine and takes the plate off once - with every model good, a
few lost, or the whole plate failed. The print order is that plate:

    Plating -> Printing -> Wash & Cure -> Printed        (or Failed, or Cancelled)

A line is one patient on the plate and carries what became of their models, so
a plate that loses two of eight models is exact: those two go onto a new plate
(a re-plate, attempt 2) while the other six are washed and cured. Every machine
figure - plates, models, failures, hours, resin - is read off print orders and
their lines; nothing is counted twice. The register's own status from Sent for
Printing on is rolled up from where its patients' models are
(Scan._sync_print_state), never set by hand.
"""
import base64
import io
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timedelta

import pytz

from markupsafe import Markup

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.fields import Domain

from . import slice_file
from .printer import RUNNING_LONG_FACTOR
from .scan_line import PRIORITIES

STATES = [
    ('draft', 'Plating'),
    ('printing', 'Printing'),
    ('washing', 'Wash & Cure'),
    ('done', 'Printed'),
    ('failed', 'Failed'),
    ('cancel', 'Cancelled'),
]
CLOSED = ('washing', 'done', 'failed')
FAILURE_REASONS = [
    ('adhesion', 'Model came off the plate'),
    ('supports', 'Supports failed'),
    ('material', 'Ran out of resin / material'),
    ('power', 'Power cut'),
    ('layers', 'Layer shift or artefacts'),
    ('file', 'File or slicing error'),
    ('machine', 'Machine fault'),
    ('other', 'Other'),
]
RESULTS = [
    ('pending', 'On the Plate'),
    ('ok', 'Printed'),
    ('failed', 'Failed'),
    ('withdrawn', 'Taken Off'),
]
PARTS = [('both', 'Upper & Lower'), ('upper', 'Upper'), ('lower', 'Lower')]
FAILED_PARTS = [('all', 'All'), ('upper', 'Upper'), ('lower', 'Lower')]
# a print still running past the file's estimate by this much is running long
RUNNING_LONG_GRACE = 1.25
DUE_OFF_GRACE_MINUTES = 30
WASH_LONG_HOURS = 2
WAITING_LONG_HOURS = 24
SLICED_NOT_STARTED_HOURS = 4
LAB_HOURS = (8, 20)
ZIP_CHUNK = 1 << 20
# the register states from which patients can be put on a plate
PLATEABLE = ('confirmed', 'printing', 'in_progress', 'printed')
# What the sliced file fills in, and what clearing it clears.
SLICE_DEFAULTS = {
    'slice_filename': False, 'slice_size': 0, 'slice_format': False, 'slice_version': 0,
    'slice_parsed': False, 'slice_error': False, 'slice_truncated': False,
    'layer_count': 0, 'layer_height_mm': 0.0, 'total_height_mm': 0.0, 'exposure_s': 0.0,
    'bottom_exposure_s': 0.0, 'bottom_layers': 0, 'resolution_x': 0, 'resolution_y': 0,
    'bed_x_mm': 0.0, 'bed_y_mm': 0.0, 'bed_z_mm': 0.0, 'resin_g': 0.0,
    'machine_name': False, 'software_version': False, 'preview_image': False, 'preview_small': False,
}
# What is settled once the plate leaves Plating. The sliced file is not in it: it can
# be replaced while the plate prints (a re-slice), and settles once the plate is off.
PLATING_FIELDS = frozenset({'printer_id', 'line_ids', 'estimated_hours', 'resin_ml'})
SLICE_FIELDS = frozenset({'slice_attachment_id', 'slice_filename'})
# How much of the sliced file the figures live in: header, parameters, slicer info,
# the two previews - all inside the first megabyte of a ChituBox file.
HEAD_BYTES = 1024 * 1024


def _hours_text(hours, env):
    """2h 05m. `env` translates it: a helper's own frame has no record for _() to read
    the language from (Odoo 20 logs a warning per call)."""
    hours = hours or 0.0
    return env._("%(h)dh %(m)02dm", h=int(hours), m=int(round((hours - int(hours)) * 60)))


class PrintOrder(models.Model):
    _name = 'lab.scan.print.order'
    _description = 'Print Order'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'priority desc, sequence, date_start desc, id desc'
    _rec_names_search = ['name']

    name = fields.Char('Print Order', default='New', readonly=True, copy=False, index=True)
    company_id = fields.Many2one('res.company', string='Company', required=True, index=True,
                                 default=lambda s: s.env.company)
    state = fields.Selection(STATES, string='Status', default='draft', required=True, tracking=True,
                             copy=False, index=True)
    sequence = fields.Integer(default=10, help="The order of the plates waiting for a machine.")
    priority = fields.Selection(PRIORITIES, string='Priority', compute='_compute_lines', store=True)
    date_delivery = fields.Date('Earliest Delivery', compute='_compute_lines', store=True)
    # Picked at Start Print, where the operator scans the machine's sticker; while
    # plating it is at most a plan. (client, 2026-10-14: "machine only to be set
    # before print in progress - no need in the wizard of creating a print order")
    printer_id = fields.Many2one('lab.scan.printer', string='Machine', ondelete='restrict',
                                 tracking=True, index=True, domain=[('state', '=', 'ready')],
                                 help="The machine the plate runs on, picked at Start Print; while plating, a plan.")
    printer_tag_ids = fields.Many2many('lab.scan.printer', string='Machine Tag', compute='_compute_printer_tag',
                                       help="The machine as a coloured tag: every machine has its own colour.")

    # ------------------------------------------------------------- the patients
    line_ids = fields.One2many('lab.scan.print.order.line', 'order_id', string='Patients', copy=False)
    scan_ids = fields.Many2many('lab.scan', 'lab_scan_print_order_scan_rel', 'order_id', 'scan_id',
                                string='Registers', compute='_compute_lines', store=True)
    register_count = fields.Integer('Register Count', compute='_compute_lines', store=True)
    patient_count = fields.Integer('Patient Count', compute='_compute_lines', store=True)
    model_count = fields.Integer('Models', compute='_compute_lines', store=True, help="Arches on the plate.")
    capacity = fields.Integer('Plate Holds', related='printer_id.plate_capacity')
    over_capacity = fields.Boolean('Over Capacity', compute='_compute_over_capacity')
    printed_count = fields.Integer('Printed Models', compute='_compute_lines', store=True)
    failed_count = fields.Integer('Failed Models', compute='_compute_lines', store=True)
    withdrawn_count = fields.Integer('Taken Off', compute='_compute_lines', store=True)
    is_partial = fields.Boolean('Partly Failed', compute='_compute_lines', store=True)
    is_ready = fields.Boolean('Ready to Print', compute='_compute_is_ready', search='_search_is_ready',
                              help="Plating on a machine in service, every patient with files (or a link), "
                                   "and a print time - from the sliced file or typed.")
    # the plate's files: each patient's own, the register's shared ones, and the link
    # they came by - managed here, where they are downloaded and sliced
    file_line_ids = fields.One2many('lab.scan.print.order.line', 'order_id', string="Patient's Files")
    files_missing_count = fields.Integer('Patients Without Files', compute='_compute_files_missing', store=True,
                                         help="Patients on the plate with no file and no download link: "
                                              "nothing to slice yet.")
    checklist_html = fields.Html('Before It Starts', compute='_compute_checklist', sanitize=False)

    # ------------------------------------------------------------- who and when
    user_id = fields.Many2one('res.users', string='Plated By', default=lambda s: s.env.user, readonly=True)
    start_user_id = fields.Many2one('res.users', string='Started By', readonly=True, copy=False)
    off_user_id = fields.Many2one('res.users', string='Taken Off By', readonly=True, copy=False)
    cure_user_id = fields.Many2one('res.users', string='Cured By', readonly=True, copy=False)
    date_start = fields.Datetime('Started On', readonly=True, copy=False, index=True)
    date_end = fields.Datetime('Plate Off', readonly=True, copy=False)
    date_cured = fields.Datetime('Cured On', readonly=True, copy=False)
    duration_hours = fields.Float('Print Time (hours)', compute='_compute_times', store=True, digits=(8, 2))
    wash_hours = fields.Float('Wash & Cure (hours)', compute='_compute_times', store=True, digits=(8, 2))
    elapsed_hours = fields.Float('Running (hours)', compute='_compute_live', digits=(8, 2))
    estimated_hours = fields.Float('Estimated Time', digits=(8, 2),
                                   help="ChituBox's own estimate from the sliced file, or typed.")
    expected_end = fields.Datetime('Expected Off', compute='_compute_expected_end', store=True,
                                   help="Start plus the estimate; failing an estimate, the machine's typical print.")
    progress_pct = fields.Integer('Progress', compute='_compute_live')
    running_long = fields.Boolean('Running Long', compute='_compute_live', search='_search_running_long',
                                  help="Still printing well past its estimate.")
    due_off = fields.Boolean('Due Off', compute='_compute_live', search='_search_due_off')
    due_off_warned = fields.Boolean(copy=False)
    resin_batch = fields.Char('Resin Batch', help="The bottle or lot in the vat.")
    note = fields.Char('Plate Note')

    # ------------------------------------------------------------- the sliced file
    # The ChituBox file itself, kept on the plate so the room can download it onto
    # the stick again or replace it; its figures are read from its first megabyte
    # on the server. (client, 2026-10-14: "sliced file can be uploaded in the wizard,
    # downloaded or edited from the print order") A plain attachment, filled by the
    # slice_upload widget in pieces: a .ctb is often over the 128 MiB an Odoo request
    # may carry, and a binary field sends the whole file in one.
    slice_attachment_id = fields.Many2one('ir.attachment', string='Sliced File', copy=False, ondelete='set null',
                                          index='btree_not_null',
                                          help="The ChituBox file for this plate. Its time, resin and layers are read "
                                               "from it; Download File gives it back for the machine's stick.")
    slice_filename = fields.Char('Sliced File Name', copy=True)
    slice_size = fields.Integer('File Size (bytes)')
    slice_size_text = fields.Char('File Size', compute='_compute_slice_size_text')
    date_sliced = fields.Datetime('File Read On', readonly=True)
    slice_format = fields.Char('Format', readonly=True)
    slice_version = fields.Integer('Format Version', readonly=True)
    slice_parsed = fields.Boolean('Figures From the File', readonly=True)
    slice_error = fields.Char('File Not Read', readonly=True)
    slice_truncated = fields.Boolean(readonly=True)
    layer_count = fields.Integer('Layers', readonly=True)
    layer_height_mm = fields.Float('Layer Height (mm)', readonly=True, digits=(6, 3))
    total_height_mm = fields.Float('Height (mm)', readonly=True)
    exposure_s = fields.Float('Exposure (s)', readonly=True)
    bottom_exposure_s = fields.Float('Bottom Exposure (s)', readonly=True)
    bottom_layers = fields.Integer('Bottom Layers', readonly=True)
    resolution_x = fields.Integer('Resolution X', readonly=True)
    resolution_y = fields.Integer('Resolution Y', readonly=True)
    bed_x_mm = fields.Float('Bed X (mm)', readonly=True)
    bed_y_mm = fields.Float('Bed Y (mm)', readonly=True)
    bed_z_mm = fields.Float('Bed Z (mm)', readonly=True)
    resin_ml = fields.Float('Resin (mL)', digits=(8, 1), help="From the file, or typed.")
    resin_g = fields.Float('Resin (g)', readonly=True, digits=(8, 1))
    machine_name = fields.Char('Sliced For', readonly=True, help="The printer profile ChituBox wrote into the file.")
    software_version = fields.Char('ChituBox Version', readonly=True)
    preview_image = fields.Binary('Plate Preview', readonly=True, attachment=True)
    preview_small = fields.Binary('Plate Thumbnail', readonly=True, attachment=True)
    profile_mismatch = fields.Boolean(compute='_compute_profile')
    profile_warning = fields.Char(compute='_compute_profile')
    suggested_filename = fields.Char('Save the Slice As', compute='_compute_suggested_filename')

    # ------------------------------------------------------------- failure, restarts
    failure_reason = fields.Selection(FAILURE_REASONS, string='What Went Wrong', readonly=True, copy=False)
    failure_note = fields.Text('Details', readonly=True, copy=False)
    restarted_from_id = fields.Many2one('lab.scan.print.order', string='Restarts', readonly=True, copy=False,
                                        index='btree_not_null')
    restart_id = fields.Many2one('lab.scan.print.order', string='Restarted As', compute='_compute_restart')
    replate_of_ids = fields.Many2many('lab.scan.print.order', string='Re-plates From', compute='_compute_restart')
    ticket_qr = fields.Binary('QR', compute='_compute_ticket_qr', attachment=False)

    _one_plate_per_machine = models.UniqueIndex("(printer_id) WHERE state = 'printing'",
                                                "A machine prints one plate at a time.")

    # ================================================================== computes
    @api.depends('line_ids.result', 'line_ids.model_count', 'line_ids.scan_id', 'line_ids.priority',
                 'line_ids.date_delivery')
    def _compute_lines(self):
        for order in self:
            live = order.line_ids.filtered(lambda l: l.result != 'withdrawn')
            order.scan_ids = live.scan_id
            order.register_count = len(live.scan_id)
            order.patient_count = len(live)
            order.model_count = sum(live.mapped('model_count'))
            order.printed_count = len(live.filtered(lambda l: l.result == 'ok'))
            order.failed_count = len(live.filtered(lambda l: l.result == 'failed'))
            order.withdrawn_count = len(order.line_ids) - len(live)
            order.is_partial = bool(order.printed_count and order.failed_count)
            order.priority = '1' if '1' in live.mapped('priority') else '0'
            dates = [d for d in live.mapped('date_delivery') if d]
            order.date_delivery = min(dates) if dates else False

    @api.depends('model_count', 'printer_id.plate_capacity')
    def _compute_over_capacity(self):
        for order in self:
            order.over_capacity = bool(order.capacity and order.model_count > order.capacity)

    @api.depends('printer_id')
    def _compute_printer_tag(self):
        for order in self:
            order.printer_tag_ids = order.printer_id

    @api.depends('state', 'printer_id.state', 'slice_parsed', 'estimated_hours', 'files_missing_count')
    def _compute_is_ready(self):
        for order in self:
            order.is_ready = bool(order.state == 'draft'
                                  and (not order.printer_id or order.printer_id.state == 'ready')
                                  and not order.files_missing_count
                                  and (order.slice_parsed or order.estimated_hours))

    def _search_is_ready(self, operator, value):
        if operator not in ('=', '!='):
            raise NotImplementedError()
        ready = Domain([('state', '=', 'draft'), '|', ('printer_id', '=', False), ('printer_id.state', '=', 'ready'),
                        ('files_missing_count', '=', 0),
                        '|', ('slice_parsed', '=', True), ('estimated_hours', '>', 0)])
        return ready if (operator == '=') == bool(value) else ~ready

    @api.depends('line_ids.has_files', 'line_ids.result')
    def _compute_files_missing(self):
        for order in self:
            order.files_missing_count = len(order.line_ids.filtered(
                lambda l: l.result == 'pending' and not l.has_files))

    @api.depends('state', 'printer_id', 'printer_id.state', 'line_ids.has_files', 'line_ids.file_count',
                 'line_ids.result', 'slice_parsed', 'slice_filename', 'estimated_hours', 'model_count',
                 'profile_warning')
    def _compute_checklist(self):
        for order in self:
            order.checklist_html = order._checklist_markup(order._checklist_items()) \
                if order.state == 'draft' else False

    def _checklist_items(self, printer=None, at_start=False):
        """What stands between this plate and the machine, item by item: ('ok' |
        'warn' | 'block', text). Only a 'block' stops Start Print; a 'warn' is the
        room's call. No machine is not a block while plating - it is picked at
        Start, where it is one (`at_start`)."""
        self.ensure_one()
        printer = printer or self.printer_id
        items = []
        if not printer and at_start:
            items.append(('block', _("Pick the machine: scan its sticker.")))
        elif not printer:
            items.append(('info', _("The machine is picked at Start Print - scan its sticker there.")))
        elif printer.state != 'ready':
            items.append(('block', _("%(machine)s is %(state)s.", machine=printer.display_name,
                                     state=dict(printer._fields['state'].selection)[printer.state].lower())))
        elif printer.running_order_id and printer.running_order_id != self:
            items.append(('warn', _("%(machine)s is printing %(plate)s until about %(at)s; this plate waits.",
                                    machine=printer.display_name, plate=printer.running_order_id.name,
                                    at=self._local_time(printer.running_order_id.expected_end))))
        else:
            items.append(('ok', _("%s is free.", printer.display_name)))
        live = self.line_ids.filtered(lambda l: l.result == 'pending')
        missing = live.filtered(lambda l: not l.has_files)
        link_only = live.filtered(lambda l: l.has_files and not l.file_count)
        dying = link_only.filtered(lambda l: l.download_state in ('soon', 'expired'))
        if missing:
            items.append(('block', _("No files and no download link for %s - add them on the Files tab.",
                                     ', '.join(missing.mapped('patient_name')))))
        if dying:
            items.append(('warn', _("Only a download link for %s, and it is about to go (or gone) - download "
                                    "the files now.", ', '.join(dying.mapped('patient_name')))))
        elif link_only:
            items.append(('warn', _("Files to download from the link for %s.",
                                    ', '.join(link_only.mapped('patient_name')))))
        if live and not missing and not link_only:
            items.append(('ok', _("Files for every patient (%d).", len(live))))
        # whole seconds: a line keeps the transaction's microseconds, date_sliced does not,
        # and the patients that came WITH the file must not read as added after it
        late = live.filtered(lambda l: self.date_sliced and l.create_date
                             and l.create_date.replace(microsecond=0) > self.date_sliced)
        if self.slice_filename and late:
            items.append(('warn', _("%s went on the plate after the file was sliced - slice the plate again.",
                                    ', '.join(late.mapped('patient_name')))))
        if self.slice_parsed:
            items.append(('ok', _("ChituBox file read: about %(time)s, %(ml).0f mL of resin.",
                                  time=_hours_text(self.estimated_hours, self.env), ml=self.resin_ml)))
        elif self.estimated_hours:
            items.append(('warn', _("No ChituBox file read; the time typed is %s.",
                                    _hours_text(self.estimated_hours, self.env))))
        elif printer:
            items.append(('warn', _("No ChituBox file read and no time typed: the clock will use the "
                                    "machine's usual %s.", _hours_text(printer.typical_hours, self.env))))
        else:
            items.append(('warn', _("No ChituBox file read and no time typed: the clock will use the "
                                    "machine's usual time.")))
        capacity = printer.plate_capacity if printer else self.capacity
        if capacity and self.model_count > capacity:
            items.append(('warn', _("%(n)d models: more than the %(cap)d this machine's plate usually holds.",
                                    n=self.model_count, cap=capacity)))
        elif capacity:
            items.append(('ok', _("%(n)d of %(cap)d models on the plate.", n=self.model_count, cap=capacity)))
        if printer:
            warning = self.profile_warning if printer == self.printer_id else \
                self.new({'printer_id': printer.id}, origin=self).profile_warning
            if warning:
                items.append(('warn', warning))
        return items

    @api.model
    def _checklist_markup(self, items):
        icons = {'ok': ('fa-check-circle', 'text-success', _('Done')),
                 'warn': ('fa-exclamation-circle', 'text-warning', _('Check')),
                 'info': ('fa-info-circle', 'text-muted', _('Later')),
                 'block': ('fa-times-circle', 'text-danger', _('Needed before it starts'))}
        rows = Markup('').join(
            Markup('<li class="o_scan_check_%s"><i class="fa fa-fw %s %s me-1" title="%s"></i>%s</li>') % (
                level, icons[level][0], icons[level][1], icons[level][2], text)
            for level, text in items)
        return Markup('<ul class="list-unstyled mb-0 o_scan_checklist">%s</ul>') % rows

    @api.depends('date_start', 'date_end', 'date_cured')
    def _compute_times(self):
        for order in self:
            order.duration_hours = round((order.date_end - order.date_start).total_seconds() / 3600.0, 2) \
                if order.date_start and order.date_end else 0.0
            order.wash_hours = round((order.date_cured - order.date_end).total_seconds() / 3600.0, 2) \
                if order.date_end and order.date_cured else 0.0

    @api.depends('date_start', 'estimated_hours', 'printer_id.typical_hours')
    def _compute_expected_end(self):
        for order in self:
            hours = order.estimated_hours or order.printer_id.typical_hours
            order.expected_end = order.date_start + timedelta(hours=hours) if order.date_start and hours else False

    def _estimate(self):
        """The hours the plate should take: the file's, else the machine's usual."""
        self.ensure_one()
        return self.estimated_hours or self.printer_id.typical_hours or 0.0

    @api.depends('state', 'date_start', 'date_end', 'duration_hours', 'estimated_hours', 'expected_end',
                 'printer_id.typical_hours')
    def _compute_live(self):
        now = fields.Datetime.now()
        for order in self:
            if order.state == 'printing' and order.date_start:
                elapsed = round((now - order.date_start).total_seconds() / 3600.0, 2)
                estimate = order._estimate()
                order.elapsed_hours = elapsed
                order.progress_pct = min(100, int(elapsed * 100 / estimate)) if estimate else 0
                if order.estimated_hours:
                    order.running_long = elapsed > order.estimated_hours * RUNNING_LONG_GRACE + 0.25
                else:
                    order.running_long = bool(estimate) and elapsed > estimate * RUNNING_LONG_FACTOR
                order.due_off = bool(order.expected_end) and now > order.expected_end
            else:
                order.elapsed_hours = order.duration_hours
                order.progress_pct = 100 if order.state in CLOSED else 0
                order.running_long = False
                order.due_off = False

    def _search_running_long(self, operator, value):
        if operator not in ('=', '!='):
            raise NotImplementedError()
        ids = self.search([('state', '=', 'printing')]).filtered('running_long').ids
        wanted = (operator == '=') == bool(value)
        return [('id', 'in', ids)] if wanted else [('id', 'not in', ids)]

    def _search_due_off(self, operator, value):
        if operator not in ('=', '!='):
            raise NotImplementedError()
        due = Domain([('state', '=', 'printing'), ('expected_end', '<', fields.Datetime.now())])
        return due if (operator == '=') == bool(value) else ~due

    @api.depends('slice_size')
    def _compute_slice_size_text(self):
        human = self.env['lab.scan']._human_size
        for order in self:
            order.slice_size_text = human(order.slice_size) if order.slice_size else False

    @api.depends('machine_name', 'bed_x_mm', 'bed_y_mm', 'resolution_x', 'resolution_y',
                 'printer_id.profile_name', 'printer_id.bed_x_mm', 'printer_id.bed_y_mm',
                 'printer_id.resolution_x', 'printer_id.resolution_y')
    def _compute_profile(self):
        """A file sliced for another machine's profile - wrong bed, wrong pixels - is
        said so, never blocked: the figures are read off a reverse-engineered header
        and a blank profile on either side means nothing is known."""
        for order in self:
            printer = order.printer_id
            problems = []
            if order.machine_name and printer.profile_name:
                a, b = order.machine_name.casefold(), printer.profile_name.casefold()
                if a not in b and b not in a:
                    problems.append(_("sliced for %(file)s, %(machine)s is %(profile)s",
                                      file=order.machine_name, machine=printer.code, profile=printer.profile_name))
            if order.bed_x_mm and order.bed_y_mm and printer.bed_x_mm and printer.bed_y_mm:
                if abs(order.bed_x_mm - printer.bed_x_mm) > 0.5 or abs(order.bed_y_mm - printer.bed_y_mm) > 0.5:
                    problems.append(_("bed %(file).0f×%(filey).0f mm, the machine's is %(mx).0f×%(my).0f mm",
                                      file=order.bed_x_mm, filey=order.bed_y_mm, mx=printer.bed_x_mm, my=printer.bed_y_mm))
            if order.resolution_x and order.resolution_y and printer.resolution_x and printer.resolution_y:
                if (order.resolution_x, order.resolution_y) != (printer.resolution_x, printer.resolution_y):
                    problems.append(_("%(fx)d×%(fy)d pixels, the machine's are %(mx)d×%(my)d",
                                      fx=order.resolution_x, fy=order.resolution_y,
                                      mx=printer.resolution_x, my=printer.resolution_y))
            order.profile_mismatch = bool(problems)
            order.profile_warning = (_("The file does not match the machine: ") + '; '.join(problems) + '.') \
                if problems else False

    @api.depends('name', 'printer_id.code')
    def _compute_suggested_filename(self):
        for order in self:
            order.suggested_filename = '%s_%s.ctb' % ((order.name or 'PRINT').replace('/', '-'),
                                                       order.printer_id.code or 'machine')

    @api.depends('line_ids.replated_from_id')
    def _compute_restart(self):
        restarts = {o.restarted_from_id.id: o for o in self.search([('restarted_from_id', 'in', self.ids)],
                                                                     order='id')}
        for order in self:
            order.restart_id = restarts.get(order.id, False)
            order.replate_of_ids = order.line_ids.replated_from_id.order_id - order

    @api.depends('name')
    def _compute_ticket_qr(self):
        Printer = self.env['lab.scan.printer']
        for order in self:
            order.ticket_qr = Printer._qr_png(order.name) if order.name and order.name != 'New' \
                and self._qr_available() else False

    @api.model
    def _qr_available(self):
        from . import printer as printer_module
        return bool(printer_module.qrcode)

    # ================================================================== crud
    @api.model_create_multi
    def create(self, vals_list):
        Printer = self.env['lab.scan.printer']
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('lab.scan.print.order') or 'New'
            if not vals.get('company_id') and vals.get('printer_id'):
                vals['company_id'] = Printer.browse(vals['printer_id']).company_id.id
            if 'slice_filename' in vals:
                vals['date_sliced'] = fields.Datetime.now() if vals['slice_filename'] else False
        orders = super().create(vals_list)
        for order, vals in zip(orders, vals_list):
            if vals.get('slice_attachment_id'):
                order._take_slice()
        return orders

    def write(self, vals):
        """Status moves through the buttons; what the plate is made of settles once it
        leaves Plating - the machine, the patients, the file's figures."""
        workflow = self.env.context.get('print_workflow')
        if 'state' in vals and not workflow:
            raise UserError(_("A print order's status changes with its buttons — Start Print, Plate Off, "
                              "Print Failed, Cured, Cancel — not by editing it."))
        settled = PLATING_FIELDS.intersection(vals)
        if settled and not workflow:
            started = self.filtered(lambda o: o.state != 'draft')
            if started:
                labels = ', '.join(sorted(self._fields[f].string for f in settled))
                raise UserError(_("%(orders)s has left Plating, so it can no longer be changed (%(fields)s).",
                                  orders=', '.join(started.mapped('name')), fields=labels))
        if 'resin_batch' in vals and not workflow:
            closed = self.filtered(lambda o: o.state not in ('draft', 'printing'))
            if closed:
                raise UserError(_("%s is off the machine; its resin batch is settled.",
                                  ', '.join(closed.mapped('name'))))
        if SLICE_FIELDS.intersection(vals) and not workflow:
            closed = self.filtered(lambda o: o.state not in ('draft', 'printing'))
            if closed:
                raise UserError(_("%s is off the machine; its sliced file is part of the record.",
                                  ', '.join(closed.mapped('name'))))
        if 'slice_filename' in vals:
            vals['date_sliced'] = fields.Datetime.now() if vals['slice_filename'] else False
        before = {order.id: order.slice_attachment_id for order in self} if 'slice_attachment_id' in vals else None
        res = super().write(vals)
        if before is not None:
            for order in self:
                order._take_slice(before[order.id])
        return res

    def unlink(self):
        self.check_access('unlink')
        if any(o.state not in ('draft', 'cancel') or o.date_start for o in self):
            raise UserError(_("A print order that ran is part of the machine's record; cancel it instead."))
        return super().unlink()

    def _move(self, vals):
        return self.with_context(print_workflow=True).write(vals)

    # ================================================================== plating
    @api.model
    def _make(self, scan_lines, printer=None, resin_batch=False, note=False, existing=None):
        """A plate from these patients - a new one on `printer`, or added to a plate
        still being plated. A patient is on one open plate at most; a register still
        Confirmed goes to Sent for Printing on the way."""
        Line = self.env['lab.scan.print.order.line']
        if not scan_lines:
            raise UserError(_("Pick at least one patient to put on the plate."))
        if existing:
            existing.ensure_one()
            if existing.state != 'draft':
                raise UserError(_("%s has left Plating; patients can no longer be added to it.", existing.name))
            printer = existing.printer_id
        for line in scan_lines:
            if line.scan_id.state not in PLATEABLE:
                raise UserError(_("%(patient)s's register %(scan)s is %(state)s and cannot go for printing.",
                                  patient=line.patient_name or _('A patient'), scan=line.scan_id.name,
                                  state=dict(line.scan_id._fields['state'].selection)[line.scan_id.state].lower()))
            pending = line.print_line_ids.filtered(lambda p: p.result == 'pending')
            if pending:
                raise UserError(_("%(patient)s is already on %(order)s.",
                                  patient=line.patient_name or _('A patient'), order=pending.order_id.name))
        scan_lines.scan_id.filtered(lambda s: s.state == 'confirmed')._send_to_print()
        # a re-plate follows the line it replaces being marked failed in this same
        # transaction: that must reach the database before the new line does, or the
        # one-open-plate-per-patient index sees two
        Line.flush_model(['result'])
        order = existing or self.create({
            'printer_id': printer.id if printer else False, 'resin_batch': resin_batch or False,
            'note': note or False,
            'company_id': (scan_lines.scan_id.company_id[:1] or (printer and printer.company_id) or self.env.company).id,
        })
        for line in scan_lines:
            live = line._live_print_lines()
            latest = live[-1:]
            replated_from = latest if latest.result == 'failed' else Line
            part = line.arch or 'both'
            if replated_from.failed_part in ('upper', 'lower'):
                part = replated_from.failed_part
            Line.with_context(print_workflow=True).create({
                'order_id': order.id, 'scan_line_id': line.id, 'part': part,
                'model_count': 2 if part == 'both' else 1, 'attempt': len(live) + 1,
                'replated_from_id': replated_from.id,
            })
        order.message_post(body=_("Plated with %(n)d patient(s) from %(scans)s.", n=len(scan_lines),
                                  scans=', '.join(scan_lines.scan_id.mapped('name'))))
        others = len(order.line_ids.filtered(lambda l: l.result == 'pending')) - 1
        for scan in scan_lines.scan_id:
            names = ', '.join(n for n in scan_lines.filtered(lambda l: l.scan_id == scan).mapped('patient_name') if n)
            scan.message_post(body=_("%(patients)s put on %(order)s%(machine)s with %(n)d other patient(s).",
                                     patients=names or _('A patient'), order=order._get_html_link(),
                                     machine=' (%s)' % order.printer_id.display_name if order.printer_id else '',
                                     n=max(others, 0)))
        scan_lines.scan_id._sync_print_state()
        return order

    @api.model
    def _withdraw_lines(self, scan_lines, why):
        """These patients leave whatever plate they are on: a Plating plate loses them
        (and is cancelled once empty); a printing plate goes on for the others, the
        line marked Taken Off. History kept, nothing counted as printed or failed."""
        pending = scan_lines.print_line_ids.filtered(lambda l: l.result == 'pending')
        orders = pending.order_id
        pending.with_context(print_workflow=True).write({'result': 'withdrawn', 'failure_note': why or False})
        for order in orders:
            names = ', '.join(pending.filtered(lambda l: l.order_id == order).mapped('patient_name'))
            left = order.line_ids.filtered(lambda l: l.result == 'pending')
            if order.state == 'draft' and not left:
                order.action_cancel()
            elif order.state == 'printing' and left:
                order.message_post(body=_("%(patients)s taken off the plate: %(why)s. The plate goes on "
                                          "for the others.", patients=names, why=why))
            elif order.state == 'printing':
                # nobody left on it: whatever the machine finishes is nobody's, so the
                # plate is given up - counted neither printed nor failed - and the
                # machine is free for the next one
                order._move({'state': 'cancel', 'date_end': fields.Datetime.now(), 'off_user_id': self.env.uid})
                order.message_post(body=_("%(patients)s taken off: %(why)s. Nobody is left on the plate, so it "
                                          "is cancelled and the machine is free.", patients=names, why=why))
        return orders

    # ================================================================== the machine
    def _start(self, printer, resin_batch=False, note=False):
        """Onto the machine: the plate is Printing, the clock runs, the registers
        are Print In Progress. One plate per machine - the index behind
        _ensure_free enforces it against two operators at once."""
        self.ensure_one()
        if self.state != 'draft':
            raise UserError(_("%s is not in Plating.", self.name))
        if not self.line_ids.filtered(lambda l: l.result == 'pending'):
            raise UserError(_("%s has no patient on it.", self.name))
        missing = self.line_ids.filtered(lambda l: l.result == 'pending' and not l.has_files)
        if missing:
            raise UserError(_("Nothing to print for %(patients)s on %(plate)s: no files and no download link. "
                              "Add them on the plate's Files tab first.",
                              patients=', '.join(missing.mapped('patient_name')), plate=self.name))
        printer._ensure_ready()
        printer._ensure_free()
        now = fields.Datetime.now()
        vals = {'state': 'printing', 'date_start': now, 'start_user_id': self.env.uid}
        if resin_batch:
            vals['resin_batch'] = resin_batch
        if note:
            vals['note'] = note
        if printer != self.printer_id:
            planned = self.printer_id.display_name
            vals['printer_id'] = printer.id
            if planned:
                self.message_post(body=_("Planned on %(planned)s, started on %(actual)s.",
                                         planned=planned, actual=printer.display_name))
        self._move(vals)
        when = self._local_time(self.expected_end)
        self.message_post(body=_("Print started on %(machine)s, expected off %(when)s.",
                                 machine=printer.display_name, when=when))
        for scan in self.scan_ids:
            scan.message_post(body=_("%(order)s started on %(machine)s, expected off %(when)s.",
                                     order=self._get_html_link(), machine=printer.display_name, when=when))
        self.scan_ids._sync_print_state()
        return True

    def _plate_off(self, results, skip_cure=False, replate_on=None):
        """The plate came off. `results` says, for every patient on it, printed or
        failed (and why, and which arch); the lost ones can go straight onto a new
        plate - a printer for a new one, or a plate still being plated."""
        self.ensure_one()
        if self.state != 'printing':
            raise UserError(_("%s is not printing.", self.name))
        pending = self.line_ids.filtered(lambda l: l.result == 'pending')
        missing = pending.filtered(lambda l: l.id not in results)
        if missing:
            raise UserError(_("Say what became of %s.", ', '.join(missing.mapped('patient_name'))))
        for line in pending:
            result, reason, note, failed_part = results[line.id]
            if result not in ('ok', 'failed'):
                raise UserError(_("A model came off printed or failed, nothing else."))
            if result == 'failed' and not reason:
                raise UserError(_("Say why %s's model failed.", line.patient_name))
            line.with_context(print_workflow=True).write({
                'result': result,
                'failure_reason': reason if result == 'failed' else False,
                'failure_note': note or False,
                'failed_part': (failed_part or 'all') if result == 'failed' else False,
            })
        ok = pending.filtered(lambda l: l.result == 'ok')
        failed = pending - ok
        now = fields.Datetime.now()
        vals = {'date_end': now, 'off_user_id': self.env.uid}
        if ok and skip_cure:
            vals.update(state='done', date_cured=now, cure_user_id=self.env.uid)
        elif ok:
            vals['state'] = 'washing'
        else:
            reasons = set(failed.mapped('failure_reason'))
            vals.update(state='failed', failure_reason=reasons.pop() if len(reasons) == 1 else 'other',
                        failure_note='\n'.join(filter(None, failed.mapped('failure_note'))) or False)
        self._move(vals)
        replate = self.browse()
        # `replate_on`: a machine to plan the new plate on - or no machine (an empty one),
        # picked at Start - or a plate being plated; None is no re-plate
        if failed and replate_on is not None:
            if replate_on._name == 'lab.scan.printer':
                replate = self._make(failed.scan_line_id, replate_on or None, resin_batch=self.resin_batch,
                                     note=_("Re-plate of %s", self.name))
            else:
                replate = self._make(failed.scan_line_id, existing=replate_on)
        time_text = _hours_text(self.duration_hours, self.env)
        for line in ok:
            line.scan_id.message_post(body=_("%(patient)s printed on %(machine)s in %(time)s.",
                                             patient=line.patient_name, machine=self.printer_id.display_name,
                                             time=time_text))
        reasons = dict(FAILURE_REASONS)
        for line in failed:
            what = _("%(patient)s's %(part)s", patient=line.patient_name, part=line.failed_part) \
                if line.failed_part in ('upper', 'lower') else line.patient_name
            where = _("re-plated on %s", replate._get_html_link()) if replate else _("waiting for a plate")
            line.scan_id.message_post(body=_("%(what)s failed on %(machine)s: %(reason)s — %(where)s.",
                                             what=what, machine=self.printer_id.display_name,
                                             reason=reasons.get(line.failure_reason), where=where))
        self.message_post(body=_("Plate off: %(ok)d printed, %(failed)d failed.", ok=len(ok), failed=len(failed)))
        self.scan_ids._sync_print_state()
        return replate

    def _fail(self, reason, note=False, restart_on=None):
        """The whole plate died mid-print. Every model on it failed for that one
        reason; unless told otherwise the same plate starts again at once, with the
        sliced file's figures carried over - no re-slicing."""
        self.ensure_one()
        if self.state != 'printing':
            raise UserError(_("%s is not printing.", self.name))
        pending = self.line_ids.filtered(lambda l: l.result == 'pending')
        pending.with_context(print_workflow=True).write({
            'result': 'failed', 'failure_reason': reason, 'failure_note': note or False, 'failed_part': 'all'})
        self._move({'state': 'failed', 'failure_reason': reason, 'failure_note': note or False,
                    'date_end': fields.Datetime.now(), 'off_user_id': self.env.uid})
        label = dict(FAILURE_REASONS).get(reason, reason)
        self.message_post(body=_("Print failed after %(time)s: %(reason)s.%(note)s",
                                 time=_hours_text(self.duration_hours, self.env), reason=label,
                                 note=(' ' + note) if note else ''))
        for scan in self.scan_ids:
            scan.message_post(body=_("%(order)s failed on %(machine)s after %(time)s: %(reason)s.",
                                     order=self._get_html_link(), machine=self.printer_id.display_name,
                                     time=_hours_text(self.duration_hours, self.env), reason=label))
        restart = self.browse()
        if restart_on:
            restart = self.copy({'restarted_from_id': self.id, 'printer_id': self.printer_id.id,
                                 'note': self.note, 'resin_batch': self.resin_batch})
            self._make(pending.scan_line_id, existing=restart)
            if self.slice_attachment_id:
                restart._move({'slice_attachment_id': self.slice_attachment_id.id})
            restart.message_post(body=_("Restart of %s after a failed print, with the same sliced file.",
                                        self._get_html_link()))
            restart._start(restart_on)
        self.scan_ids._sync_print_state()
        return restart

    def action_cure(self):
        """The models are out of the UV box: the plate is done."""
        for order in self:
            if order.state != 'washing':
                raise UserError(_("%s is not in Wash & Cure.", order.name))
            order._move({'state': 'done', 'date_cured': fields.Datetime.now(), 'cure_user_id': self.env.uid})
            order.message_post(body=_("Models washed and cured."))
            order.scan_ids._sync_print_state()
        return True

    def action_cancel(self):
        for order in self:
            if order.state != 'draft':
                raise UserError(_("%s has left Plating and cannot be cancelled — take it off the machine "
                                  "with Plate Off or Print Failed.", order.name))
            pending = order.line_ids.filtered(lambda l: l.result == 'pending')
            pending.with_context(print_workflow=True).write({'result': 'withdrawn',
                                                             'failure_note': _("Plate cancelled")})
            order._move({'state': 'cancel'})
            order.message_post(body=_("Plate cancelled."))
            pending.scan_id._sync_print_state()
        return True

    # ================================================================== the sliced file
    @api.model
    def read_slice_head(self, head_b64, filename=False, size=0):
        """The figures in the head of a sliced file, as the values to write on the
        plate: what the browser's slice_head widget asks for, and what tests feed.
        Nothing given clears them all; a file that cannot be read keeps its name
        and says why, so the figures can be typed."""
        vals = dict(SLICE_DEFAULTS)
        if not head_b64:
            return vals
        data = base64.b64decode(head_b64)
        read = slice_file.read(data)
        vals.update({
            'slice_filename': filename or _('sliced file'),
            'slice_size': int(size or len(data)),
            'slice_format': read['format'] or False,
            'slice_version': read['version'] or 0,
            'slice_parsed': not read['error'],
            'slice_error': read['error'] or False,
            'slice_truncated': bool(read['truncated']),
            'layer_count': read['layer_count'], 'layer_height_mm': read['layer_height_mm'],
            'total_height_mm': read['total_height_mm'], 'exposure_s': read['exposure_s'],
            'bottom_exposure_s': read['bottom_exposure_s'], 'bottom_layers': read['bottom_layers'],
            'resolution_x': read['resolution_x'], 'resolution_y': read['resolution_y'],
            'bed_x_mm': read['bed_x_mm'], 'bed_y_mm': read['bed_y_mm'], 'bed_z_mm': read['bed_z_mm'],
            'resin_g': read['weight_g'],
            'machine_name': read['machine_name'] or False,
            'software_version': read['software_version'] or False,
            'preview_image': base64.b64encode(read['preview_png']).decode() if read['preview_png'] else False,
            'preview_small': base64.b64encode(read['preview_small_png']).decode() if read['preview_small_png'] else False,
        })
        if vals['slice_parsed']:
            vals['estimated_hours'] = slice_file.hours(read['print_time_s'])
            vals['resin_ml'] = read['volume_ml']
        return vals

    def _apply_slice_head(self, head_bytes, filename=False, size=0):
        """The figures from a file's head, written at once (tests, and the stored file)."""
        head_b64 = base64.b64encode(head_bytes).decode() if head_bytes else False
        return self._move(self.read_slice_head(head_b64, filename, size))

    def _take_slice(self, previous=None):
        """The sliced file just set on the plate: made the plate's own (an upload
        arrives belonging to nobody), its figures read from its first megabyte, and
        the file it replaces removed. Only three files can become a plate's: its
        own, an upload of the person setting it, or another plate's (a restart
        prints the failed plate's file - shared, not moved)."""
        self.ensure_one()
        attachment = self.slice_attachment_id.sudo()
        old = (previous or self.env['ir.attachment']).sudo().exists()
        if old and old != attachment and (old.res_model, old.res_id) == (self._name, self.id) \
                and not self.search_count([('slice_attachment_id', '=', old.id), ('id', '!=', self.id)]):
            old.unlink()
        if not attachment:
            self._apply_slice_head(None)
            return True
        if (attachment.res_model, attachment.res_id) != (self._name, self.id):
            if attachment.res_model == self._name and attachment.res_id:
                self.browse(attachment.res_id).check_access('read')
            elif attachment.res_model or attachment.res_id or attachment.res_field or (
                    not self.env.su and attachment.create_uid.id != self.env.uid):
                raise AccessError(_("Only a file you uploaded - or another plate's sliced file - can become "
                                    "the sliced file of %s.", self.name))
            else:
                attachment.write({'res_model': self._name, 'res_id': self.id})
        self._apply_slice_head(self._head_of(attachment), attachment.name, attachment.file_size)
        return True

    @api.model
    def _head_of(self, attachment):
        """The first megabyte of an attachment, without reading the rest: a .ctb is
        50-300 MB of layer pictures behind a few hundred kilobytes of figures."""
        attachment = attachment.sudo()
        if attachment.store_fname:
            # Odoo 20: the stored file opens as a stream - only what is read is read
            with attachment._file_read().open() as handle:
                return handle.read(HEAD_BYTES)
        attachment.flush_recordset(['db_datas'])
        self.env.cr.execute("SELECT substring(db_datas FROM 1 FOR %s) FROM ir_attachment WHERE id = %s",
                            (HEAD_BYTES, attachment.id))
        row = self.env.cr.fetchone()
        return bytes(row[0]) if row and row[0] else b''

    # ================================================================== the files
    def _zip_files(self):
        """The plate's STLs, ready for ChituBox: a folder per patient, the register's
        shared files under every patient of that register, and a manifest. Built in
        a temporary file so a plate of large scans never sits in memory twice."""
        self.ensure_one()
        tmp = tempfile.TemporaryFile()
        folder = self.name.replace('/', '-')
        manifest = [_("Print order %s", self.name),
                    _("Machine: %s", self.printer_id.display_name or _('picked at Start Print')),
                    _("Save the slice as: %s", self.suggested_filename), '']
        with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_STORED) as archive:
            for line in self.line_ids.filtered(lambda l: l.result != 'withdrawn'):
                sub = '%s/%s - %s' % (folder, line.scan_id.name.replace('/', '-'),
                                      line.patient_name or _('patient'))
                files = line.attachment_ids
                for att in files:
                    # streamed from the filestore, a scan at a time (Odoo 20's raw is a
                    # lazy file, not the bytes)
                    with att.raw.open() as source, archive.open('%s/%s' % (sub, att.name), 'w') as target:
                        shutil.copyfileobj(source, target, ZIP_CHUNK)
                manifest.append('%s · %s · %s · %s · %s · %s' % (
                    line.patient_name or '-', line.scan_id.name, line.partner_id.name or '-',
                    dict(PARTS).get(line.part, line.part), line.date_delivery or '-',
                    ', '.join(files.mapped('name')) or _('no files')))
            archive.writestr('%s/manifest.txt' % folder, '\n'.join(manifest))
        tmp.seek(0)
        return tmp

    def action_download_files(self):
        """The patients' scans as one zip, a folder per patient, for the slicer."""
        self.ensure_one()
        if not any(l.attachment_ids for l in self.line_ids if l.result != 'withdrawn'):
            raise UserError(_("No patient on %s has files to download.", self.name))
        return {'type': 'ir.actions.act_url', 'url': '/scan/print-order/%d/files.zip' % self.id, 'target': 'self'}

    def action_download_file(self):
        """The sliced file, back onto the stick. (client, 2026-10-14: "Download File
        will download the sliced file")"""
        self.ensure_one()
        if not self.slice_attachment_id:
            raise UserError(_("%s has no sliced file kept - upload the ChituBox file on the Sliced File tab.",
                              self.name))
        return {'type': 'ir.actions.act_url', 'target': 'self',
                'url': '/web/content/%d?download=true' % self.slice_attachment_id.id}

    # ================================================================== finding
    @api.model
    def _by_code(self, text, company=None):
        """The plate a scanned ticket or a typed number names: PRINT/2026/0012,
        PRINT-2026-0012, print/2026/0012, or a bare 12 this year."""
        text = (text or '').strip().upper().replace('-', '/')
        if not text:
            return self.browse()
        match = re.match(r'^(?:PRINT/)?(\d{4})/(\d+)$', text)
        if match:
            name = 'PRINT/%s/%04d' % (match.group(1), int(match.group(2)))
        elif re.match(r'^\d+$', text):
            name = 'PRINT/%d/%04d' % (fields.Date.context_today(self).year, int(text))
        else:
            return self.browse()
        return self.search([('name', '=', name), ('company_id', '=', (company or self.env.company).id)], limit=1)

    @api.model
    def _waiting_domain(self, company=None):
        """The patients the printing room can put on a plate."""
        return Domain([('scan_id.company_id', '=', (company or self.env.company).id),
                       ('scan_id.state', 'in', PLATEABLE),
                       ('print_state', 'in', ('none', 'to_plate')),
                       ('sale_order_id', '=', False)])

    @api.model
    def _urgency_key(self, line):
        """Re-plates first, then the urgent, then the earliest delivery, then the
        longest waiting: the order patients go onto plates in."""
        return (0 if line.last_failure else 1, 0 if line.priority == '1' else 1,
                line.date_delivery or datetime.max.date(), line.scan_id.date_received or datetime.max, line.id)

    @api.model
    def _waiting_lines(self, company=None):
        lines = self.env['lab.scan.line'].search(self._waiting_domain(company))
        return lines.sorted(key=self._urgency_key)

    def _hours_text(self):
        self.ensure_one()
        return _hours_text(self.elapsed_hours, self.env)

    def _local_time(self, when):
        if not when:
            return _("—")
        local = pytz.utc.localize(when).astimezone(self.env['lab.scan']._lab_tz())
        return local.strftime('%H:%M')

    # ================================================================== cron
    @api.model
    def _cron_watch_prints(self):
        """A plate past its expected off time, in the lab's working hours, once:
        an activity on whoever minds the machine, else whoever started it."""
        now = fields.Datetime.now()
        hour = pytz.utc.localize(now).astimezone(self.env['lab.scan']._lab_tz()).hour
        if not LAB_HOURS[0] <= hour < LAB_HOURS[1]:
            return 0
        due = self.search([('state', '=', 'printing'), ('due_off_warned', '=', False),
                           ('expected_end', '<', now - timedelta(minutes=DUE_OFF_GRACE_MINUTES))])
        for order in due:
            user = order.printer_id.user_id or order.start_user_id or order.user_id
            order.activity_schedule(
                'mail.mail_activity_data_todo', user_id=user.id,
                summary=_("Plate %s is past its expected off time", order.name),
                note=_("%(order)s on %(machine)s was expected off at %(when)s and is still printing. "
                       "Take it off with Plate Off, or record a failed print.",
                       order=order.name, machine=order.printer_id.display_name,
                       when=order._local_time(order.expected_end)))
            order.write({'due_off_warned': True})
        return len(due)

    # ================================================================== openers
    def _dialog(self, model, name, **context):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': name, 'res_model': model, 'view_mode': 'form',
                'target': 'new', 'context': dict({'active_model': self._name, 'active_ids': self.ids,
                                                  'active_id': self.id, 'default_order_id': self.id}, **context)}

    def action_start_print(self):
        return self._dialog('lab.scan.print.order.start', _('Start Print'))

    def action_plate_off(self):
        return self._dialog('lab.scan.print.order.off', _('Plate Off'))

    def action_print_failed(self):
        return self._dialog('lab.scan.print.order.fail', _('Print Failed'))

    def action_add_patients(self):
        return self._dialog('lab.scan.print.order.make', _('Add Patients'), default_mode='existing')

    def action_print_ticket(self):
        # config=False: the ticket draws its own layout, so an administrator is not sent
        # to the company's document-layout wizard first
        return self.env.ref('lab_scan_register.action_report_plate_ticket').report_action(self, config=False)

    def action_view_registers(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': _('Registers — %s', self.name),
                'res_model': 'lab.scan', 'view_mode': 'list,form',
                'domain': [('id', 'in', self.scan_ids.ids)], 'context': {'create': False}}

    def action_view_lines(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': _('Models — %s', self.name),
                'res_model': 'lab.scan.print.order.line', 'view_mode': 'list',
                'domain': [('order_id', '=', self.id)], 'context': {'create': False}}


class PrintOrderLine(models.Model):
    _name = 'lab.scan.print.order.line'
    _description = 'Print Order Patient'
    _order = 'order_id desc, id'

    order_id = fields.Many2one('lab.scan.print.order', string='Print Order', required=True,
                               ondelete='cascade', index=True)
    scan_line_id = fields.Many2one('lab.scan.line', string='Patient Line', required=True, ondelete='restrict',
                                   index=True)
    scan_id = fields.Many2one(related='scan_line_id.scan_id', string='Scan Register', store=True, index=True)
    partner_id = fields.Many2one(related='scan_id.partner_id', string='Doctor / Clinic', store=True)
    patient_name = fields.Char(related='scan_line_id.patient_name', string='Patient')
    product_id = fields.Many2one(related='scan_line_id.product_id')
    description = fields.Char(related='scan_line_id.description')
    date_delivery = fields.Date(related='scan_line_id.date_delivery')
    priority = fields.Selection(related='scan_line_id.priority')
    company_id = fields.Many2one(related='order_id.company_id', store=True, index=True)
    printer_id = fields.Many2one(related='order_id.printer_id', string='Machine', store=True, index=True)
    order_state = fields.Selection(related='order_id.state', string='Plate Status', store=True)
    date_start = fields.Datetime(related='order_id.date_start', store=True)
    date_end = fields.Datetime(related='order_id.date_end', store=True)
    part = fields.Selection(PARTS, string='Part', default='both', required=True,
                            help="What of this patient is on this plate.")
    model_count = fields.Integer('Models', default=2, help="Arches of this patient on the plate.")
    attempt = fields.Integer('Attempt', readonly=True, default=1, help="The patient's first plate is 1.")
    result = fields.Selection(RESULTS, string='Result', default='pending', required=True, index=True)
    failure_reason = fields.Selection(FAILURE_REASONS, string='Failure Reason')
    failure_note = fields.Char('Note')
    failed_part = fields.Selection(FAILED_PARTS, string='Failed Part')
    replated_from_id = fields.Many2one('lab.scan.print.order.line', string='Re-plate Of', readonly=True,
                                       index='btree_not_null', help="The failed line this one prints again.")
    replated_by_id = fields.Many2one('lab.scan.print.order.line', string='Re-plated On', compute='_compute_replated_by',
                                     search='_search_replated_by_id')
    superseded = fields.Boolean(default=False,
                                help="Set when the register went back to draft after this line: the line "
                                     "stays in the machine's record but no longer says where the patient is.")
    attachment_ids = fields.Many2many('ir.attachment', compute='_compute_attachments', string='Files')
    # what the room downloads and slices from, managed on the plate's Files tab
    file_ids = fields.Many2many('ir.attachment', string='Add Scans', compute='_compute_file_ids',
                                inverse='_inverse_file_ids',
                                help="This patient's own scans. Files added here are the register's too, and "
                                     "go to the patient's order.")
    shared_file_ids = fields.Many2many('ir.attachment', string='Shared Files', compute='_compute_file_ids',
                                       help="The register's files no patient picked - a bite record, photos - "
                                            "which go to every patient of it.")
    file_count = fields.Integer('File Count', compute='_compute_file_count', store=True)
    has_files = fields.Boolean('Has Files', compute='_compute_file_count', store=True,
                               help="Files of its own or shared, or at least a download link.")
    download_url = fields.Char(related='scan_id.download_url', readonly=False, string='Download Link')
    download_expiry = fields.Date(related='scan_id.download_expiry', readonly=False, string='Link Expires')
    download_state = fields.Selection(related='scan_id.download_state', string='Link Status')
    scan_state = fields.Selection(related='scan_id.state', string='Register Status')

    _one_open_plate_per_patient = models.UniqueIndex("(scan_line_id) WHERE result = 'pending'",
                                                     "This patient is already on a plate that has not come off yet.")

    @api.depends('scan_line_id.attachment_ids', 'scan_id.attachment_ids')
    def _compute_attachments(self):
        for line in self:
            own = line.scan_line_id.attachment_ids
            shared = line.scan_id._shared_attachments() if line.scan_id else own.browse()
            line.attachment_ids = (own | shared) or line.scan_id.attachment_ids

    @api.depends('scan_line_id.attachment_ids', 'scan_id.attachment_ids', 'scan_id.line_ids.attachment_ids')
    def _compute_file_ids(self):
        for line in self:
            line.file_ids = line.scan_line_id.attachment_ids
            line.shared_file_ids = line.scan_id._shared_attachments() if line.scan_id else line.file_ids.browse()

    def _inverse_file_ids(self):
        for line in self:
            if set(line.file_ids.ids) != set(line.scan_line_id.attachment_ids.ids):
                line.scan_line_id.attachment_ids = [Command.set(line.file_ids.ids)]

    @api.depends('scan_line_id.attachment_ids', 'scan_id.attachment_ids', 'scan_id.line_ids.attachment_ids',
                 'scan_id.download_url')
    def _compute_file_count(self):
        for line in self:
            shared = line.scan_id._shared_attachments() if line.scan_id else line.scan_line_id.attachment_ids.browse()
            line.file_count = len(line.scan_line_id.attachment_ids | shared)
            line.has_files = bool(line.file_count or line.scan_id.download_url)

    @api.onchange('download_url')
    def _onchange_download_url(self):
        """A WeTransfer link dies in days: its expiry comes with it, as on the register."""
        platform = self.env['lab.scan.channel']._match_link(self.download_url, self.company_id)
        if self.download_url and platform.link_validity_days and not self.download_expiry:
            self.download_expiry = fields.Date.context_today(self) + timedelta(days=platform.link_validity_days)

    def action_open_link(self):
        self.ensure_one()
        if not self.download_url:
            raise UserError(_("%s's register has no download link.", self.patient_name or _('This patient')))
        return {'type': 'ir.actions.act_url', 'url': self.download_url, 'target': 'new'}

    def _search_replated_by_id(self, operator, value):
        """'Not re-plated yet' is the question the room asks of a failed model."""
        if operator in ('in', 'not in'):
            values = set(value)
        elif operator in ('=', '!='):
            values = {value}
            operator = 'in' if operator == '=' else 'not in'
        else:
            raise NotImplementedError()
        replated = self.search([('replated_from_id', '!=', False)])
        ids = set()
        if False in values or None in values:
            ids |= set(self.search([('id', 'not in', replated.replated_from_id.ids)]).ids)
        wanted = {v for v in values if v}
        if wanted:
            ids |= set(self.browse(list(wanted)).exists().replated_from_id.ids)
        return [('id', operator, list(ids))]

    def _compute_replated_by(self):
        latest = {}
        for line in self.search([('replated_from_id', 'in', self.ids)], order='id'):
            latest[line.replated_from_id.id] = line
        for line in self:
            line.replated_by_id = latest.get(line.id, False)

    @api.depends('order_id.name', 'patient_name')
    def _compute_display_name(self):
        for line in self:
            line.display_name = '%s · %s' % (line.order_id.name or '', line.patient_name or _('No patient'))

    def write(self, vals):
        if not self.env.context.get('print_workflow'):
            if {'result', 'failure_reason', 'failed_part', 'attempt', 'replated_from_id', 'superseded'} & set(vals):
                raise UserError(_("What became of a model is recorded with Plate Off and Print Failed, not by "
                                  "editing it."))
            if {'part', 'model_count', 'scan_line_id', 'order_id'} & set(vals):
                started = self.filtered(lambda l: l.order_id.state != 'draft')
                if started:
                    raise UserError(_("%s has left Plating; its patients are settled.",
                                      ', '.join(started.order_id.mapped('name'))))
        return super().write(vals)

    def unlink(self):
        self.check_access('unlink')
        if any(l.order_id.state != 'draft' for l in self):
            raise UserError(_("A patient on a plate that ran stays on its record — take them off instead."))
        return super().unlink()

    def action_take_off(self):
        """Off the plate while it is still being plated - or marked Taken Off on a
        plate that is printing; the plate goes on for the others."""
        for line in self:
            if line.result != 'pending':
                raise UserError(_("%s is no longer on this plate.", line.patient_name))
        why = _("Taken off by %s", self.env.user.name)
        orders = self.env['lab.scan.print.order']._withdraw_lines(self.scan_line_id, why)
        self.scan_id._sync_print_state()
        return True

    def action_view_scan(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'lab.scan', 'res_id': self.scan_id.id,
                'view_mode': 'form'}

    def action_view_order(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'lab.scan.print.order',
                'res_id': self.order_id.id, 'view_mode': 'form'}
