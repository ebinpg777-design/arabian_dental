# -*- coding: utf-8 -*-
"""Writing to the doctor on WhatsApp from a register: pick a template, read it,
send it to the WhatsApp group or to the person who posted.

WhatsApp lets a link pre-fill a message only for a one-to-one chat. A group can be
opened from its invite link but not written into, so for a group the message is
copied to the clipboard as the group opens, and is pasted there.
"""
import re
from urllib.parse import quote, urlparse

from odoo import _, api, fields, models
from odoo.exceptions import UserError

TARGETS = [('group', 'WhatsApp group'), ('sender', 'Sender / doctor (direct chat)')]


def group_web_url(link):
    """An invite link (https://chat.whatsapp.com/CODE) as the WhatsApp Web address
    that opens that group (https://web.whatsapp.com/accept?code=CODE), skipping
    WhatsApp's landing page. Anything else is opened as given."""
    link = (link or '').strip()
    parsed = urlparse(link if '://' in link else 'https://' + link)
    if parsed.netloc.lower().endswith('chat.whatsapp.com'):
        code = parsed.path.strip('/').split('/')[0]
        if re.fullmatch(r'[A-Za-z0-9]{10,40}', code or ''):
            return 'https://web.whatsapp.com/accept?code=%s' % code
    return link


class ScanWhatsApp(models.TransientModel):
    _name = 'lab.scan.whatsapp'
    _description = 'WhatsApp Message for a Scan Register'

    scan_id = fields.Many2one('lab.scan', string='Scan Register', required=True, ondelete='cascade')
    channel_id = fields.Many2one(related='scan_id.channel_id', string='Channel')
    group_link = fields.Char('Group Invite Link', compute='_compute_targets')
    sender_number = fields.Char('Sender Number', compute='_compute_targets')
    can_group = fields.Boolean(compute='_compute_targets')
    can_sender = fields.Boolean(compute='_compute_targets')
    target = fields.Selection(TARGETS, string='Send To', required=True, default='group')
    template_id = fields.Many2one('lab.scan.message.template', string='Template')
    message = fields.Text('Message', required=True)
    message_copy = fields.Text('Copy', compute='_compute_message_copy')

    @api.depends('scan_id')
    def _compute_targets(self):
        for wiz in self:
            channel = wiz.scan_id.channel_id
            link = channel.sudo().link if channel.kind == 'whatsapp' else False
            wiz.group_link = link or False
            wiz.sender_number = wiz.scan_id.whatsapp_to or False
            wiz.can_group = bool(link)
            wiz.can_sender = bool(wiz.scan_id.whatsapp_to)

    @api.depends('message')
    def _compute_message_copy(self):
        for wiz in self:
            wiz.message_copy = wiz.message

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        scan = self.env['lab.scan'].browse(vals.get('scan_id') or self.env.context.get('active_id'))
        if scan:
            vals['scan_id'] = scan.id
            has_link = scan.channel_id.kind == 'whatsapp' and scan.channel_id.sudo().link
            vals['target'] = 'group' if has_link or not scan.whatsapp_to else 'sender'
            template = self.env['lab.scan.message.template'].search([], limit=1)
            if template and 'template_id' in fields_list:
                vals['template_id'] = template.id
                vals['message'] = template.render(scan)
        return vals

    @api.onchange('template_id')
    def _onchange_template_id(self):
        if self.template_id and self.scan_id:
            self.message = self.template_id.render(self.scan_id)

    def _target_url(self):
        self.ensure_one()
        if self.target == 'group':
            if not self.group_link:
                raise UserError(_(
                    "%s has no invite link. Add it under Scans › Configuration › WhatsApp Groups "
                    "(in WhatsApp: Group info › Invite via link › Copy link).",
                    self.channel_id.name or _("This group")))
            return group_web_url(self.group_link)
        if not self.sender_number:
            raise UserError(_("There is no WhatsApp number for the sender or the clinic."))
        return 'https://web.whatsapp.com/send?phone=%s&text=%s' % (self.sender_number, quote(self.message or ''))

    def action_send(self):
        """Record it on the register, then hand the browser the address to open and,
        for a group, the text to put on the clipboard."""
        self.ensure_one()
        url = self._target_url()
        scan = self.scan_id
        where = self.channel_id.name if self.target == 'group' else '+' + (self.sender_number or '')
        scan.message_post(body=_("WhatsApp to %(where)s%(tpl)s:\n%(text)s",
                                 where=where,
                                 tpl=(' — ' + self.template_id.name) if self.template_id else '',
                                 text=self.message))
        if not scan.date_acknowledged:
            scan.with_context(scan_workflow=True).write({'date_acknowledged': fields.Datetime.now()})
        return {
            'type': 'ir.actions.client',
            'tag': 'lab_scan_whatsapp_open',
            'params': {'url': url, 'text': self.message, 'copy': self.target == 'group',
                       'where': where},
        }
