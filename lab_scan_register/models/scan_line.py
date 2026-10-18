# -*- coding: utf-8 -*-
"""One patient on a scan register, and the order made for them.

A clinic sends three patients' scans in one message, or one e-mail carries a case
for each of two patients: the register is the arrival, a line is a patient, and
each line becomes its own sales order.
"""
from datetime import timedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError

from . import parsers

PRIORITIES = [('0', 'Normal'), ('1', 'Urgent')]
ARCHES = [('both', 'Upper & Lower'), ('upper', 'Upper'), ('lower', 'Lower')]
DUPLICATE_WINDOW_DAYS = 7
DELIVERY_RISK_DAYS = 2
OPEN = ('draft', 'confirmed', 'printing', 'in_progress', 'printed')
# Where a patient's models are, from Sent for Printing on - read off their plate lines.
PRINT_STATES = [
    ('none', 'Not in Printing'),
    ('to_plate', 'Waiting for a Plate'),
    ('plated', 'On a Plate'),
    ('printing', 'Printing'),
    ('washing', 'Wash & Cure'),
    ('printed', 'Printed'),
]
# What a person types on a line; settled once the register is confirmed.
LINE_LOCKED_FIELDS = frozenset((
    'patient_name', 'description', 'date_delivery', 'priority',
    'note', 'scan_id', 'sequence', 'arch',
))


