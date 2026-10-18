# -*- coding: utf-8 -*-
"""The scan register: one arrival of scans from a doctor, from landing to the orders.

A register is what came in together - one WhatsApp message, one e-mail, one transfer -
from one doctor or clinic. It carries one line per patient, and each line becomes its
own sales order. In between, the scans are confirmed, sent to the printing room,
printed, and only then ordered:

    Draft -> Confirmed -> Sent for Printing -> Print In Progress -> Printed -> Ordered
                                                                          (or Cancelled)

The printing room prints plates, not registers: a print order (print_order.py) holds
several registers' patients on one machine's plate. From Sent for Printing on, the
register's status is rolled up from where its patients' models are (_sync_print_state)
- on a plate, printing, back from a failed one, or off clean - and nothing else moves
it. Confirmed means settled: nothing typed changes again unless the register is reset
to draft, which is possible right up until its orders exist.
"""
from datetime import timedelta

import pytz
from markupsafe import Markup, escape

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Domain
from odoo.tools.mail import html2plaintext

from . import parsers
from .channel import KINDS, digits
from .scan_line import DELIVERY_RISK_DAYS, OPEN, PRIORITIES

STATES = [
    ('draft', 'Draft'),
    ('confirmed', 'Confirmed'),
    ('printing', 'Sent for Printing'),
    ('in_progress', 'Print In Progress'),
    ('printed', 'Printed'),
    ('done', 'Ordered'),
    ('cancel', 'Cancelled'),
]
DOWNLOAD_STATES = [
    ('none', 'No Link'), ('ok', 'Link Live'), ('soon', 'Link Expiring'), ('expired', 'Link Expired')]
EXPIRY_WARNING_DAYS = 2
# What a person types on a register; settled from Confirm until Reset to Draft. The
# system's own bookkeeping (files counted, orders linked, an acknowledgement recorded)
# is not on the list and carries on.
LOCKED_FIELDS = frozenset((
    'source', 'channel_id', 'member_id', 'sender_name', 'sender_phone', 'date_received',
    'partner_id', 'doctor_name', 'clinic_name', 'doctor_email', 'note', 'message_text',
    'ref', 'user_id', 'company_id', 'line_ids',
))
# The files arrive after the case is confirmed - downloaded from the link by the
# printing room and added on the print order, dropped in by the doctor - so they stay
# open until the orders are made from them. (client, 2026-10-13: "files and link
# should be in the print order")
FILE_FIELDS = frozenset(('attachment_ids', 'download_url', 'download_expiry'))
FILES_SETTLED = ('done', 'cancel')
# What a patient line still takes after Confirm: file picks, and the product up to
# the moment its order is made.
LATE_LINE_FIELDS = frozenset(('attachment_ids', 'product_id'))


