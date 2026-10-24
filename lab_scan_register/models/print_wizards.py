# -*- coding: utf-8 -*-
"""The four dialogs of the printing room.

New Print Order: the patients going on one plate, and the machine it is planned
on - the free one offered, the sticker scanned to pick another; or Queue Only, and
the room plates them later from Waiting for a Plate.

Start Print: the plate goes on a machine - scanned off the machine's sticker (or the
plate ticket, to pick the plate) - and the clock starts.

Plate Off: every patient ticked Printed; untick the ones that failed - or just the
arch that failed, U or L - and why, and the failed models go on the next plate at
once: the plate being plated with room for them, or a new one filled with the
patients waiting. The same board, opened while the plate prints, is A Model Failed:
a model seen coming off mid-print is recorded then, and the plate goes on.

Print Failed: the whole plate was lost mid-print; what went wrong, on record against
the machine, and unless somebody says otherwise it starts again now.

Every dialog talks to the order through the same few methods (`_make`, `_start`,
`_plate_off`, `_fail`); nothing in here changes a state by itself.
"""
import pytz

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError

from .print_order import FAILURE_REASONS, _arches
from .printer import EPOCH

ORDER = 'lab.scan.print.order'
PRINTER = 'lab.scan.printer'


# ------------------------------------------------------------------ shared helpers
def _clock(env, when):
    """06:40 - on the lab's clock, not UTC."""
    if not when:
        return ''
    return pytz.utc.localize(when).astimezone(env['lab.scan']._lab_tz()).strftime('%H:%M')


def _hm(hours, env):
    """2h 38m. The helpers here translate through `env._`: their own frames carry no
    record for _() to read the language from."""
    hours = hours or 0.0
    whole = int(hours)
    return env._("%(h)dh %(m)02dm", h=whole, m=int(round((hours - whole) * 60)))


def _printer_hint(printer):
    """One line about the machine offered: what it is doing and how reliable it is -
    "free · 2% of 118 models failed" or "printing PRINT/2026/0012 · off at 06:40 ·
    2 plates queued"."""
    if not printer:
        return False
    _ = printer.env._
    running = printer.running_order_id
    if running:
        parts = [_("printing %s", running.name)]
        if running.expected_end:
            parts.append(_("off at %s", _clock(printer.env, running.expected_end)))
    else:
        parts = [_("free")]
        models_ = printer.model_done_count + printer.model_failed_count
        if models_:
            parts.append(_("%(rate).0f%% of %(n)d models failed", rate=printer.failure_rate, n=models_))
        else:
            parts.append(_("no models on record yet"))
    if printer.queued_count:
        parts.append(_("%d plate(s) queued", printer.queued_count))
    return ' · '.join(parts)


def _take_code(wizard, field='code', printer_field='printer_id', order_field='order_id', order_states=None):
    """Whatever was scanned into the code box, taken and the box cleared.

    A machine sticker names a machine: it goes into `printer_field` - or, in a dialog
    without a machine box (Plate Off), it picks the plate printing on that machine.
    A plate ticket names an order: it goes into `order_field`, if its state is one the
    dialog can work with. Anything else is a warning, and nothing changes."""
    code = wizard[field]
    if not code:
        return None
    wizard[field] = False
    env = wizard.env
    _ = env._
    printer = env[PRINTER]._by_code(code)
    if printer:
        if printer_field:
            wizard[printer_field] = printer
            return None
        running = printer.running_order_id
        if order_field and running and (not order_states or running.state in order_states):
            wizard[order_field] = running
            return None
        return {'warning': {'title': _("Nothing printing"),
                            'message': _("Nothing is printing on %s.", printer.display_name)}}
    order = env[ORDER]._by_code(code)
    if order:
        if not order_field:
            return {'warning': {'title': _("That is a plate"),
                                'message': _("%s is a plate ticket — scan a machine's sticker here.", order.name)}}
        if order_states and order.state not in order_states:
            labels = dict(order._fields['state']._description_selection(env))
            return {'warning': {'title': _("Wrong plate"),
                                'message': _("%(plate)s is %(state)s and cannot be used here.",
                                             plate=order.name, state=labels.get(order.state, order.state).lower())}}
        wizard[order_field] = order
        return None
    return {'warning': {'title': _("No such machine or plate"),
                        'message': _("Nothing carries the number or name '%s'.", code)}}


def _models(line, part=None):
    """Models a patient puts on a plate: the arches still to print - after a plate
    lost only the lower, one - and both arches are two."""
    part = part or line.print_part or line.arch
    return 2 if part == 'both' else 1


def _fill_candidates(env, room, exclude, company):
    """Waiting patients for a plate's free room: the ones back from a failed plate
    first, then the urgent, then the earliest delivery (_urgency_key); a patient's
    arches together, and one who does not fit is passed over for a smaller one who
    does. Only patients the desk sent for printing."""
    out = env['lab.scan.line']
    if room <= 0:
        return out
    used = 0
    for line in env[ORDER]._waiting_lines(company):
        if line in exclude or line.print_state != 'to_plate' or line.scan_id.already_printed:
            continue
        need = _models(line)
        if used + need > room:
            continue
        out |= line
        used += need
        if used >= room:
            break
    return out


def _last_batch(printer):
    """The resin batch written on the machine's latest plate - most likely still in the vat."""
    if not printer:
        return False
    last = printer.env[ORDER].search([('printer_id', '=', printer.id), ('resin_batch', '!=', False)],
                                     order='id desc', limit=1)
    return last.resin_batch or False


