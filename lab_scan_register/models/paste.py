# -*- coding: utf-8 -*-
"""Registering a run of WhatsApp messages at once.

The front desk catches up on a group a few times a day. Instead of one form per scan,
they copy the messages out of WhatsApp (or attach the group's export), paste them
here, and get one line per message: who posted, when, the patient, what is asked
for, the delivery date and the files. Chat ("do u have a 9mm screw??") and scans
already registered are unticked; everything else is registered in one click, and the
people who posted join the group's member list as they go.
"""
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from . import parsers
from .scan import DUPLICATE_WINDOW_DAYS


class ScanPaste(models.TransientModel):
    _name = 'lab.scan.paste'
    _description = 'Paste WhatsApp Messages'

    channel_id = fields.Many2one('lab.scan.channel', string='WhatsApp group', required=True,
                                 domain="[('kind', '=', 'whatsapp')]")
    text = fields.Text('Messages', help="Select the messages in WhatsApp, copy, and paste them here. "
                                       "An exported chat (.txt) works too.")
    line_ids = fields.One2many('lab.scan.paste.line', 'paste_id', string='Found')
    read_done = fields.Boolean()
    line_count = fields.Integer(compute='_compute_counts')
    selected_count = fields.Integer(compute='_compute_counts')

    @api.depends('line_ids.include')
    def _compute_counts(self):
        for paste in self:
            paste.line_count = len(paste.line_ids)
            paste.selected_count = len(paste.line_ids.filtered('include'))

    def _reopen(self):
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id,
                'view_mode': 'form', 'target': 'new', 'name': _('Paste WhatsApp messages')}

    def action_read(self):
        self.ensure_one()
        messages = parsers.split_chat(self.text)
        if not messages:
            raise UserError(_("Nothing to read — paste the messages first."))
        Scan = self.env['lab.scan']
        lines = [(5, 0, 0)]
        for message in messages:
            found = parsers.read_chat_message(message)
            when = Scan._local_to_utc(found['when']) if found['when'] else fields.Datetime.now()
            member = self.channel_id._match_member(found['poster'], found['phone'])
            partner = member.partner_id or self.channel_id.partner_id
            already = self._already_registered(found.get('patient'), when)
            note = ''
            if already:
                note = _("Already registered as %s", already.name)
            elif not found['looks_like_scan']:
                note = _("Looks like chat, not a scan")
            lines.append((0, 0, {
                'include': found['looks_like_scan'] and not already,
                'received_on': when,
                'posted_by': found['poster'],
                'posted_phone': found['phone'],
                'member_id': member.id or False,
                'partner_id': partner.id or False,
                'patient': found.get('patient', False),
                'appliance': found.get('appliance', False),
                'delivery_requested': found.get('delivery', False),
                'files': ', '.join(found['files']) or False,
                'existing_scan_id': already.id or False,
                'note': note or False,
            }))
        self.write({'line_ids': lines, 'read_done': True})
        return self._reopen()

    def _already_registered(self, patient, when):
        patient = (patient or '').strip()
        if not patient:
            return self.env['lab.scan']
        window = timedelta(days=DUPLICATE_WINDOW_DAYS)
        return self.env['lab.scan'].search([
            ('patient', '=ilike', patient), ('state', '!=', 'cancelled'),
            ('received_on', '>=', when - window), ('received_on', '<=', when + window),
            ('company_id', '=', self.channel_id.company_id.id)], limit=1)

    def action_register(self):
        self.ensure_one()
        chosen = self.line_ids.filtered('include')
        if not chosen:
            raise UserError(_("Tick at least one message to register."))
        vals_list = []
        for line in chosen:
            notes = _("Files in the chat: %s", line.files) if line.files else False
            vals_list.append({
                'source': 'whatsapp',
                'channel_id': self.channel_id.id,
                'member_id': line.member_id.id or False,
                'posted_by': line.posted_by or False,
                'posted_phone': line.posted_phone or False,
                'partner_id': line.partner_id.id or False,
                'patient': line.patient or False,
                'appliance': line.appliance or False,
                'delivery_requested': line.delivery_requested or False,
                'received_on': line.received_on,
                'notes': notes,
            })
        scans = self.env['lab.scan'].create(vals_list)
        action = self.env['ir.actions.act_window']._for_xml_id('lab_scan_register.action_scan')
        action.update({'domain': [('id', 'in', scans.ids)], 'context': {},
                       'view_mode': 'list,kanban,form', 'views': [(False, 'list'), (False, 'kanban'), (False, 'form')],
                       'name': _('%(n)s scans registered from %(group)s', n=len(scans),
                                 group=self.channel_id.name)})
        return action



class ScanPasteLine(models.TransientModel):
    _name = 'lab.scan.paste.line'
    _description = 'Pasted WhatsApp Message'
    _order = 'received_on, id'

    paste_id = fields.Many2one('lab.scan.paste', required=True, ondelete='cascade')
    include = fields.Boolean('Register', default=True)
    received_on = fields.Datetime('Sent')
    posted_by = fields.Char('From')
    posted_phone = fields.Char()
    member_id = fields.Many2one('lab.scan.channel.member', string='Member')
    partner_id = fields.Many2one('res.partner', string='Clinic / Doctor')
    patient = fields.Char()
    appliance = fields.Char('Asked for')
    delivery_requested = fields.Date('Delivery')
    files = fields.Char()
    existing_scan_id = fields.Many2one('lab.scan', string='Already registered')
    note = fields.Char()