class Scan(models.Model):
    _name = 'lab.scan'
    _description = 'Scan Register'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_received desc, id desc'

    name = fields.Char('Reference', default='New', readonly=True, copy=False, index=True)
    company_id = fields.Many2one('res.company', string='Company', required=True, index=True,
                                 default=lambda s: s.env.company)
    state = fields.Selection(STATES, string='Status', default='draft', required=True,
                             tracking=True, copy=False, index=True)

    # ------------------------------------------------------------- where from
    source = fields.Selection(KINDS, string='Source', required=True, default='whatsapp',
                              tracking=True, index=True,
                              help="How the scans reached the lab. It decides which details the form asks for.")
    channel_id = fields.Many2one('lab.scan.channel', string='Channel', required=True, tracking=True,
                                 index=True, domain="[('kind', '=', source)]",
                                 help="The WhatsApp group, e-mail source or transfer platform.")
    member_id = fields.Many2one('lab.scan.channel.member', string='Sender', tracking=True,
                                domain="[('channel_id', '=', channel_id)]",
                                help="Who posted it in the group. Their clinic fills the doctor in.")
    sender_name = fields.Char('Sender Name', help="The name or number WhatsApp showed.")
    sender_phone = fields.Char('Sender Phone')
    date_received = fields.Datetime('Received On', default=fields.Datetime.now, required=True,
                                    tracking=True, index=True)
    email_from = fields.Char('Email From', readonly=True)
    email_subject = fields.Char('Email Subject', readonly=True)
    ref = fields.Char('Reference No.', index=True, help="The platform's own case or order number.")

    # ------------------------------------------------------------- who
    partner_id = fields.Many2one('res.partner', string='Doctor / Clinic', tracking=True, index=True,
                                 domain="['|', ('is_company', '=', True), ('is_clinic', '=', True)]")
    doctor_name = fields.Char('Doctor Name', help="As written on the request, when it could not be matched to a contact.")
    clinic_name = fields.Char('Clinic Name', help="As written on the request.")
    doctor_email = fields.Char('Doctor Email')

    # ------------------------------------------------------------- patients
    line_ids = fields.One2many('lab.scan.line', 'scan_id', string='Patients', copy=True)
    patient_count = fields.Integer('Patients', compute='_compute_lines', store=True)
    patient_names = fields.Char('Patient Names', compute='_compute_lines', store=True)
    date_delivery = fields.Date('Delivery Date', compute='_compute_lines', store=True,
                                help="The earliest delivery date asked for on any patient.")
    priority = fields.Selection(PRIORITIES, string='Priority', compute='_compute_lines', store=True)
    order_ids = fields.Many2many('sale.order', compute='_compute_orders', string='Orders')
    order_count = fields.Integer('Order Count', compute='_compute_orders')
    delivery_at_risk = fields.Boolean('Delivery at Risk', compute='_compute_delivery_at_risk',
                                      search='_search_delivery_at_risk')
    duplicate_count = fields.Integer('Duplicates', compute='_compute_duplicate_count')

    # ------------------------------------------------------------- files
    attachment_ids = fields.Many2many('ir.attachment', 'lab_scan_attachment_rel', 'scan_id',
                                      'attachment_id', string='Files', copy=False)
    attachment_count = fields.Integer('File Count', compute='_compute_attachments', store=True)
    attachment_size = fields.Char('Files Size', compute='_compute_attachments', store=True)
    has_attachments = fields.Boolean('Has Files', compute='_compute_attachments', store=True)
    download_url = fields.Char('Download Link')
    download_expiry = fields.Date('Link Expiry', tracking=True)
    download_state = fields.Selection(DOWNLOAD_STATES, string='Link Status',
                                      compute='_compute_download_state', search='_search_download_state')
    download_platform_id = fields.Many2one('lab.scan.channel', string='Link Platform',
                                           compute='_compute_download_platform')
    expiry_warned = fields.Boolean(copy=False)

    # ------------------------------------------------------------- workflow
    note = fields.Text('Notes')
    message_text = fields.Text('WhatsApp Message',
                               help="Paste the WhatsApp message(s) here: each patient becomes a line, "
                                    "and the sender and time are read from WhatsApp's own header.")
    user_id = fields.Many2one('res.users', string='Registered By', default=lambda s: s.env.user,
                              readonly=True, tracking=True)
    confirm_user_id = fields.Many2one('res.users', string='Confirmed By', readonly=True, copy=False)
    date_confirmed = fields.Datetime('Confirmed On', readonly=True, copy=False)
    date_print_start = fields.Datetime('Sent for Printing', readonly=True, copy=False)
    date_in_progress = fields.Datetime('Print Started On', readonly=True, copy=False,
                                       help="When the plate holding this register's models started on its machine.")
    date_printed = fields.Datetime('Printed On', readonly=True, copy=False,
                                   help="When the last of its patients' models came off the plate clean.")

    # ------------------------------------------------------------- 3D printing
    # The register's printing story is its patients' plate lines: one plate holds
    # several registers' patients, and one register's patients can sit on several.
    print_line_ids = fields.One2many('lab.scan.print.order.line', 'scan_id', string='Plate Lines',
                                     readonly=True, copy=False)
    print_order_ids = fields.Many2many('lab.scan.print.order', string='Print Orders', compute='_compute_prints')
    print_order_count = fields.Integer('Plates', compute='_compute_prints')
    current_order_ids = fields.Many2many('lab.scan.print.order', string='Printing On', compute='_compute_prints',
                                         help="The plates on a machine with a patient of this register.")
    open_order_ids = fields.Many2many('lab.scan.print.order', string='Being Plated', compute='_compute_prints',
                                      help="The plates being made with a patient of this register.")
    expected_end = fields.Datetime('Expected Off', compute='_compute_prints')
    printer_id = fields.Many2one('lab.scan.printer', string='Machine', compute='_compute_printer', store=True,
                                 index='btree_not_null',
                                 help="The machine printing this register's models - or the last one that did.")
    print_hours = fields.Float('Machine Time (hours)', compute='_compute_printer', store=True, digits=(8, 2),
                               help="The print time of the plates its models came off clean - shared with the "
                                    "other registers on those plates.")
    failed_print_count = fields.Integer('Failed Models', compute='_compute_printer', store=True,
                                        help="Its patients' models that failed on a plate since it was last "
                                             "confirmed.")
    waiting_count = fields.Integer('Waiting for a Plate', compute='_compute_printer', store=True)
    last_print_failure = fields.Char('Last Failure', compute='_compute_prints',
                                     help="The patient whose model failed last and is not printed yet, and why.")
    print_summary = fields.Char('In Printing', compute='_compute_prints',
                                help="Where its patients' models are, in a line.")
    date_ordered = fields.Datetime('Ordered On', readonly=True, copy=False)
    date_acknowledged = fields.Datetime('Acknowledged On', readonly=True, copy=False)
    age_days = fields.Integer('Days Waiting', compute='_compute_age_days')
    whatsapp_to = fields.Char('Acknowledge To', compute='_compute_whatsapp_to')

    # ------------------------------------------------------------------ computes
    @api.depends('line_ids.patient_name', 'line_ids.date_delivery', 'line_ids.priority')
    def _compute_lines(self):
        for scan in self:
            names = [n for n in scan.line_ids.mapped('patient_name') if n]
            dates = [d for d in scan.line_ids.mapped('date_delivery') if d]
            scan.patient_count = len(scan.line_ids)
            scan.patient_names = ', '.join(names) or False
            scan.date_delivery = min(dates) if dates else False
            scan.priority = '1' if '1' in scan.line_ids.mapped('priority') else '0'

    @api.depends('line_ids.sale_order_id')
    def _compute_orders(self):
        for scan in self:
            scan.order_ids = scan.line_ids.sale_order_id
            scan.order_count = len(scan.order_ids)

    @api.depends('print_line_ids.result', 'print_line_ids.superseded', 'print_line_ids.order_state',
                 'print_line_ids.printer_id', 'print_line_ids.date_start', 'print_line_ids.date_end',
                 'print_line_ids.order_id.duration_hours', 'line_ids.print_state')
    def _compute_printer(self):
        """The machine is the one printing its models now (the latest started), else
        the one its last plate came off; time and failures count since the register
        was last confirmed - a reset starts its printing story afresh."""
        for scan in self:
            live = scan.print_line_ids.filtered(lambda p: not p.superseded and p.result != 'withdrawn')
            printing = live.filtered(lambda p: p.order_state == 'printing').order_id
            closed = live.filtered(lambda p: p.order_state in ('washing', 'done', 'failed')).order_id
            if printing:
                scan.printer_id = printing.sorted(lambda o: (o.date_start, o.id))[-1].printer_id
            elif closed:
                scan.printer_id = closed.sorted(lambda o: (o.date_end or o.date_start, o.id))[-1].printer_id
            else:
                scan.printer_id = False
            clean = live.filtered(lambda p: p.result == 'ok' and p.order_state in ('washing', 'done')).order_id
            scan.print_hours = round(sum(clean.mapped('duration_hours')), 2)
            scan.failed_print_count = len(live.filtered(lambda p: p.result == 'failed'))
            scan.waiting_count = len(scan.line_ids.filtered(lambda l: l.print_state == 'to_plate'))

    @api.depends('print_line_ids.result', 'print_line_ids.superseded', 'print_line_ids.order_state',
                 'print_line_ids.failure_reason', 'print_line_ids.failure_note', 'line_ids.print_state',
                 'line_ids.current_order_id')
    def _compute_prints(self):
        labels = dict(self.env['lab.scan.line']._fields['print_state'].selection)
        reasons = dict(self.env['lab.scan.print.order.line']._fields['failure_reason'].selection)
        for scan in self:
            lines = scan.print_line_ids
            pending = lines.filtered(lambda p: p.result == 'pending')
            scan.print_order_ids = lines.filtered(lambda p: p.result != 'withdrawn').order_id
            scan.print_order_count = len(scan.print_order_ids)
            scan.current_order_ids = pending.filtered(lambda p: p.order_state == 'printing').order_id
            scan.open_order_ids = pending.filtered(lambda p: p.order_state == 'draft').order_id
            ends = [d for d in scan.current_order_ids.mapped('expected_end') if d]
            scan.expected_end = max(ends) if ends else False
            # the failure the desk needs to hear about: a patient still without a clean
            # print whose latest plate lost their model
            failure = False
            for patient in scan.line_ids.filtered(lambda l: l.print_state in ('to_plate', 'plated', 'printing')):
                live = patient._live_print_lines() if isinstance(patient.id, int) else lines.browse()
                failed = live.filtered(lambda p: p.result == 'failed')[-1:]
                if not failed or live.filtered(lambda p: p.result == 'ok' and p.id > failed.id):
                    continue
                what = patient.patient_name or _('A patient')
                if failed.failed_part in ('upper', 'lower'):
                    what = '%s (%s)' % (what, dict(failed._fields['failed_part'].selection)[failed.failed_part].lower())
                where = _("re-plated on %s", patient.current_order_id.name) if patient.current_order_id \
                    else _("waiting for a plate")
                failure = '%s: %s%s · %s' % (what, reasons.get(failed.failure_reason, _('failed')),
                                             (' — %s' % failed.failure_note) if failed.failure_note else '', where)
            scan.last_print_failure = failure
            # "1 printing on 3DP-02 · off 06:40 · 1 waiting for a plate"
            parts = []
            for key, label in labels.items():
                group = scan.line_ids.filtered(lambda l, k=key: l.print_state == k)
                if key == 'none' or not group:
                    continue
                text = '%d %s' % (len(group), label.lower())
                if key == 'printing':
                    machines = group.current_printer_id
                    if machines:
                        text += _(" on %s", ', '.join(machines.mapped('code')))
                    if scan.expected_end:
                        text += _(" · off %s", self.env['lab.scan.print.order']._local_time(scan.expected_end))
                parts.append(text)
            scan.print_summary = ' · '.join(parts) or False

    @api.depends('line_ids.delivery_at_risk')
    def _compute_delivery_at_risk(self):
        for scan in self:
            scan.delivery_at_risk = any(scan.line_ids.mapped('delivery_at_risk'))

    def _search_delivery_at_risk(self, operator, value):
        if operator not in ('=', '!='):
            raise NotImplementedError()
        horizon = fields.Date.context_today(self) + timedelta(days=DELIVERY_RISK_DAYS)
        risky = Domain([('state', 'in', OPEN), ('line_ids.date_delivery', '<=', horizon)])
        return risky if (operator == '=') == bool(value) else ~risky

    @api.depends('line_ids.duplicate_count')
    def _compute_duplicate_count(self):
        for scan in self:
            scan.duplicate_count = len(scan.line_ids.duplicate_line_ids.scan_id - scan)

    @api.depends('attachment_ids', 'attachment_ids.file_size')
    def _compute_attachments(self):
        for scan in self:
            size = sum(scan.attachment_ids.mapped('file_size'))
            scan.attachment_count = len(scan.attachment_ids)
            scan.has_attachments = bool(scan.attachment_ids)
            scan.attachment_size = self._human_size(size) if size else False

    @staticmethod
    def _human_size(size):
        for unit in ('B', 'KB', 'MB', 'GB'):
            if size < 1024 or unit == 'GB':
                return '%.1f %s' % (size, unit) if unit != 'B' else '%d B' % size
            size /= 1024.0

    @api.depends('download_url', 'download_expiry')
    def _compute_download_state(self):
        today = fields.Date.context_today(self)
        for scan in self:
            if not scan.download_url:
                scan.download_state = 'none'
            elif scan.download_expiry and scan.download_expiry < today:
                scan.download_state = 'expired'
            elif scan.download_expiry and scan.download_expiry <= today + timedelta(days=EXPIRY_WARNING_DAYS):
                scan.download_state = 'soon'
            else:
                scan.download_state = 'ok'

    def _search_download_state(self, operator, value):
        """One definition of "expiring", searchable from any filter or tile."""
        if operator not in ('=', '!=', 'in', 'not in'):
            raise NotImplementedError()
        # an 'in' value arrives as an OrderedSet, not a list
        wanted = {value} if isinstance(value, str) or not hasattr(value, '__iter__') else set(value)
        if operator in ('!=', 'not in'):
            wanted = {k for k, _l in DOWNLOAD_STATES} - wanted
        today = fields.Date.context_today(self)
        soon = today + timedelta(days=EXPIRY_WARNING_DAYS)
        parts = []
        if 'none' in wanted:
            parts.append([('download_url', '=', False)])
        if 'expired' in wanted:
            parts.append([('download_url', '!=', False), ('download_expiry', '<', today)])
        if 'soon' in wanted:
            parts.append([('download_url', '!=', False), ('download_expiry', '>=', today),
                          ('download_expiry', '<=', soon)])
        if 'ok' in wanted:
            parts.append(['&', ('download_url', '!=', False),
                          '|', ('download_expiry', '=', False), ('download_expiry', '>', soon)])
        if not parts:
            return [('id', '=', False)]
        return Domain.OR(Domain(part) for part in parts)

    @api.depends('download_url', 'company_id')
    def _compute_download_platform(self):
        Channel = self.env['lab.scan.channel']
        for scan in self:
            scan.download_platform_id = Channel._match_link(scan.download_url, scan.company_id)

    @api.depends('date_received', 'state', 'date_ordered')
    def _compute_age_days(self):
        now = fields.Datetime.now()
        for scan in self:
            end = scan.date_ordered if scan.state == 'done' and scan.date_ordered else now
            scan.age_days = (end - scan.date_received).days if scan.date_received else 0

    @api.depends('member_id.phone', 'sender_phone', 'partner_id')
    def _compute_whatsapp_to(self):
        """The number to acknowledge on: the sender's own, else the clinic's WhatsApp
        number. Never a plain phone - a wa.me link to a landline goes nowhere."""
        has_wa = 'whatsapp_number' in self.env['res.partner']._fields
        for scan in self:
            number = digits(scan.member_id.phone) or digits(scan.sender_phone)
            if not number and has_wa:
                number = digits(scan.partner_id.whatsapp_number)
            scan.whatsapp_to = scan._international(number) if number else False

    def _international(self, number):
        """A 10-digit number gets the company's country code; WhatsApp needs it."""
        if len(number) == 10:
            code = digits(str(self.company_id.country_id.phone_code or ''))
            return (code + number) if code else number
        return number

    @api.depends('name', 'patient_names', 'patient_count')
    def _compute_display_name(self):
        for scan in self:
            first = (scan.patient_names or '').split(', ')[0]
            more = ' +%d' % (scan.patient_count - 1) if scan.patient_count > 1 else ''
            scan.display_name = '%s · %s%s' % (scan.name, first, more) if first else scan.name

    # ------------------------------------------------------------------ onchange
    @api.onchange('source')
    def _onchange_source(self):
        if self.channel_id and self.channel_id.kind != self.source:
            self.channel_id = False
        if self.source != 'whatsapp':
            self.member_id = False

    @api.onchange('channel_id')
    def _onchange_channel_id(self):
        if self.channel_id:
            self.source = self.channel_id.kind
        if self.member_id and self.member_id.channel_id != self.channel_id:
            self.member_id = False
        if self.channel_id and not self.member_id and (self.sender_name or self.sender_phone):
            self.member_id = self.channel_id._match_member(self.sender_name, self.sender_phone)
        if not self.partner_id:
            self.partner_id = self.member_id.partner_id or self.channel_id.partner_id

    @api.onchange('member_id')
    def _onchange_member_id(self):
        if self.member_id:
            self.sender_name = self.member_id.name
            self.sender_phone = self.member_id.phone
            if self.member_id.partner_id and not self.partner_id:
                self.partner_id = self.member_id.partner_id

    @api.onchange('download_url')
    def _onchange_download_url(self):
        """The platform from the link: a WeTransfer link dies in days, so it gets an
        expiry at once rather than when somebody remembers to type one."""
        platform = self.env['lab.scan.channel']._match_link(self.download_url, self.company_id)
        if not platform:
            return
        if self.source == 'transfer' and not self.channel_id and platform.kind == 'transfer':
            self.channel_id = platform
        if not self.download_expiry and platform.link_validity_days:
            start = fields.Datetime.to_datetime(self.date_received or fields.Datetime.now())
            self.download_expiry = start.date() + timedelta(days=platform.link_validity_days)

    @api.onchange('message_text')
    def _onchange_message_text(self):
        """Each pasted message that names a patient becomes a patient line, unless that
        patient is already there; the sender and the time come from WhatsApp's own
        header. Nothing already typed is overwritten."""
        if not self.message_text:
            return
        messages = [parsers.read_chat_message(m) for m in parsers.split_chat(self.message_text)]
        if not messages:
            return
        present = {(n or '').strip().lower() for n in self.line_ids.mapped('patient_name')}
        commands, files = [], []
        for found in messages:
            files += found['files']
            name = (found.get('patient') or '').strip()
            if not (name or found.get('appliance')) or (name and name.lower() in present):
                continue
            present.add(name.lower())
            commands.append(Command.create({
                'patient_name': name or False,
                'description': found.get('appliance') or False,
                'date_delivery': found.get('delivery') or False,
            }))
        if commands:
            self.line_ids = commands
        first = next((m for m in messages if m['poster']), messages[0])
        if first.get('poster') and not self.sender_name:
            self.sender_name = first['poster']
            self.sender_phone = first['phone'] or self.sender_phone
        if first.get('when'):
            self.date_received = self._local_to_utc(first['when'])
        if files:
            listed = _("Files in the chat: %s", ', '.join(files))
            if listed not in (self.note or ''):
                self.note = '\n'.join(filter(None, (self.note, listed)))
        if self.channel_id and not self.member_id and self.sender_name:
            self.member_id = self.channel_id._match_member(self.sender_name, self.sender_phone)
            if self.member_id.partner_id and not self.partner_id:
                self.partner_id = self.member_id.partner_id

    @api.model
    def _lab_tz(self):
        name = (self.env.context.get('tz') or self.env.user.tz
                or self.env.company.partner_id.tz or 'Asia/Kolkata')
        try:
            return pytz.timezone(name)
        except pytz.UnknownTimeZoneError:
            return pytz.timezone('Asia/Kolkata')

    @api.model
    def _local_to_utc(self, naive):
        """A time read off a WhatsApp message is the lab's clock, not UTC."""
        if not naive:
            return False
        return self._lab_tz().localize(naive.replace(tzinfo=None)).astimezone(pytz.utc).replace(tzinfo=None)

    # ------------------------------------------------------------------ crud
    @api.model_create_multi
    def create(self, vals_list):
        Channel = self.env['lab.scan.channel']
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('lab.scan') or 'New'
            channel = Channel.browse(vals.get('channel_id'))
            if channel and not vals.get('source'):
                vals['source'] = channel.kind
            if (channel.kind == 'whatsapp' and not vals.get('member_id')
                    and (vals.get('sender_name') or vals.get('sender_phone'))):
                member = channel._ensure_member(vals.get('sender_name'), vals.get('sender_phone'))
                vals['member_id'] = member.id
                if not vals.get('partner_id') and member.partner_id:
                    vals['partner_id'] = member.partner_id.id
        scans = super().create(vals_list)
        scans._claim_attachments()
        return scans

    def write(self, vals):
        """Confirmed means settled: what was typed stays as it was until somebody
        resets the register to draft. And the status moves only through the buttons:
        a kanban card dragged from Draft to Confirmed would skip every check Confirm
        makes, and one dragged out of Ordered would orphan its orders."""
        workflow = self.env.context.get('scan_workflow')
        if 'state' in vals and not workflow:
            raise UserError(_("A register's status changes with its buttons — Confirm, Send to "
                              "3D Printing, Create Orders, Reset to Draft, Cancel - and, while it "
                              "prints, with its print order's — not by editing it."))
        locked = set(LOCKED_FIELDS.intersection(vals))
        if locked and not workflow:
            settled = self.filtered(lambda s: s.state != 'draft')
            # a confirmed register still takes its patients' file picks and their product
            if ('line_ids' in locked and self._only_late_edits(vals['line_ids'])
                    and not any(s.state in FILES_SETTLED for s in settled)):
                locked.discard('line_ids')
            if settled and locked:
                labels = ', '.join(sorted(self._fields[f].string for f in locked))
                raise UserError(_(
                    "%(scans)s is confirmed, so it can no longer be changed (%(fields)s). "
                    "Reset it to draft first — possible until its orders are made.",
                    scans=', '.join(settled.mapped('name')), fields=labels))
        files = FILE_FIELDS.intersection(vals)
        if files and not workflow:
            settled = self.filtered(lambda s: s.state in FILES_SETTLED)
            if settled:
                raise UserError(_(
                    "%s is ordered or cancelled, so its files and download link are settled.",
                    ', '.join(settled.mapped('name'))))
        res = super().write(vals)
        if 'attachment_ids' in vals:
            self._claim_attachments()
        return res

    @staticmethod
    def _only_late_edits(commands):
        """True when the patient-line commands only change what stays open after
        Confirm: which files a patient has, and the product their order is made of
        (client, 2026-10-14: "the product is mandatory only to create the order, and
        can be edited until then")."""
        for command in commands or []:
            if not isinstance(command, (list, tuple)) or len(command) < 3:
                return False
            if command[0] != 1 or not set(command[2] or {}) <= LATE_LINE_FIELDS:
                return False
        return True

    @api.constrains('source', 'channel_id')
    def _check_channel_source(self):
        labels = dict(KINDS)
        for scan in self:
            if scan.channel_id.kind != scan.source:
                raise ValidationError(_(
                    "%(channel)s is a %(kind)s channel, but this register is marked as received "
                    "by %(source)s.", channel=scan.channel_id.name,
                    kind=labels.get(scan.channel_id.kind), source=labels.get(scan.source)))

    def unlink(self):
        if any(scan.state == 'done' or scan.line_ids.sale_order_id for scan in self):
            raise UserError(_("A register whose orders exist is part of their story; cancel it "
                              "instead of deleting it."))
        return super().unlink()

    # ------------------------------------------------------------------ files
    def _claim_attachments(self):
        """Files uploaded on an unsaved form belong to nobody yet; files that arrive in
        the chatter are not in Files yet. Both are put right: every file of the
        register is in Files and is owned by the register."""
        for scan in self:
            stray = scan.attachment_ids.filtered(
                lambda a: a.res_model != scan._name or a.res_id != scan.id)
            if stray:
                stray.sudo().write({'res_model': scan._name, 'res_id': scan.id})
            posted = self.env['ir.attachment'].search([
                ('res_model', '=', scan._name), ('res_id', '=', scan.id),
                ('res_field', '=', False), ('id', 'not in', scan.attachment_ids.ids)])
            if posted:
                super(Scan, scan.with_context(scan_workflow=True)).write(
                    {'attachment_ids': [Command.link(a.id) for a in posted]})

    def message_post(self, **kwargs):
        message = super().message_post(**kwargs)
        if kwargs.get('attachment_ids') or kwargs.get('attachments'):
            self._claim_attachments()
        return message

    def action_open_link(self):
        self.ensure_one()
        if not self.download_url:
            raise UserError(_("This register has no download link."))
        return {'type': 'ir.actions.act_url', 'url': self.download_url, 'target': 'new'}

    # ------------------------------------------------------------------ workflow
    def _move(self, vals):
        return self.with_context(scan_workflow=True).write(vals)

    def action_confirm(self):
        for scan in self:
            if scan.state != 'draft':
                raise UserError(_("%s is not a draft.", scan.name))
            problems = []
            if not scan.partner_id:
                problems.append(_("the doctor or clinic"))
            if not scan.line_ids:
                problems.append(_("at least one patient"))
            no_name = scan.line_ids.filtered(lambda l: not (l.patient_name or '').strip())
            if no_name:
                problems.append(_("a name for every patient (%s missing)", len(no_name)))
            if problems:
                raise UserError(_("Before %(scan)s can be confirmed it needs %(what)s.",
                                  scan=scan.name, what='; '.join(problems)))
            scan._move({'state': 'confirmed', 'confirm_user_id': self.env.uid,
                        'date_confirmed': fields.Datetime.now()})
        return True

    def _check_printable(self):
        """Confirmed, and every patient's scans attached - or the link to fetch them -
        when it goes to the printing room. (client, 2026-10-14: "files should be
        attached when sending to 3D printing")"""
        for scan in self:
            if scan.state != 'confirmed':
                raise UserError(_("%s must be confirmed before it goes for 3D printing.", scan.name))
            missing = scan.line_ids.filtered(lambda l: not l.has_print_files)
            if missing:
                raise UserError(_(
                    "Attach the scans for %(patients)s - or the download link - before %(scan)s goes for "
                    "3D printing.", patients=', '.join(p or _('a patient') for p in missing.mapped('patient_name')),
                    scan=scan.name))

    def action_send_to_print(self):
        """Over to the 3D printing room, from the register list: the New Print Order
        dialog with the ticked registers' patients on one plate - or spread over the
        free machines, or Queue Only and the room plates them later. Each patient's
        scans are attached right there, in the dialog, and nothing is sent without
        them (_check_printable)."""
        if not self:
            raise UserError(_("Tick the confirmed registers to send for 3D printing."))
        others = self.filtered(lambda s: s.state != 'confirmed')
        if others:
            raise UserError(_("Only confirmed registers go for 3D printing; untick %s.",
                              ', '.join(others.mapped('name'))))
        return {'type': 'ir.actions.act_window', 'name': _('Send to 3D Printing'),
                'res_model': 'lab.scan.print.order.make', 'view_mode': 'form', 'target': 'new',
                'context': {'active_model': 'lab.scan', 'active_ids': self.ids}}

    def _send_to_print(self):
        """The register is in the printing room's hands: Sent for Printing, its
        patients waiting for a plate (or on the one being made for them)."""
        self._check_printable()
        for scan in self:
            scan._move({'state': 'printing', 'date_print_start': fields.Datetime.now()})
            scan.message_post(body=_("Sent for 3D printing."))
        return True

    def _sync_print_state(self):
        """From Sent for Printing on, the register's status comes from its print
        orders, and nothing else moves it: a plate of it printing - or off the
        machine in wash & cure - makes it Print In Progress; every patient's plate
        Printed makes it Printed; anything else is Sent for Printing (a patient waits
        for a plate or is on one being made). The only writer of those three states,
        and a total rule over any mix of patients, so no register is stranded."""
        for scan in self:
            if scan.state not in ('printing', 'in_progress', 'printed'):
                continue
            states = set(scan.line_ids.mapped('print_state'))
            if scan.line_ids and states == {'printed'}:
                # the start is kept: "print in progress" is timed from it to here
                vals = {'state': 'printed', 'date_printed': scan.date_printed or fields.Datetime.now()}
            elif states & {'printing', 'washing'}:
                vals = {'state': 'in_progress', 'date_printed': False}
                if scan.state != 'in_progress' or not scan.date_in_progress:
                    starts = [d for d in scan.print_line_ids.filtered(
                        lambda p: p.result in ('pending', 'ok') and not p.superseded
                        and p.order_state in ('printing', 'washing')).order_id.mapped('date_start') if d]
                    vals['date_in_progress'] = min(starts) if starts else fields.Datetime.now()
            else:
                vals = {'state': 'printing', 'date_in_progress': False, 'date_printed': False}
            changed = {k: v for k, v in vals.items() if scan[k] != v}
            if changed:
                scan._move(changed)
        return True

    # ------------------------------------------------------------------ the plates
    def _open_orders(self, orders, name):
        return {'type': 'ir.actions.act_window', 'name': name, 'res_model': 'lab.scan.print.order',
                'view_mode': 'list,form', 'domain': [('id', 'in', orders.ids)], 'context': {'create': False}}

    def action_view_files(self):
        """The register's files, wherever they were added - the chatter, an e-mail,
        or a print order's Files tab."""
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': _('Files — %s', self.name),
                'res_model': 'ir.attachment', 'view_mode': 'kanban,list,form',
                'domain': [('id', 'in', self.attachment_ids.ids)], 'context': {'create': False}}

    def action_view_print_orders(self):
        self.ensure_one()
        orders = self.print_order_ids
        if len(orders) == 1:
            return {'type': 'ir.actions.act_window', 'res_model': 'lab.scan.print.order',
                    'res_id': orders.id, 'view_mode': 'form'}
        return self._open_orders(orders, _('Plates — %s', self.name))

    def action_cancel(self):
        PrintOrder = self.env['lab.scan.print.order']
        for scan in self:
            if scan.line_ids.sale_order_id:
                raise UserError(_("%s already has orders — cancel the orders, not the register.",
                                  scan.name))
            PrintOrder._withdraw_lines(scan.line_ids, _("Register %s cancelled", scan.name))
            scan._move({'state': 'cancel', 'date_in_progress': False})
        return True

    def action_reset(self):
        """Back to draft, and editable again - right up until the orders are made. Its
        patients come off any plate (a printing one goes on for the others), and its
        earlier plates stop saying where its patients are: the machine's record keeps
        them, the register starts afresh."""
        PrintOrder = self.env['lab.scan.print.order']
        for scan in self:
            if scan.state == 'done' or scan.line_ids.sale_order_id:
                raise UserError(_("%s already has orders — it cannot go back to draft.", scan.name))
            PrintOrder._withdraw_lines(scan.line_ids, _("Register %s reset to draft", scan.name))
            scan.print_line_ids.filtered(lambda p: not p.superseded) \
                .with_context(print_workflow=True).write({'superseded': True})
            scan._move({'state': 'draft', 'confirm_user_id': False, 'date_confirmed': False,
                        'date_print_start': False, 'date_in_progress': False, 'date_printed': False})
            scan.message_post(body=_("Reset to draft."))
        return True

    # ------------------------------------------------------------------ orders
    def _shared_attachments(self):
        """Files no patient picked: a bite record, a photo of the whole case - they go
        to every order."""
        self.ensure_one()
        return self.attachment_ids - self.line_ids.attachment_ids

    def _order_values(self, line):
        self.ensure_one()
        lines = [Command.create({'product_id': line.product_id.id, 'product_uom_qty': 1})] \
            if line.product_id else []
        origin = _("Scan %(scan)s, received via %(channel)s on %(date)s",
                   scan=self.name, channel=self.channel_id.name,
                   date=fields.Date.to_string(self.date_received.date()))
        note = '\n'.join(filter(None, (line.note, self.note, origin)))
        vals = {
            'partner_id': self.partner_id.id,
            'origin': self.name,
            'client_order_ref': self.ref or False,
            'patient': line.patient_name,
            'instruction': line.description or False,
            'note': Markup('<p>%s</p>') % Markup('<br/>').join(escape(l) for l in note.split('\n')),
            'scan_id': self.id,
            'scan_line_id': line.id,
            'order_line': lines,
            # a scanned case, in the field the order form already has for it. Not
            # 3D Model Print: Ortho relabelled that flag "Scanned Order", the lab's
            # still means a printed model.
            'scanned_impression': 'Yes',
        }
        if line.date_delivery:
            vals['date_due'] = line.date_delivery
        if line.priority == '1':
            priority = self.env['sale.order']._fields.get('priority')
            keys = [k for k, _l in (priority.selection if priority and isinstance(priority.selection, list) else [])]
            if 'urgent' in keys:
                vals['priority'] = 'urgent'
        return vals

    def action_create_orders(self):
        """One order per patient, each with its own files and the shared ones. Only
        once the models are printed."""
        self.ensure_one()
        if self.state != 'printed':
            raise UserError(_("%s must be printed before its orders are made.", self.name))
        # the product is what the order is made of: needed here, and not before
        # (client, 2026-10-14)
        no_product = self.line_ids.filtered(lambda l: not l.sale_order_id and not l.product_id)
        if no_product:
            raise UserError(_("Pick a product for %(patients)s before the orders of %(scan)s are made.",
                              patients=', '.join(l.patient_name or _('a patient') for l in no_product),
                              scan=self.name))
        shared = self._shared_attachments()
        for line in self.line_ids.filtered(lambda l: not l.sale_order_id):
            order = self.env['sale.order'].create(self._order_values(line))
            for att in line.attachment_ids | shared:
                att.copy({'res_model': 'sale.order', 'res_id': order.id})
            line.with_context(scan_workflow=True).write({'sale_order_id': order.id})
            # the plate the patient's models came off, so the order can be traced to
            # its machine and resin batch
            printed = line.print_line_ids.filtered(lambda p: p.result == 'ok' and not p.superseded)
            if printed:
                order.print_order_id = printed.sorted('id')[-1].order_id
            order.message_post(body=_("Made from scan register %s.", self._get_html_link()))
        self._move({'state': 'done', 'date_ordered': fields.Datetime.now()})
        orders = self.line_ids.sale_order_id
        self.message_post(body=Markup(_("Orders created: %s")) % Markup(', ').join(
            o._get_html_link() for o in orders))
        return self.action_view_orders()

    def action_view_orders(self):
        self.ensure_one()
        orders = self.line_ids.sale_order_id
        action = {'type': 'ir.actions.act_window', 'name': _('Orders — %s', self.name),
                  'res_model': 'sale.order', 'context': {'create': False}}
        if len(orders) == 1:
            action.update(view_mode='form', res_id=orders.id)
        else:
            action.update(view_mode='list,form', domain=[('id', 'in', orders.ids)])
        return action

    def action_view_duplicates(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': _('Also arrived'),
                'res_model': 'lab.scan', 'view_mode': 'list,form',
                'domain': [('id', 'in', (self | self.line_ids.duplicate_line_ids.scan_id).ids)]}

    # ------------------------------------------------------------------ WhatsApp
    def action_open_whatsapp(self):
        """Pick a template, read it, and send it to the group or the sender."""
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': _('WhatsApp Message'),
                'res_model': 'lab.scan.whatsapp', 'view_mode': 'form', 'target': 'new',
                'context': {'default_scan_id': self.id}}

    # ------------------------------------------------------------------ e-mail in
    @api.model
    def _match_partner(self, parsed, channel):
        """Who sent it, in this order of trust: the channel's own clinic, an e-mail
        address we know, then a clinic or doctor name that matches exactly one contact.
        Anything less certain is left for a person."""
        Partner = self.env['res.partner']
        if channel.partner_id:
            return channel.partner_id
        email = parsed.get('doctor_email')
        if email:
            found = Partner.search([('email', '=ilike', email)], limit=2)
            if len(found) == 1:
                return found.commercial_partner_id if found.is_company is False and found.commercial_partner_id.is_company else found
        for key in ('clinic_name', 'doctor_name'):
            name = parsed.get(key)
            if not name:
                continue
            needle = name
            for prefix in ('Dr.', 'Dr ', 'DR.', 'DR '):
                if needle.startswith(prefix):
                    needle = needle[len(prefix):].strip()
            # Freqty writes "BRIGHTSMILE TOOTH AFFAIR PRIVATE LTD VKM001017": the clinic
            # plus its own customer code, which no contact carries. Try as written,
            # then with the trailing coded words dropped.
            words = needle.split()
            while words and any(ch.isdigit() for ch in words[-1]):
                words.pop()
            for candidate in dict.fromkeys((needle, ' '.join(words))):
                if len(candidate) < 4:
                    continue
                found = Partner.search([('name', 'ilike', candidate)], limit=2)
                if len(found) == 1:
                    return found
                if len(found) > 1:
                    break
        return Partner

    @api.model
    def message_new(self, msg_dict, custom_values=None):
        """A notification from a scanner cloud, forwarded by the lab, becomes a register.

        Read for the platform, the patient, the doctor, the case number, the download
        link and when it dies. What cannot be read is left blank, and the mail itself
        is in the chatter for whoever fills it in. Core expects the record back, not
        the id its docstring promises."""
        subject = msg_dict.get('subject') or ''
        text = html2plaintext(msg_dict.get('body') or '')
        email_from = msg_dict.get('email_from') or ''
        parsed = parsers.parse_email(subject, text, email_from)
        company = self.env.company
        if custom_values and custom_values.get('company_id'):
            company = self.env['res.company'].browse(custom_values['company_id'])
        Channel = self.env['lab.scan.channel']
        channel, link = self._recognise_mail(parsed, text, company)
        if link:
            parsed['link'] = link
        channel = channel or \
            self.env.ref('lab_scan_register.channel_email_other', raise_if_not_found=False) or \
            Channel.search([('kind', '=', 'email'), ('company_id', '=', company.id)], limit=1)
        if not channel:
            channel = Channel.create({'name': _('E-mail'), 'kind': 'email', 'company_id': company.id})
        partner = self._match_partner(parsed, channel)
        if not parsed.get('link_expires') and parsed.get('link'):
            platform = Channel._match_link(parsed['link'], company)
            if platform.link_validity_days:
                parsed['link_expires'] = fields.Date.context_today(self) + timedelta(
                    days=platform.link_validity_days)
        notes = parsed.get('notes', '')
        files = parsed.get('file_names')
        if files:
            notes = '\n'.join(filter(None, (notes, _("Files: %s", ', '.join(files)))))
        lines = []
        if parsed.get('patient') or parsed.get('appliance'):
            lines.append(Command.create({'patient_name': parsed.get('patient', False),
                                         'description': parsed.get('appliance', False),
                                         'date_delivery': parsed.get('delivery', False)}))
        vals = dict(custom_values or {})
        vals.update({
            'name': 'New',
            'company_id': company.id,
            'source': channel.kind,
            'channel_id': channel.id,
            'partner_id': partner.id or False,
            'email_from': parsed.get('sender') or email_from,
            'email_subject': subject,
            'doctor_name': parsed.get('doctor_name', False),
            'doctor_email': parsed.get('doctor_email', False),
            'clinic_name': parsed.get('clinic_name', False),
            'ref': parsed.get('external_ref', False),
            'download_url': parsed.get('link', False),
            'download_expiry': parsed.get('link_expires', False),
            'note': notes or False,
            'line_ids': lines,
            'user_id': False,
        })
        received = msg_dict.get('date')
        if received:
            try:
                vals['date_received'] = fields.Datetime.to_datetime(received)
            except (ValueError, TypeError):
                pass
        return self.create(vals)

    @api.model
    def _mail_source(self, msg_dict, company=None):
        """The e-mail source an incoming mail would be registered under by its
        sender or subject, or nothing - read the way message_new reads it."""
        subject = msg_dict.get('subject') or ''
        text = html2plaintext(msg_dict.get('body') or '')
        parsed = parsers.parse_email(subject, text, msg_dict.get('email_from') or '')
        return self._recognise_mail(parsed, text, company)[0]

    @api.model
    def _recognise_mail(self, parsed, text, company=None):
        """(channel, link): which source an e-mail belongs to, and the download link
        that says so. The one reading shared by the mail-server filter and by
        message_new, so the two can never disagree about a mail.

        A sender rule, then a subject rule (an e-mail source); failing both, a link
        to a known platform - which is how a share from Google Drive, Dropbox or any
        platform added under File Transfer Platforms is recognised when it arrives
        from someone the lab has no rule for. (client, 2026-10-13)"""
        Channel = self.env['lab.scan.channel']
        channel = Channel._match_email(parsed.get('sender', ''), text, company,
                                       subject=parsed.get('subject', ''))
        if channel:
            platform = Channel._match_link(parsed.get('link'), company)
            if not platform:
                # the parser took the first link; prefer one the platform owns
                for url in parsers.all_urls(text):
                    if Channel._match_link(url, company) == channel:
                        return channel, url
            return channel, parsed.get('link')
        for url in [parsed.get('link')] + parsers.all_urls(text):
            platform = Channel._match_link(url, company)
            if platform:
                return platform, url
        return Channel, parsed.get('link')

    # ------------------------------------------------------------------ cron
    @api.model
    def _cron_watch_links(self):
        """A link about to die with no files in yet is a scan about to be lost. Say so
        once, as an activity on whoever registered it, before it is too late."""
        today = fields.Date.context_today(self)
        horizon = today + timedelta(days=EXPIRY_WARNING_DAYS)
        self.search([('state', 'in', OPEN)])._claim_attachments()
        due = self.search([('state', 'in', OPEN), ('download_url', '!=', False),
                           ('download_expiry', '!=', False), ('download_expiry', '<=', horizon),
                           ('expiry_warned', '=', False), ('has_attachments', '=', False)])
        for scan in due:
            user = scan.user_id or scan.create_uid
            when = _("expired on %s") if scan.download_expiry < today else _("expires on %s")
            scan.activity_schedule(
                'mail.mail_activity_data_todo',
                date_deadline=min(scan.download_expiry, today),
                user_id=user.id,
                summary=_("Download the scan files before the link dies"),
                note=_("The download link for %(patients)s (%(scan)s) %(when)s and no files "
                       "have been attached yet.",
                       patients=scan.patient_names or scan.name, scan=scan.name,
                       when=when % fields.Date.to_string(scan.download_expiry)))
            scan.with_context(scan_workflow=True).write({'expiry_warned': True})
        return len(due)