def _active(env, model):
    """The records the dialog was opened on, when they are of `model`."""
    ctx = env.context
    if ctx.get('active_model') != model:
        return env[model]
    ids = ctx.get('active_ids') or ([ctx['active_id']] if ctx.get('active_id') else [])
    return env[model].browse(ids).exists()


def _form(order):
    return {'type': 'ir.actions.act_window', 'name': order.name, 'res_model': ORDER,
            'res_id': order.id, 'view_mode': 'form', 'target': 'current'}


CLOSE = {'type': 'ir.actions.act_window_close'}


# ------------------------------------------------------------------ New Print Order
class PrintOrderMake(models.TransientModel):
    _name = 'lab.scan.print.order.make'
    _description = 'New Print Order'

    scan_ids = fields.Many2many('lab.scan', string='Registers', readonly=True,
                                help="The registers this dialog was opened on.")
    # The picker offers the waiting patients; as text, so it stays the picker's: Odoo 20
    # enforces a list domain on every write, and Print Again or a re-plate puts a
    # patient on a plate who is not "waiting" (printed, or failed on the plate going off).
    line_ids = fields.Many2many('lab.scan.line', string='Patients',
                                domain=lambda self: str(list(self.env[ORDER]._waiting_domain())),
                                help="The patients going on this plate. Re-plates first, urgent next, "
                                     "earliest delivery after.")
    mode = fields.Selection([('new', 'New plate'), ('existing', 'Add to a plate being plated')],
                            string='Put On', default='new', required=True)
    order_id = fields.Many2one(ORDER, string='Plate', domain=[('state', '=', 'draft')])
    printer_code = fields.Char('Scan the Sticker',
                               help="Scan the machine's sticker here, or type its number or name.")
    # The machine is picked at Start Print, not here (client, 2026-10-14); the field
    # stays for a plate planned on one (a re-plate on the same machine, the spread).
    printer_id = fields.Many2one(PRINTER, string='Machine', domain=[('state', '=', 'ready')],
                                 help="Planned while plating; the machine it runs on is settled at Start.")
    printer_hint = fields.Char(compute='_compute_printer_hint')
    # the ChituBox file, uploaded as the plate is made (in pieces, by the
    # slice_upload widget) and kept on it
    slice_attachment_id = fields.Many2one('ir.attachment', string='Sliced File', ondelete='set null',
                                          help="The ChituBox file for this plate. Its time, resin and layers are read "
                                               "from it, and the plate keeps it for download. On a plate being "
                                               "plated it replaces the file the plate had.")
    model_count = fields.Integer('Models', compute='_compute_capacity',
                                 help="Arches on the plate: two for a patient with upper and lower, else one.")
    capacity = fields.Integer('Plate Holds', compute='_compute_capacity')
    over_capacity = fields.Boolean(compute='_compute_capacity')
    capacity_text = fields.Char(compute='_compute_capacity')
    resin_batch = fields.Char('Resin Batch', help="The bottle or lot in the vat.")
    note = fields.Char('Plate Note', help="Anything about this plate: position, who loaded it.")
    start_now = fields.Boolean('Start the print now', default=False,
                               help="The plate goes straight on the machine - no slicing figures, so "
                                    "the clock uses the machine's typical time.")
    split = fields.Boolean('Spread over the free machines',
                           help="More models than one plate holds: fill a plate on each machine in service - "
                                "the free one first - the urgent patients first, a patient's arches together.")
    plan_text = fields.Char('Plan', compute='_compute_plan_text')
    # Add Patients fills the plate's room with the patients waiting, by urgency, so a
    # plate is made in one dialog (client, 2026-10-23); the ones it opened with stay.
    fill = fields.Boolean('Fill with the patients waiting, by urgency',
                          help="Adds the patients waiting for a plate - back from a failed plate first, then the "
                               "urgent, then the earliest delivery - up to what the plate holds.")
    initial_line_ids = fields.Many2many('lab.scan.line', 'lab_scan_make_initial_rel', 'wizard_id', 'line_id',
                                        string='Opened With')
    # Print Again: which arch broke in finishing
    again = fields.Boolean()
    again_part = fields.Selection([('both', 'Upper & Lower'), ('upper', 'Upper'), ('lower', 'Lower')],
                                  string='Print Again', help="The arch to print again.")

    # ------------------------------------------------------------------ defaults
    @api.model
    def default_get(self, fields_list):
        """Opened on registers (desk: Send to 3D Printing), on waiting patients (room: Put on
        a Plate), on one patient (Print Again) or on a plating order (Add Patients)."""
        res = super().default_get(fields_list)
        ctx = self.env.context
        if ctx.get('default_again'):
            res['again'] = True
        Order, Line = self.env[ORDER], self.env['lab.scan.line']
        scans = _active(self.env, 'lab.scan')
        order = Order.browse(res.get('order_id')).exists() if res.get('order_id') else _active(self.env, ORDER)[:1]
        if order and order.state != 'draft':
            order = Order
        if order:
            res['order_id'] = order.id
            if 'default_mode' not in ctx:
                res['mode'] = 'existing'
            if 'default_printer_id' not in ctx:
                res['printer_id'] = order.printer_id.id
        if 'scan_ids' in fields_list and 'default_scan_ids' not in ctx and scans:
            res['scan_ids'] = [Command.set(scans.filtered(
                lambda s: s.state in ('confirmed', 'printing', 'in_progress')).ids)]
        if 'line_ids' in fields_list and 'default_line_ids' not in ctx:
            lines = Line
            if scans:
                lines = Order._waiting_lines().filtered(lambda l: l.scan_id in scans)
            elif ctx.get('active_model') == 'lab.scan.line':
                lines = _active(self.env, 'lab.scan.line')
            elif ctx.get('default_scan_line_id'):
                lines = Line.browse(ctx['default_scan_line_id']).exists()
            if 'initial_line_ids' in fields_list:
                res['initial_line_ids'] = [Command.set(lines.ids)]
            if not lines and order and res.get('mode', ctx.get('default_mode')) == 'existing':
                # Add Patients with nobody picked: the plate's room, filled by urgency
                capacity, used = Order._plate_room(order)
                lines = _fill_candidates(self.env, capacity - used, order.line_ids.scan_line_id, order.company_id)
                res['fill'] = bool(lines)
            if lines:
                res['line_ids'] = [Command.set(lines.ids)]
            if ctx.get('default_again') and len(lines) == 1 and 'again_part' in fields_list:
                res['again_part'] = lines.arch or 'both'
        if 'resin_batch' in fields_list and not res.get('resin_batch'):
            res['resin_batch'] = _last_batch(self.env[PRINTER].browse(res.get('printer_id')))
        return res

    # ------------------------------------------------------------------ computes
    @api.depends('printer_id', 'printer_id.running_order_id', 'printer_id.running_order_id.expected_end',
                 'printer_id.queued_count', 'printer_id.model_done_count', 'printer_id.model_failed_count',
                 'printer_id.failure_rate')
    def _compute_printer_hint(self):
        for wiz in self:
            wiz.printer_hint = _printer_hint(wiz.printer_id)

    @api.depends('line_ids.arch', 'line_ids.print_part', 'printer_id.plate_capacity', 'mode',
                 'order_id.model_count', 'order_id.printer_id.plate_capacity', 'again', 'again_part')
    def _compute_capacity(self):
        """The gauge: "7 of 8 models". A hint, never a block - the room knows its plates.
        With no machine picked yet, the smallest plate among the machines in service."""
        for wiz in self:
            existing = wiz.mode == 'existing' and wiz.order_id
            printer = wiz.order_id.printer_id if existing else wiz.printer_id
            again = wiz.again_part if wiz.again else None
            count = (wiz.order_id.model_count if existing else 0) + sum(_models(line, again) for line in wiz.line_ids)
            capacity = printer.plate_capacity if printer else wiz._usual_capacity()
            wiz.model_count = count
            wiz.capacity = capacity
            wiz.over_capacity = bool(capacity) and count > capacity
            wiz.capacity_text = _("%(n)d of %(cap)d models", n=count, cap=capacity) if capacity \
                else _("%d model(s)", count)

    def _usual_capacity(self):
        ready = self.env[PRINTER].search([('state', '=', 'ready'), ('company_id', '=', self.env.company.id)])
        return min(ready.mapped('plate_capacity') or [0])

    @api.depends('split', 'line_ids', 'line_ids.arch', 'line_ids.print_part', 'mode')
    def _compute_plan_text(self):
        """"3DP-01: 4 patients (8 models) · 3DP-02: 1 patient (2 models)" - the plates
        the spread would make, before anything is made."""
        for wiz in self:
            if not wiz.split or wiz.mode != 'new' or not wiz.line_ids:
                wiz.plan_text = False
                continue
            # a loop, not a generator: _() reads the language from its caller's frame
            bits = []
            for printer, lines in wiz._plan():
                bits.append(_("%(machine)s: %(n)d patient(s), %(models)d models", machine=printer.code,
                              n=len(lines), models=sum(_models(l) for l in lines)))
            wiz.plan_text = ' · '.join(bits) or _("No machine is in service to take a plate.")

    def _plan(self):
        """[(machine, patients)]: one plate per machine in service, each filled up to its
        plate's capacity - the most urgent patients first, both arches of a patient on
        the same plate - and round again over the machines while anyone is left. A
        machine busy now still gets a plate: it waits in that machine's queue."""
        self.ensure_one()
        Order = self.env[ORDER]
        machines = self.env[PRINTER].search([('state', '=', 'ready'), ('company_id', '=', self.env.company.id)])
        machines = machines.sorted(lambda p: (p.is_busy, p.expected_free_at or EPOCH, p.failure_rate,
                                              p.sequence, p.id))
        queue = sorted(self.line_ids, key=Order._urgency_key)
        plan = []
        while queue and machines:
            for machine in machines:
                if not queue:
                    break
                capacity, used, plate = machine.plate_capacity, 0, []
                for line in list(queue):
                    need = _models(line)
                    if capacity and plate and used + need > capacity:
                        continue
                    plate.append(line)
                    queue.remove(line)
                    used += need
                    if capacity and used >= capacity:
                        break
                plan.append((machine, self.env['lab.scan.line'].concat(plate)))
        return plan

    # ------------------------------------------------------------------ onchanges
    @api.onchange('printer_code')
    def _onchange_printer_code(self):
        return _take_code(self, 'printer_code', 'printer_id', 'order_id', order_states=('draft',))

    @api.onchange('order_id')
    def _onchange_order_id(self):
        if self.order_id:
            self.mode = 'existing'
            self.printer_id = self.order_id.printer_id

    @api.onchange('fill')
    def _onchange_fill(self):
        """On: the room left on the plate filled with the patients waiting. Off: back to
        the patients the dialog opened with."""
        Order = self.env[ORDER]
        if not self.fill:
            self.line_ids = self.initial_line_ids
            return
        plate = self.order_id if self.mode == 'existing' else Order
        capacity, used = Order._plate_room(plate=plate or None, printer=None if plate else self.printer_id)
        used += sum(_models(line) for line in self.line_ids)
        exclude = self.line_ids | (plate.line_ids.scan_line_id if plate else self.env['lab.scan.line'])
        self.line_ids = self.line_ids | _fill_candidates(self.env, capacity - used, exclude, self.env.company)

    @api.onchange('printer_id')
    def _onchange_printer_id(self):
        if not self.resin_batch:
            self.resin_batch = _last_batch(self.printer_id)

    # ------------------------------------------------------------------ buttons
    def action_create(self):
        """A plate, now: the registers still at the desk go for printing, the order is
        made, and if asked it starts on the machine at once."""
        self.ensure_one()
        if not self.line_ids:
            raise UserError(_("Pick at least one patient for the plate."))
        if self.mode == 'existing':
            if not self.order_id:
                raise UserError(_("Pick the plate being plated to add the patients to."))
            existing, printer = self.order_id, self.order_id.printer_id
        else:
            existing, printer = None, self.printer_id
            if self.start_now and not printer:
                raise UserError(_("Pick the machine to start the plate on."))
        if self.split and not existing and self.slice_attachment_id:
            raise UserError(_("One sliced file is one plate: untick Spread, or upload each plate's file on it afterwards."))
        self.line_ids.scan_id.filtered(lambda s: s.state == 'confirmed')._send_to_print()
        if self.split and not existing:
            plan = self._plan()
            if not plan:
                raise UserError(_("No machine is in service to take a plate."))
            orders = self.env[ORDER]
            for machine, lines in plan:
                orders |= self.env[ORDER]._make(lines, printer=machine, resin_batch=self.resin_batch or False,
                                                note=self.note or False)
            if len(orders) == 1:
                return _form(orders)
            return {'type': 'ir.actions.act_window', 'name': _('The plates planned'), 'res_model': ORDER,
                    'view_mode': 'list,form', 'views': [(False, 'list'), (False, 'form')],
                    'domain': [('id', 'in', orders.ids)], 'context': {'create': False}}
        parts = {line.id: self.again_part for line in self.line_ids} if self.again and self.again_part else None
        order = self.env[ORDER]._make(self.line_ids, printer=None if existing else printer,
                                      resin_batch=self.resin_batch or False, note=self.note or False,
                                      existing=existing, parts=parts)
        self._hand_slice_to(order)
        if self.start_now:
            order._start(printer, resin_batch=self.resin_batch or False, note=self.note or False)
        return _form(order)

    def _hand_slice_to(self, order):
        """The uploaded ChituBox file becomes the plate's - the same attachment, never
        a second copy of a 200 MB file - and its figures are read there. On a plate
        being plated it replaces the file the plate had: patients were added, so the
        plate was sliced again."""
        self.ensure_one()
        if self.slice_attachment_id:
            order._move({'slice_attachment_id': self.slice_attachment_id.id})

    def action_queue_only(self):
        """No plate yet: the registers go for printing and the room plates them later."""
        self.ensure_one()
        if self.slice_attachment_id:
            raise UserError(_("A sliced file belongs to a plate: create the print order, or take the file off "
                              "to queue the registers without a plate."))
        self.scan_ids.filtered(lambda s: s.state == 'confirmed')._send_to_print()
        return CLOSE


