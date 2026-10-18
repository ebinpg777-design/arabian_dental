# -*- coding: utf-8 -*-
"""Where a scan came in from - the masters behind the register's three sources.

* **WhatsApp groups**, by the name the lab knows them by, with the people who post
  in them. A lab group like "ARABIAN DENTAL CALICUT" carries many clinics, so the
  clinic is often a property of who posted, not of the group.
* **E-mail sources**: the scanner clouds that notify by mail (Allied Star, Freqty,
  DEXIS), recognised by sender.
* **File transfer platforms**: WeTransfer, Google Drive, Dropbox - recognised by the
  link, and knowing how long their links live.
"""
import re

from odoo import _, api, fields, models

KINDS = [
    ('whatsapp', 'WhatsApp'),
    ('email', 'E-mail'),
    ('transfer', 'File transfer'),
]
ROLES = [
    ('doctor', 'Doctor'),
    ('staff', 'Clinic staff'),
    ('executive', 'Our field executive'),
    ('lab', 'Our lab staff'),
]


def digits(value):
    return re.sub(r'\D', '', value or '')


def patterns(value):
    """The patterns of a recognise-rule, lower-cased: commas, semicolons or new
    lines separate them."""
    return [p.strip().lower() for p in re.split(r'[,;\n]', value or '') if p.strip()]


class ScanChannel(models.Model):
    _name = 'lab.scan.channel'
    _description = 'Scan Channel'
    _order = 'kind, name'

    name = fields.Char(required=True, help="The group, the mailbox or the platform, as the lab calls it.")
    kind = fields.Selection(KINDS, string='Source', required=True, default='whatsapp')
    partner_id = fields.Many2one(
        'res.partner', string='Clinic',
        domain="['|', ('is_company', '=', True), ('is_clinic', '=', True)]",
        help="The clinic this channel belongs to. A scan from it is filled in with "
             "them - unless the person who posted belongs to another clinic.")
    user_id = fields.Many2one('res.users', string='Lab contact',
                              help="Who at the lab watches this group or mailbox.")
    member_ids = fields.One2many('lab.scan.channel.member', 'channel_id', string='Members')
    member_count = fields.Integer(compute='_compute_member_count')
    sender_match = fields.Char(
        'Recognise e-mails from',
        help="A piece of the sender's address, such as 'allied-star.com', or a "
             "whole address. Several can be given, separated by commas. An e-mail "
             "whose sender contains one is registered here by itself.")
    subject_match = fields.Char(
        'Recognise subjects containing',
        help="Words from the e-mail's subject, such as 'New case' or 'Scan'. "
             "Several can be given, separated by commas. An e-mail whose subject "
             "contains one is registered here, when no sender rule took it first.")
    link_match = fields.Char(
        'Recognise links containing',
        help="A piece of the download link, such as 'wetransfer.com/downloads'. A scan "
             "whose link contains it is known to be on this platform; and an e-mail no "
             "sender or subject rule recognises is still registered here when it carries "
             "such a link. Make it the share part of the address "
             "('drive.google.com/drive/folders', not 'google.com'), so the platform's own "
             "newsletters are not taken for scans. Several can be given, separated by commas.")
    link_validity_days = fields.Integer(
        'Links live for (days)',
        help="How long this platform keeps a download. A scan with such a link and "
             "no expiry date is given one counted from when it arrived. 0 = never expires.")
    # Anybody holding a group's invite link can join it, and these groups carry
    # doctors and their patients: the link is for the people who register scans.
    link = fields.Char('Invite Link', groups='lab_scan_register.group_scan_user',
                       help="For a WhatsApp group: its invite link (in WhatsApp, Group info > "
                            "Invite via link > Copy link). The WhatsApp button opens the group "
                            "from it. For a folder or platform: its address.")
    note = fields.Text()
    active = fields.Boolean(default=True)
    color = fields.Integer()
    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company)
    scan_count = fields.Integer(compute='_compute_scan_count')

    _uniq_name = models.Constraint(
        'unique(name, company_id)', 'There is already a channel with this name.')

    def _compute_scan_count(self):
        counts = dict(self.env['lab.scan']._read_group(
            [('channel_id', 'in', self.ids)], ['channel_id'], ['__count']))
        for channel in self:
            channel.scan_count = counts.get(channel, 0)

    @api.depends('member_ids')
    def _compute_member_count(self):
        for channel in self:
            channel.member_count = len(channel.member_ids)

    # ------------------------------------------------------------------ recognising
    @api.model
    def _match_email(self, sender, text, company=None, subject=''):
        """The channel an incoming e-mail belongs to.

        Sender rules first - a pattern found in the sender or in the forwarded
        'From:' line - then subject rules, a pattern found in the subject. Each
        rule may hold several patterns separated by commas; the longest pattern
        that matches wins. Any source: a WeTransfer notice arrives by mail but the
        scan is a file transfer. (client, 2026-10-13)"""
        company = company or self.env.company
        channels = self.search(['|', ('sender_match', '!=', False), ('subject_match', '!=', False),
                                ('company_id', '=', company.id)])
        for field, hay in (('sender_match', ' '.join((sender or '', text or ''))),
                           ('subject_match', subject or '')):
            hay = hay.lower()
            best, best_len = self.browse(), 0
            for channel in channels:
                for needle in patterns(channel[field]):
                    if needle in hay and len(needle) > best_len:
                        best, best_len = channel, len(needle)
            if best:
                return best
        return self.browse()

    @api.model
    def _match_link(self, link, company=None):
        """The platform a download link is on, from its address."""
        if not link:
            return self.browse()
        company = company or self.env.company
        hay = link.lower()
        best, best_len = self.browse(), 0
        for channel in self.search([('link_match', '!=', False), ('company_id', '=', company.id)]):
            for needle in patterns(channel.link_match):
                if needle in hay and len(needle) > best_len:
                    best, best_len = channel, len(needle)
        return best

    def _match_member(self, poster, phone=''):
        """The member of this group who posted: by number when we have one (an unsaved
        contact shows as a number), else by name, ignoring case and stray marks."""
        self.ensure_one()
        number = digits(phone)
        if not number and not re.search(r'[A-Za-z]', poster or ''):
            number = digits(poster)          # an unsaved contact: the name IS the number
        if len(number) >= 10:
            for member in self.member_ids:
                if digits(member.phone)[-10:] == number[-10:]:
                    return member
        key = re.sub(r'[^a-z0-9]', '', (poster or '').lower())
        if key:
            for member in self.member_ids:
                if re.sub(r'[^a-z0-9]', '', (member.name or '').lower()) == key:
                    return member
        return self.env['lab.scan.channel.member']

    def _ensure_member(self, poster, phone=''):
        """Find the poster among the members, or add them: the group's member list
        builds itself as people post, and is corrected by hand when it guesses."""
        self.ensure_one()
        if not poster and not phone:
            return self.env['lab.scan.channel.member']
        member = self._match_member(poster, phone)
        if not member:
            member = self.env['lab.scan.channel.member'].create({
                'channel_id': self.id, 'name': poster or phone,
                'phone': phone or (poster if not re.search(r'[A-Za-z]', poster or '') else False),
                'partner_id': self.partner_id.id or False})
        return member

    # ------------------------------------------------------------------ actions
    def action_view_scans(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('lab_scan_register.action_scan')
        action['domain'] = [('channel_id', '=', self.id)]
        action['context'] = {'default_channel_id': self.id, 'default_source': self.kind}
        return action

    def action_paste_messages(self):
        """Register a run of this group's messages in one go."""
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': _('Paste WhatsApp messages'),
                'res_model': 'lab.scan.paste', 'view_mode': 'form', 'target': 'new',
                'context': {'default_channel_id': self.id}}