class ScanLine(models.Model):
    _name = 'lab.scan.line'
    _description = 'Scan Register Patient'
    _order = 'scan_id desc, sequence, id'

    scan_id = fields.Many2one('lab.scan', string='Scan Register', required=True,
                              ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one(related='scan_id.company_id', store=True, index=True)
    state = fields.Selection(related='scan_id.state', string='Status')
    partner_id = fields.Many2one(related='scan_id.partner_id', string='Doctor / Clinic')
    date_received = fields.Datetime(related='scan_id.date_received', string='Received On')

    patient_name = fields.Char('Patient', index=True)
    description = fields.Char('Description', help="What the doctor asked for, in their words.")
    # what the patient's order is made of: needed when that order is made, open
    # until then - the desk often learns the appliance after the scan arrives
    # (client, 2026-10-14)
    product_id = fields.Many2one('product.product', string='Product',
                                 domain="[('sale_ok', '=', True)]",
                                 help="What the patient's order is made of. Needed to create the order; "
                                      "can be changed until then.")
    date_delivery = fields.Date('Delivery Date')
    priority = fields.Selection(PRIORITIES, string='Priority', default='0')
    note = fields.Char('Note')
    attachment_ids = fields.Many2many(
        'ir.attachment', 'lab_scan_line_attachment_rel', 'line_id', 'attachment_id',
        string='Files',
        help="This patient's files, picked from the register's. Files no patient picks "
             "are shared and go to every order.")
    sale_order_id = fields.Many2one('sale.order', string='Order', readonly=True, copy=False,
                                    index='btree_not_null')
    arch = fields.Selection(ARCHES, string='Arch', default='both', required=True,
                            help="What is to be printed for this patient: both arches are two models on "
                                 "the plate, one arch is one.")

    # ------------------------------------------------------------- the plates
    print_line_ids = fields.One2many('lab.scan.print.order.line', 'scan_line_id', string='Plates',
                                     readonly=True, copy=False)
    print_state = fields.Selection(PRINT_STATES, string='Print Status', compute='_compute_print_state',
                                   store=True, index=True,
                                   help="Where this patient's models are: waiting for a plate, on one, "
                                        "printing, in wash & cure, or printed.")
    current_order_id = fields.Many2one('lab.scan.print.order', string='Plate', compute='_compute_print_state',
                                       store=True, index='btree_not_null',
                                       help="The plate the patient is on now, plating or printing.")
    current_printer_id = fields.Many2one(related='current_order_id.printer_id', string='Machine')
    expected_end = fields.Datetime(related='current_order_id.expected_end', string='Expected Off')
    print_attempts = fields.Integer('Print Attempts', compute='_compute_print_state', store=True)
    failed_print_count = fields.Integer('Failed Prints', compute='_compute_print_state', store=True)
    last_failure = fields.Char('Last Failure', compute='_compute_print_state', store=True,
                               help="Why the patient's last plate lost their model, while they wait for "
                                    "another; blank once it printed.")
    # what the patient will be printed from: their scans, the register's shared ones, or the
    # link to fetch them - needed before they go for 3D printing (client, 2026-10-14)
    download_url = fields.Char(related='scan_id.download_url', readonly=False, string='Download Link')
    print_file_count = fields.Integer('Scans', compute='_compute_print_files',
                                      help="The patient's own files and the register's shared ones.")
    has_print_files = fields.Boolean('Ready to Print', compute='_compute_print_files',
                                     help="Scans attached, or at least the link to download them.")

    delivery_in_days = fields.Integer('Days to Delivery', compute='_compute_delivery')
    delivery_at_risk = fields.Boolean('Delivery at Risk', compute='_compute_delivery')
    duplicate_line_ids = fields.Many2many('lab.scan.line', compute='_compute_duplicates',
                                          string='Possible Duplicates')
    duplicate_count = fields.Integer('Duplicates', compute='_compute_duplicates')
    history_count = fields.Integer('Earlier Orders', compute='_compute_history_count')

    # ------------------------------------------------------------------ computes
    @api.depends('attachment_ids', 'scan_id.attachment_ids', 'scan_id.line_ids.attachment_ids', 'download_url')
    def _compute_print_files(self):
        # the line's own link field, not the register's: in the send dialog the link
        # just typed in the row is what tells the row it is ready
        for line in self:
            shared = line.scan_id._shared_attachments() if line.scan_id else line.attachment_ids.browse()
            line.print_file_count = len(line.attachment_ids | shared)
            line.has_print_files = bool(line.print_file_count or line.download_url)

    def _live_print_lines(self):
        """The patient's plate lines that still say something about where they are:
        not taken off, not from before a reset to draft. Oldest first."""
        self.ensure_one()
        return self.print_line_ids.filtered(lambda p: p.result != 'withdrawn' and not p.superseded) \
            .sorted('id')

    @api.depends('scan_id.state', 'print_line_ids.result', 'print_line_ids.superseded',
                 'print_line_ids.order_state', 'print_line_ids.failure_reason')
    def _compute_print_state(self):
        """Where the patient's models are, from their latest plate - the one rule the
        register's own status is rolled up from (Scan._sync_print_state)."""
        reasons = dict(self.env['lab.scan.print.order.line']._fields['failure_reason'].selection)
        for line in self:
            live = line._live_print_lines() if isinstance(line.id, int) else line.print_line_ids.browse()
            latest = live[-1:]
            state, order, failure = 'none', False, False
            scan_state = line.scan_id.state
            if scan_state == 'done':
                state = 'printed' if any(p.result == 'ok' for p in live) else 'none'
            elif scan_state in ('printing', 'in_progress', 'printed'):
                if latest.result == 'pending':
                    order = latest.order_id
                    state = 'plated' if latest.order_state == 'draft' else 'printing'
                elif latest.result == 'ok':
                    state = 'washing' if latest.order_state == 'washing' else 'printed'
                else:
                    state = 'to_plate'
                    if latest.result == 'failed':
                        failure = reasons.get(latest.failure_reason) or _('failed')
            line.print_state = state
            line.current_order_id = order
            line.print_attempts = len(live)
            line.failed_print_count = len(live.filtered(lambda p: p.result == 'failed'))
            line.last_failure = failure

    @api.depends('date_delivery', 'scan_id.state')
    def _compute_delivery(self):
        today = fields.Date.context_today(self)
        for line in self:
            days = (line.date_delivery - today).days if line.date_delivery else 0
            line.delivery_in_days = days
            line.delivery_at_risk = bool(line.date_delivery and line.scan_id.state in OPEN
                                         and days <= DELIVERY_RISK_DAYS)

    @api.depends('patient_name', 'scan_id.partner_id', 'scan_id.date_received', 'scan_id.state')
    def _compute_duplicates(self):
        """The same patient arriving twice within a week - by WhatsApp and again by
        e-mail, say. Flagged, not blocked: a lower-jaw rescan a week on is real."""
        for line in self:
            patient = (line.patient_name or '').strip()
            received = line.scan_id.date_received
            if not patient or not received or not isinstance(line.id, int):
                line.duplicate_line_ids = False
                line.duplicate_count = 0
                continue
            window = timedelta(days=DUPLICATE_WINDOW_DAYS)
            domain = [('id', '!=', line.id), ('company_id', '=', line.scan_id.company_id.id),
                      ('scan_id.state', '!=', 'cancel'), ('patient_name', '=ilike', patient),
                      ('scan_id.date_received', '>=', received - window),
                      ('scan_id.date_received', '<=', received + window)]
            partner = line.scan_id.partner_id
            if partner:
                domain += ['|', ('scan_id.partner_id', '=', partner.id), ('scan_id.partner_id', '=', False)]
            dups = self.search(domain)
            line.duplicate_line_ids = dups
            line.duplicate_count = len(dups)

    def _history_domain(self):
        self.ensure_one()
        domain = [('partner_id', 'child_of', self.scan_id.partner_id.commercial_partner_id.id),
                  ('patient', '=ilike', (self.patient_name or '').strip()), ('state', '!=', 'cancel')]
        if self.sale_order_id:
            domain.append(('id', '!=', self.sale_order_id.id))
        return domain

    @api.depends('patient_name', 'scan_id.partner_id', 'sale_order_id')
    def _compute_history_count(self):
        """Earlier orders for the same patient at the same clinic: a rescan, a remake
        or a second appliance - the order to look at before making this one."""
        SaleOrder = self.env['sale.order']
        for line in self:
            line.history_count = SaleOrder.search_count(line._history_domain()) \
                if line.scan_id.partner_id and (line.patient_name or '').strip() else 0

    @api.depends('patient_name', 'scan_id.name')
    def _compute_display_name(self):
        for line in self:
            line.display_name = '%s · %s' % (line.scan_id.name or '', line.patient_name or _('No patient'))

    # ------------------------------------------------------------------ the lock
    def write(self, vals):
        # a patient's files arrive on the print order, right up until their order is made
        if 'attachment_ids' in vals and not self.env.context.get('scan_workflow'):
            settled = self.filtered(lambda l: l.sale_order_id or l.scan_id.state in ('done', 'cancel'))
            if settled:
                raise UserError(_("%s is ordered or cancelled, so its patients' files are settled.",
                                  ', '.join(settled.scan_id.mapped('name'))))
        if 'product_id' in vals and not self.env.context.get('scan_workflow'):
            ordered = self.filtered(lambda l: l.sale_order_id or l.scan_id.state in ('done', 'cancel'))
            if ordered:
                raise UserError(_("The order of %s is made; its product is settled there.",
                                  ', '.join(ordered.mapped('patient_name'))))
        if LINE_LOCKED_FIELDS.intersection(vals) and not self.env.context.get('scan_workflow'):
            settled = self.filtered(lambda l: l.scan_id.state != 'draft')
            if settled:
                raise UserError(_(
                    "%s is confirmed, so its patients can no longer be changed. Reset it to "
                    "draft first — possible until its orders are made.",
                    ', '.join(settled.scan_id.mapped('name'))))
        res = super().write(vals)
        if 'attachment_ids' in vals:
            # a patient's file is one of the register's files: that is what goes to the
            # orders, and what the chatter and the Files button show
            for scan in self.scan_id:
                missing = self.filtered(lambda l: l.scan_id == scan).attachment_ids - scan.attachment_ids
                if missing:
                    scan.with_context(scan_workflow=True).write(
                        {'attachment_ids': [Command.link(a.id) for a in missing]})
        return res

    @api.onchange('description')
    def _onchange_description(self):
        arch = parsers.guess_arch(self.description)
        if arch:
            self.arch = arch

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # "Upper & Lower Hawley" is two models on the plate, "lower retainer" one
            if 'arch' not in vals and vals.get('description'):
                vals['arch'] = parsers.guess_arch(vals['description']) or 'both'
        if not self.env.context.get('scan_workflow'):
            scans = self.env['lab.scan'].browse([v.get('scan_id') for v in vals_list if v.get('scan_id')])
            settled = scans.filtered(lambda s: s.state != 'draft')
            if settled:
                raise UserError(_("%s is confirmed — reset it to draft to add a patient.",
                                  ', '.join(settled.mapped('name'))))
        return super().create(vals_list)

    def unlink(self):
        if not self.env.context.get('scan_workflow') and any(l.scan_id.state != 'draft' for l in self):
            raise UserError(_("A confirmed register's patients cannot be removed — reset it to draft first."))
        return super().unlink()

    # ------------------------------------------------------------------ actions
    def action_view_history(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': _('Earlier orders — %s', self.patient_name),
                'res_model': 'sale.order', 'view_mode': 'list,form',
                'domain': self._history_domain(), 'context': {'create': False}}

    def action_view_duplicates(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': _('Also arrived — %s', self.patient_name),
                'res_model': 'lab.scan', 'view_mode': 'list,form',
                'domain': [('id', 'in', (self.scan_id | self.duplicate_line_ids.scan_id).ids)]}

    def action_view_order(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'sale.order',
                'res_id': self.sale_order_id.id, 'view_mode': 'form'}

    # ------------------------------------------------------------------ printing
    def _make_dialog(self, name, **context):
        return {'type': 'ir.actions.act_window', 'name': name, 'res_model': 'lab.scan.print.order.make',
                'view_mode': 'form', 'target': 'new',
                'context': dict({'active_model': self._name, 'active_ids': self.ids}, **context)}

    def action_put_on_plate(self):
        """The room ticks the waiting patients that fit one plate."""
        lines = self.filtered(lambda l: l.print_state in ('none', 'to_plate') and not l.sale_order_id
                              and l.scan_id.state in ('confirmed', 'printing', 'in_progress', 'printed'))
        if not lines:
            raise UserError(_("None of these patients is waiting for a plate."))
        return lines._make_dialog(_('New Print Order'))

    def action_print_again(self):
        """A model broke in finishing before its order was made: it goes on a plate again."""
        self.ensure_one()
        if self.print_state not in ('washing', 'printed') or self.sale_order_id or self.scan_id.state == 'done':
            raise UserError(_("Only a printed model whose order is not made yet can be printed again."))
        return self._make_dialog(_('Print Again'))

    def action_view_print_order(self):
        """The patient's plate now - or the last one they were on."""
        self.ensure_one()
        order = self.current_order_id or self.print_line_ids.sorted('id')[-1:].order_id
        if not order:
            raise UserError(_("%s has not been on a plate yet.", self.patient_name or _('This patient')))
        return {'type': 'ir.actions.act_window', 'res_model': 'lab.scan.print.order',
                'res_id': order.id, 'view_mode': 'form'}

    def action_view_scan(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'lab.scan', 'res_id': self.scan_id.id,
                'view_mode': 'form'}