# ------------------------------------------------------------------ Start Print
class PrintOrderStart(models.TransientModel):
    _name = 'lab.scan.print.order.start'
    _description = 'Start Print'

    order_id = fields.Many2one(ORDER, string='Plate', required=True, domain=[('state', '=', 'draft')])
    code = fields.Char('Scan the Sticker (or the Plate Ticket)',
                       help="Scan the machine's sticker to pick the machine, or a plate ticket to pick the plate.")
    printer_id = fields.Many2one(PRINTER, string='Machine', required=True, domain=[('state', '=', 'ready')])
    printer_hint = fields.Char(compute='_compute_printer_hint')
    file_summary = fields.Char(compute='_compute_file_summary')
    profile_warning = fields.Char(compute='_compute_profile_warning')
    checklist_html = fields.Html('Before It Starts', compute='_compute_checklist', sanitize=False)
    blocked = fields.Boolean(compute='_compute_checklist')
    resin_batch = fields.Char('Resin Batch', help="The bottle or lot in the vat.")
    note = fields.Char('Plate Note')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        Order = self.env[ORDER]
        order = Order.browse(res.get('order_id')).exists() if res.get('order_id') else _active(self.env, ORDER)[:1]
        if order:
            res['order_id'] = order.id
            # the plate's planned machine, else the free, reliable one - the sticker
            # scanned in the dialog overrides either
            res.setdefault('printer_id', (order.printer_id or self.env[PRINTER]._suggest(order.company_id)).id)
            if not res.get('resin_batch'):
                res['resin_batch'] = order.resin_batch or False
            if not res.get('note'):
                res['note'] = order.note or False
        return res

    @api.depends('printer_id', 'printer_id.running_order_id', 'printer_id.running_order_id.expected_end',
                 'printer_id.queued_count', 'printer_id.model_done_count', 'printer_id.model_failed_count',
                 'printer_id.failure_rate')
    def _compute_printer_hint(self):
        for wiz in self:
            wiz.printer_hint = _printer_hint(wiz.printer_id)

    @api.depends('order_id.slice_parsed', 'order_id.layer_count', 'order_id.layer_height_mm',
                 'order_id.estimated_hours', 'order_id.resin_ml', 'order_id.machine_name',
                 'printer_id.typical_hours')
    def _compute_file_summary(self):
        """What the clock will run on: the file's figures, typed ones, or the machine's typical time."""
        for wiz in self:
            order = wiz.order_id
            if not order:
                wiz.file_summary = False
            elif order.slice_parsed:
                parts = [_("%s layers", f"{order.layer_count:,}"), _("%.2f mm", order.layer_height_mm),
                         _("est. %s", _hm(order.estimated_hours, self.env)), _("%.0f mL", order.resin_ml)]
                if order.machine_name:
                    parts.append(_("sliced for %s", order.machine_name))
                wiz.file_summary = ' · '.join(parts)
            elif order.estimated_hours:
                wiz.file_summary = _("figures typed: est. %s", _hm(order.estimated_hours, self.env))
            else:
                wiz.file_summary = _("no sliced file — the clock will use %(machine)s's typical %(time)s",
                                     machine=wiz.printer_id.display_name or _("the machine"),
                                     time=_hm(wiz.printer_id.typical_hours, self.env))

    @api.depends('order_id', 'printer_id')
    def _compute_profile_warning(self):
        """The order's own warning, asked for the machine chosen here: a new record with
        the order as origin carries the file's figures and the other machine."""
        for wiz in self:
            order, printer = wiz.order_id, wiz.printer_id
            if not order or not printer:
                wiz.profile_warning = False
            elif printer == order.printer_id:
                wiz.profile_warning = order.profile_warning
            else:
                wiz.profile_warning = order.new({'printer_id': printer.id}, origin=order).profile_warning

    @api.depends('order_id', 'printer_id', 'printer_id.state', 'printer_id.running_order_id')
    def _compute_checklist(self):
        Order = self.env[ORDER]
        for wiz in self:
            items = wiz.order_id._checklist_items(wiz.printer_id, at_start=True) if wiz.order_id else []
            wiz.checklist_html = Order._checklist_markup(items) if items else False
            wiz.blocked = any(level == 'block' for level, _text in items)

    @api.onchange('code')
    def _onchange_code(self):
        return _take_code(self, 'code', 'printer_id', 'order_id', order_states=('draft',))

    @api.onchange('order_id')
    def _onchange_order_id(self):
        if self.order_id:
            if not self.printer_id:
                self.printer_id = self.order_id.printer_id
            if not self.resin_batch:
                self.resin_batch = self.order_id.resin_batch
            if not self.note:
                self.note = self.order_id.note

    def action_start(self):
        self.ensure_one()
        if not self.order_id:
            raise UserError(_("Pick the plate to start."))
        if not self.printer_id:
            raise UserError(_("Pick the machine the plate is loaded on."))
        self.order_id._start(self.printer_id, resin_batch=self.resin_batch or False, note=self.note or False)
        return CLOSE