class ScanChannelMember(models.Model):
    """Somebody who posts scans in a WhatsApp group, and whose clinic they belong to."""
    _name = 'lab.scan.channel.member'
    _description = 'WhatsApp Group Member'
    _order = 'channel_id, name'

    channel_id = fields.Many2one('lab.scan.channel', string='Group', required=True,
                                 ondelete='cascade', index=True)
    name = fields.Char(required=True, help="As they appear in the group.")
    phone = fields.Char('WhatsApp number')
    role = fields.Selection(ROLES, default='doctor')
    partner_id = fields.Many2one(
        'res.partner', string='Clinic / Doctor',
        domain="['|', ('is_company', '=', True), ('is_clinic', '=', True)]",
        help="The clinic their scans are for. Leave empty for our own executives, who "
             "post on behalf of many.")
    user_id = fields.Many2one('res.users', string='Lab user',
                              help="When the poster is one of our own people.")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(related='channel_id.company_id', store=True)
    scan_count = fields.Integer(compute='_compute_scan_count')

    def _compute_scan_count(self):
        counts = dict(self.env['lab.scan']._read_group(
            [('member_id', 'in', self.ids)], ['member_id'], ['__count']))
        for member in self:
            member.scan_count = counts.get(member, 0)

    @api.depends('name', 'phone')
    def _compute_display_name(self):
        for member in self:
            member.display_name = member.name if not member.phone or member.phone == member.name \
                else '%s (%s)' % (member.name, member.phone)
