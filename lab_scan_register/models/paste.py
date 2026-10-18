# -*- coding: utf-8 -*-
"""Registering a run of WhatsApp messages at once.

The front desk catches up on a group a few times a day. They copy the messages out of
WhatsApp (or attach the group's export), paste them here, and get one line per
message: who sent it, when, the patient, what is asked for, the delivery date and the
files. Chat ("do u have a 9mm screw??") and patients already registered are unticked.
Registering makes one register per sender, with a patient line for each of their
messages - the way the scans arrived.
"""
from datetime import timedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError

from . import parsers
from .scan_line import DUPLICATE_WINDOW_DAYS


class ScanPaste(models.TransientModel):
    _name = 'lab.scan.paste'
    _description = 'Paste WhatsApp Messages'

    channel_id = fields.Many2one('lab.scan.channel', string='WhatsApp Group', required=True,
                                 domain="[('kind', '=', 'whatsapp')]")
    text = fields.Text('Messages', help="Select the messages in WhatsApp, copy, and paste them "
                                       "here. An exported chat (.txt) works too.")
    line_ids = fields.One2many('lab.scan.paste.line', 'paste_id', string='Messages Found')
    is_read = fields.Boolean('Read')
    line_count = fields.Integer('Message Count', compute='_compute_counts')
    selected_count = fields.Integer('Selected', compute='_compute_counts')

    @api.depends('line_ids.include')
    def _compute_counts(self):
        for paste in self:
            paste.line_count = len(paste.line_ids)
            paste.selected_count = len(paste.line_ids.filtered('include'))

    def _reopen(self):
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id,
                'view_mode': 'form', 'target': 'new', 'name': _('Paste WhatsApp Messages')}

    def action_read(self):
        self.ensure_one()
        messages = parsers.split_chat(self.text)
        if not messages:
            raise UserError(_("Nothing to read — paste the messages first."))
        Scan = self.env['lab.scan']
        lines = [Command.clear()]
        for message in messages:
            found = parsers.read_chat_message(message)
            when = Scan._local_to_utc(found['when']) if found['when'] else fields.Datetime.now()
            member = self.channel_id._match_member(found['poster'], found['phone'])
            partner = member.partner_id or self.channel_id.partner_id
            already = self._already_registered(found.get('patient'), when)
            note = False
            if already:
                note = _("Already registered in %s", already.scan_id.name)
            elif not found['looks_like_scan']:
                note = _("Looks like chat, not a scan")
            lines.append(Command.create({
                'include': found['looks_like_scan'] and not already,
                'date_received': when,
                'sender_name': found['poster'] or False,
                'sender_phone': found['phone'] or False,
                'member_id': member.id or False,
                'partner_id': partner.id or False,
                'patient_name': found.get('patient', False),
                'description': found.get('appliance', False),
                'date_delivery': found.get('delivery', False),
                'file_names': ', '.join(found['files']) or False,
                'existing_line_id': already.id or False,
                'note': note,
            }))
        self.write({'line_ids': lines, 'is_read': True})
        return self._reopen()

    def _already_registered(self, patient, when):
        patient = (patient or '').strip()
        if not patient:
            return self.env['lab.scan.line']
        window = timedelta(days=DUPLICATE_WINDOW_DAYS)
        return self.env['lab.scan.line'].search([
            ('patient_name', '=ilike', patient), ('scan_id.state', '!=', 'cancel'),
            ('scan_id.date_received', '>=', when - window), ('scan_id.date_received', '<=', when + window),
            ('company_id', '=', self.channel_id.company_id.id)], limit=1)

    def action_register(self):
        """One register per sender, a patient line for each of their messages."""
        self.ensure_one()
        chosen = self.line_ids.filtered('include')
        if not chosen:
            raise UserError(_("Tick at least one message to register."))
        groups = {}
        for line in chosen.sorted('date_received'):
            key = (line.member_id.id or (line.sender_name or line.sender_phone or ''), line.partner_id.id)
            groups.setdefault(key, self.env['lab.scan.paste.line'])
            groups[key] |= line
        vals_list = []
        for lines in groups.values():
            first = lines[0]
            files = [f for f in lines.mapped('file_names') if f]
            vals_list.append({
                'source': 'whatsapp',
                'channel_id': self.channel_id.id,
                'member_id': first.member_id.id or False,
                'sender_name': first.sender_name or False,
                'sender_phone': first.sender_phone or False,
                'partner_id': first.partner_id.id or False,
                'date_received': first.date_received,
                'note': _("Files in the chat: %s", ', '.join(files)) if files else False,
                'line_ids': [Command.create({
                    'patient_name': l.patient_name or False,
                    'description': l.description or False,
                    'date_delivery': l.date_delivery or False,
                    'sequence': i,
                }) for i, l in enumerate(lines, 1)],
            })
        scans = self.env['lab.scan'].create(vals_list)
        action = self.env['ir.actions.act_window']._for_xml_id('lab_scan_register.action_scan')
        action.update({'domain': [('id', 'in', scans.ids)], 'context': {},
                       'views': [(False, 'list'), (False, 'form')],
                       'name': _('%(n)s registers from %(group)s', n=len(scans), group=self.channel_id.name)})
        return action


class ScanPasteLine(models.TransientModel):
    _name = 'lab.scan.paste.line'
    _description = 'Pasted WhatsApp Message'
    _order = 'date_received, id'

    paste_id = fields.Many2one('lab.scan.paste', required=True, ondelete='cascade')
    include = fields.Boolean('Register', default=True)
    date_received = fields.Datetime('Sent On')
    sender_name = fields.Char('Sender')
    sender_phone = fields.Char('Sender Phone')
    member_id = fields.Many2one('lab.scan.channel.member', string='Member')
    partner_id = fields.Many2one('res.partner', string='Doctor / Clinic')
    patient_name = fields.Char('Patient')
    description = fields.Char('Description')
    date_delivery = fields.Date('Delivery Date')
    file_names = fields.Char('Files')
    existing_line_id = fields.Many2one('lab.scan.line', string='Already Registered')
    note = fields.Char('Note')