# ------------------------------------------------------------------ Plate Off
# A re-plate is a new plate like any other: its machine is picked at Start Print
# (client, 2026-10-14). "The same machine" is kept as a plan the Start dialog offers.
# By default the failed models go where they fit best: the plate being plated with
# room for them, else a new plate - filled with the patients waiting (2026-10-23).
REPLATE_MODES = [('auto', 'The best plate: one being plated with room, else a new one'),
                 ('new', 'A new plate - machine picked at Start'),
                 ('same', 'A new plate planned on the same machine'),
                 ('existing', 'Add to a plate being plated')]
FAILED_PARTS = [('all', 'All'), ('upper', 'Upper'), ('lower', 'Lower')]
BOARD_MODES = [('off', 'Plate Off'), ('lost', 'A Model Failed')]


class PrintOrderOff(models.TransientModel):
    _name = 'lab.scan.print.order.off'
    _description = 'Plate Off'

    mode = fields.Selection(BOARD_MODES, default='off', required=True,
                            help="Plate Off: the plate came off. A Model Failed: a model came off while "
                                 "the plate goes on printing.")
    order_id = fields.Many2one(ORDER, string='Plate', required=True, domain=[('state', '=', 'printing')])
    code = fields.Char('Scan the Plate Ticket',
                       help="Scan the plate ticket - or the machine's sticker - to pick the plate coming off.")
    line_ids = fields.One2many('lab.scan.print.order.off.line', 'wizard_id', string='Patients')
    failed_count = fields.Integer(compute='_compute_failed')
    failed_models = fields.Integer('Models Lost', compute='_compute_failed')
    replate_models = fields.Integer('Models to Re-plate', compute='_compute_failed',
                                    help="The lost models going on another plate now.")
    reason = fields.Selection(FAILURE_REASONS, string='What Went Wrong',
                              help="One reason for the plate; a row can say otherwise.")
    note = fields.Text('Details')
    skip_cure = fields.Boolean('Models already washed and cured', default=False)
    replate = fields.Boolean('Re-plate the failed models now', default=True)
    replate_mode = fields.Selection(REPLATE_MODES, string='Re-plate On', default='auto', required=True)
    replate_order_id = fields.Many2one(ORDER, string='Plate Being Plated', domain=[('state', '=', 'draft')])
    suggested_order_id = fields.Many2one(ORDER, string='Best Plate', compute='_compute_suggestion')
    suggest_text = fields.Char(compute='_compute_suggestion')
    same_text = fields.Char(compute='_compute_suggestion')
    fill = fields.Boolean('Fill the plate with the patients waiting',
                          help="The patients waiting for a plate join the re-plates - back from a failed plate "
                               "first, then the urgent, then the earliest delivery - up to what the plate holds.")
    fill_line_ids = fields.Many2many('lab.scan.line', 'lab_scan_off_fill_rel', 'wizard_id', 'line_id',
                                     string='Waiting Patients', compute='_compute_fill', store=True,
                                     readonly=False)
    capacity_text = fields.Char(compute='_compute_capacity_text')
    over_capacity = fields.Boolean(compute='_compute_capacity_text')

    # ------------------------------------------------------------------ defaults
    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        Order = self.env[ORDER]
        order = Order.browse(res.get('order_id')).exists() if res.get('order_id') else _active(self.env, ORDER)[:1]
        if order:
            res['order_id'] = order.id
            res.update(self._defaults_for(order, res))
        return res

    def _defaults_for(self, order, given=None):
        """Rows for every patient still on the plate."""
        given = given or {}
        vals = {}
        if 'line_ids' not in given:
            vals['line_ids'] = self._rows(order)
        return vals

    @api.model
    def _rows(self, order):
        """Every patient printed - except an arch already lost mid-print, which stays lost."""
        rows = []
        for line in order.line_ids.filtered(lambda l: l.result == 'pending'):
            lost = line.lost_part
            rows.append(Command.create({
                'line_id': line.id, 'ok': not lost, 'upper_ok': lost != 'upper', 'lower_ok': lost != 'lower',
                'failed_part': lost or False, 'reason': line.failure_reason if lost else False}))
        return rows

    # ------------------------------------------------------------------ computes
    @api.depends('line_ids.ok', 'line_ids.failed_part', 'line_ids.hold', 'line_ids.lost_part', 'mode')
    def _compute_failed(self):
        for wiz in self:
            failed = wiz.line_ids.filtered(lambda r: not r.ok)
            wiz.failed_count = len(failed)
            wiz.failed_models = sum(len(r._lost_arches()) for r in failed)
            wiz.replate_models = sum(r._replate_models(wiz.mode) for r in failed if not r.hold)

    @api.depends('order_id', 'replate_models')
    def _compute_suggestion(self):
        Order = self.env[ORDER]
        for wiz in self:
            order = wiz.order_id
            plate = Order._suggest_plate(wiz.replate_models, machine=order.printer_id, exclude=order,
                                         company=order.company_id) if order and wiz.replate_models else Order
            wiz.suggested_order_id = plate
            if plate:
                capacity, used = Order._plate_room(plate)
                wiz.suggest_text = ' · '.join([
                    plate.name,
                    _("planned on %s", plate.printer_id.code) if plate.printer_id else _("machine picked at Start"),
                    _("%(used)d of %(capacity)d models", used=used, capacity=capacity),
                    _("sliced already: slice it again") if plate.slice_filename else _("not sliced yet")])
            elif wiz.replate_models:
                wiz.suggest_text = _("No plate being plated has room: a new plate, its machine picked at Start.")
            else:
                wiz.suggest_text = False
            printer = order.printer_id
            wiz.same_text = _("%s is not in service: the new plate stays unplanned, its machine picked at Start.",
                              printer.display_name) if printer and printer.state != 'ready' else False

    def _fill_target(self):
        """(the plate being plated the models join, or None; the machine of a new plate, or None)."""
        if self.replate_mode == 'auto' and self.suggested_order_id:
            return self.suggested_order_id, None
        if self.replate_mode == 'existing' and self.replate_order_id:
            return self.replate_order_id, None
        if self.replate_mode == 'same' and self.order_id.printer_id.state == 'ready':
            return None, self.order_id.printer_id
        return None, None

    @api.depends('fill', 'replate', 'replate_mode', 'replate_order_id', 'suggested_order_id', 'replate_models',
                 'order_id')
    def _compute_fill(self):
        Order = self.env[ORDER]
        for wiz in self:
            if not (wiz.fill and wiz.replate and wiz.replate_models and wiz.order_id):
                wiz.fill_line_ids = self.env['lab.scan.line']
                continue
            plate, printer = wiz._fill_target()
            capacity, used = Order._plate_room(plate=plate, printer=printer)
            exclude = wiz.order_id.line_ids.scan_line_id | (plate.line_ids.scan_line_id if plate
                                                            else self.env['lab.scan.line'])
            wiz.fill_line_ids = _fill_candidates(wiz.env, capacity - used - wiz.replate_models, exclude,
                                                 wiz.order_id.company_id)

    @api.depends('fill_line_ids', 'replate_models', 'replate_mode', 'replate_order_id', 'suggested_order_id',
                 'fill', 'replate')
    def _compute_capacity_text(self):
        """"3 re-plated + 4 waiting = 7 of 8 models" - what the next plate will hold."""
        Order = self.env[ORDER]
        for wiz in self:
            if not (wiz.replate and wiz.replate_models and wiz.order_id):
                wiz.capacity_text, wiz.over_capacity = False, False
                continue
            plate, printer = wiz._fill_target()
            capacity, used = Order._plate_room(plate=plate, printer=printer)
            waiting = sum(_models(line) for line in wiz.fill_line_ids) if wiz.fill else 0
            total = used + wiz.replate_models + waiting
            bits = [_("%d on the plate", used)] if plate else []
            bits.append(_("%d re-plated", wiz.replate_models))
            if wiz.fill:
                bits.append(_("%d waiting", waiting))
            wiz.capacity_text = _("%(parts)s = %(total)d of %(capacity)d models", parts=' + '.join(bits),
                                  total=total, capacity=capacity)
            wiz.over_capacity = total > capacity

    # ------------------------------------------------------------------ onchanges
    @api.onchange('code')
    def _onchange_code(self):
        return _take_code(self, 'code', None, 'order_id', order_states=('printing',))

    @api.onchange('order_id')
    def _onchange_order_id(self):
        if self.order_id:
            self.line_ids = [Command.clear()] + self._rows(self.order_id)

    # ------------------------------------------------------------------ buttons
    def _destination(self):
        """Where the re-plated models go: a plate being plated, or a machine for a new
        plate - an empty one when the machine is picked at Start."""
        Printer = self.env[PRINTER]
        if self.replate_mode == 'auto':
            return self.suggested_order_id or Printer
        if self.replate_mode == 'same':
            # a plan only: a machine gone into maintenance leaves the plate unplanned
            return self.order_id.printer_id if self.order_id.printer_id.state == 'ready' else Printer
        if self.replate_mode == 'existing':
            if not self.replate_order_id:
                raise UserError(_("Pick the plate being plated to add the failed models to."))
            return self.replate_order_id
        return Printer

    def _reason_for(self, row):
        return row.reason or row.line_id.failure_reason or self.reason

    def action_confirm(self):
        """Every row's verdict to the order in one go; the failed models re-plated where asked."""
        self.ensure_one()
        if self.mode == 'lost':
            return self.action_record_failure()
        order = self.order_id
        if order.state != 'printing':
            raise UserError(_("%s is not on a machine.", order.name))
        failed = self.line_ids.filtered(lambda r: not r.ok)
        if any(not self._reason_for(row) for row in failed):
            raise UserError(_("Say what went wrong with the failed models — one reason for the plate, "
                              "or a reason on each failed row."))
        results = {}
        for row in self.line_ids:
            if row.ok:
                results[row.line_id.id] = ('ok', False, row.note or False, False)
            else:
                results[row.line_id.id] = ('failed', self._reason_for(row), row.note or False,
                                           row.failed_part or 'all')
        replate_on = self._destination() if self.replate and failed else None
        routes = {row.line_id.scan_line_id.id: None for row in failed if row.hold}
        made = order._plate_off(results, skip_cure=self.skip_cure, replate_on=replate_on, routes=routes,
                                fill=self.fill_line_ids if self.fill and replate_on is not None else None,
                                note=self.note or False)
        return self._open(made)

    def action_record_failure(self):
        """A Model Failed: the unticked models are lost now; the plate goes on."""
        self.ensure_one()
        order = self.order_id
        rows = self.line_ids.filtered(lambda r: not r.ok and r._lost_arches() - set(_arches(r.lost_part)))
        if not rows:
            raise UserError(_("Untick the models that came off the plate."))
        if any(not self._reason_for(row) for row in rows):
            raise UserError(_("Say what went wrong — one reason, or a reason on each row."))
        results = {row.line_id.id: (row.failed_part or 'all', self._reason_for(row), row.note or False)
                   for row in rows}
        replate_on = self._destination() if self.replate and self.replate_models else None
        routes = {row.line_id.scan_line_id.id: None for row in rows if row.hold}
        made = order._fail_lines(results, replate_on=replate_on, routes=routes,
                                 fill=self.fill_line_ids if self.fill and replate_on is not None else None,
                                 note=self.note or False)
        return self._open(made)

    def _open(self, plates):
        if len(plates) == 1:
            return _form(plates)
        if plates:
            return {'type': 'ir.actions.act_window', 'name': _('The plates the models went on'), 'res_model': ORDER,
                    'view_mode': 'list,form', 'views': [(False, 'list'), (False, 'form')],
                    'domain': [('id', 'in', plates.ids)], 'context': {'create': False}}
        return CLOSE


class PrintOrderOffLine(models.TransientModel):
    _name = 'lab.scan.print.order.off.line'
    _description = 'Plate Off — Patient'
    _order = 'id'

    wizard_id = fields.Many2one('lab.scan.print.order.off', required=True, ondelete='cascade')
    line_id = fields.Many2one('lab.scan.print.order.line', string='Plate Line', required=True,
                              ondelete='cascade')
    patient_name = fields.Char(related='line_id.patient_name')
    scan_name = fields.Char(related='line_id.scan_id.name', string='Register')
    partner_id = fields.Many2one(related='line_id.partner_id', string='Doctor / Clinic')
    part = fields.Selection(related='line_id.part')
    model_count = fields.Integer(related='line_id.model_count')
    attempt = fields.Integer(related='line_id.attempt')
    lost_part = fields.Selection(related='line_id.lost_part')
    has_upper = fields.Boolean(compute='_compute_has_arches')
    has_lower = fields.Boolean(compute='_compute_has_arches')
    # One tap per lost model: the Printed toggle is every arch of the patient at once,
    # U and L each arch alone (client, 2026-10-23: "the U or L of one scan will fail").
    ok = fields.Boolean('Printed', default=True)
    upper_ok = fields.Boolean('U', default=True, help="The upper came off printed.")
    lower_ok = fields.Boolean('L', default=True, help="The lower came off printed.")
    failed_part = fields.Selection(FAILED_PARTS, string='Failed Arch', default='all')
    reason = fields.Selection(FAILURE_REASONS, string='Different Reason',
                              help="Blank means the plate's reason.")
    note = fields.Char('Note')
    hold = fields.Boolean('Wait', help="Leave this patient waiting for a plate instead of re-plating them now.")

    @api.depends('part')
    def _compute_has_arches(self):
        for row in self:
            arches = _arches(row.part)
            row.has_upper = 'upper' in arches
            row.has_lower = 'lower' in arches

    @api.onchange('ok')
    def _onchange_ok(self):
        """Printed on is every arch printed (but one already lost mid-print); off, from
        every arch printed, is every arch lost. Off because U or L was unticked leaves
        the arches as they are: Odoo runs this again when _sync_verdict turns Printed
        off for one arch, and unticking the other arch then would lose both."""
        for row in self:
            arches = _arches(row.part)
            if row.ok:
                for arch in arches:
                    row[arch + '_ok'] = row.lost_part != arch
            elif all(row[arch + '_ok'] for arch in arches):
                for arch in arches:
                    row[arch + '_ok'] = False
            row._sync_verdict()

    @api.onchange('upper_ok', 'lower_ok')
    def _onchange_arches(self):
        for row in self:
            if row.lost_part:
                row[row.lost_part + '_ok'] = False
            row._sync_verdict()

    def _sync_verdict(self):
        """The row's verdict from its arches: what Plate Off sends the plate."""
        arches = _arches(self.part)
        lost = [arch for arch in arches if not self[arch + '_ok']]
        self.ok = not lost
        if not lost:
            self.failed_part = 'all'
        elif len(lost) == len(arches):
            self.failed_part = 'all' if self.part == 'both' else self.part
        else:
            self.failed_part = lost[0]

    def _lost_arches(self):
        """The arches this row says are lost - with one already lost mid-print."""
        arches = set(_arches(self.part))
        lost = set(_arches(self.lost_part)) if self.lost_part else set()
        if not self.ok:
            lost |= set(_arches(self.part if self.failed_part in (False, 'all') else self.failed_part))
        return lost & arches

    def _replate_models(self, mode):
        """The models this row puts on another plate now: mid-print, a patient with
        both who lost one is settled at Plate Off, not re-plated yet."""
        lost = self._lost_arches()
        if mode == 'lost' and self.part == 'both' and len(lost) == 1:
            return 0
        return len(lost)


# ------------------------------------------------------------------ Print Failed
class PrintOrderFail(models.TransientModel):
    _name = 'lab.scan.print.order.fail'
    _description = 'Print Failed'

    order_id = fields.Many2one(ORDER, string='Plate', required=True, domain=[('state', '=', 'printing')])
    reason = fields.Selection(FAILURE_REASONS, string='What Went Wrong', required=True)
    note = fields.Text('Details')
    restart = fields.Boolean('Restart the print now', default=True,
                             help="A new plate with the same sliced-file figures, started at once.")
    printer_code = fields.Char('Scan the Sticker',
                               help="Scan the sticker of the machine to restart on, or type its number.")
    printer_id = fields.Many2one(PRINTER, string='Restart On', domain=[('state', '=', 'ready')])
    printer_hint = fields.Char(compute='_compute_printer_hint')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        Order = self.env[ORDER]
        order = Order.browse(res.get('order_id')).exists() if res.get('order_id') else _active(self.env, ORDER)[:1]
        if order:
            res['order_id'] = order.id
            if not res.get('printer_id'):
                res['printer_id'] = self._restart_printer(order).id
        return res

    def _restart_printer(self, order):
        """The same machine again while it is still fit - it is freed by the failure -
        otherwise the free, reliable one."""
        if order.printer_id.state == 'ready':
            return order.printer_id
        return self.env[PRINTER]._suggest(order.company_id)

    @api.depends('printer_id', 'printer_id.running_order_id', 'printer_id.running_order_id.expected_end',
                 'printer_id.queued_count', 'printer_id.model_done_count', 'printer_id.model_failed_count',
                 'printer_id.failure_rate')
    def _compute_printer_hint(self):
        for wiz in self:
            wiz.printer_hint = _printer_hint(wiz.printer_id)

    @api.onchange('printer_code')
    def _onchange_printer_code(self):
        return _take_code(self, 'printer_code', 'printer_id', 'order_id', order_states=('printing',))

    @api.onchange('order_id')
    def _onchange_order_id(self):
        if self.order_id and not self.printer_id:
            self.printer_id = self._restart_printer(self.order_id)

    def action_confirm(self):
        self.ensure_one()
        if self.order_id.state != 'printing':
            raise UserError(_("%s is not on a machine.", self.order_id.name))
        if self.restart and not self.printer_id:
            raise UserError(_("Pick the machine to restart the print on, or untick the restart."))
        new = self.order_id._fail(self.reason, note=self.note or False,
                                  restart_on=self.printer_id if self.restart else None)
        return _form(new) if new else CLOSE
