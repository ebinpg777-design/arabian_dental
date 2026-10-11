# -*- coding: utf-8 -*-
"""The scan register: one row per scan a doctor sent, from arrival to the order.

A scan arrives as an STL in a WhatsApp group with a caption above it, as a link in a
notification from the scanner's cloud, or as a photo of a QR code - and until somebody
typed an order it existed nowhere the lab could count, chase or come back to. Here it
is registered the moment it lands, by hand or by itself from the e-mail, confirmed when
somebody has looked at it, and turned into an order that keeps its files and points
back at it.
"""
from datetime import timedelta
from urllib.parse import quote

import pytz

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.fields import Domain
from odoo.exceptions import UserError, ValidationError
from odoo.tools.mail import html2plaintext

from . import parsers
from .channel import KINDS, digits

STATES = [
    ('new', 'Draft'),
    ('confirmed', 'Confirmed'),
    ('ordered', 'Ordered'),
    ('cancelled', 'Cancelled'),
]
OPEN = ('new', 'confirmed')
LINK_STATES = [
    ('none', 'No link'), ('ok', 'Link live'), ('soon', 'Link expiring'), ('expired', 'Link expired')]
DUPLICATE_WINDOW_DAYS = 7
EXPIRY_WARNING_DAYS = 2
DELIVERY_RISK_DAYS = 2
# What a person types on a scan. Once it is confirmed, none of it changes until it is
# reset to draft; the system's own bookkeeping (files counted, the order linked, an
# acknowledgement recorded) is not in this list and carries on.
LOCKED_FIELDS = frozenset((
    'source', 'channel_id', 'member_id', 'posted_by', 'posted_phone', 'received_on',
    'partner_id', 'doctor_name', 'clinic_name', 'doctor_email', 'patient', 'appliance',
    'product_id', 'notes', 'delivery_requested', 'external_ref', 'pasted_message',
    'link', 'link_expires', 'urgent', 'user_id', 'company_id',
))


class Scan(models.Model):
    _name = 'lab.scan'
    _description = 'Scan Request'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'received_on desc, id desc'

    name = fields.Char(default='New', readonly=True, copy=False, index=True)
    company_id = fields.Many2one('res.company', required=True, index=True,
                                 default=lambda s: s.env.company)
    state = fields.Selection(STATES, default='new', required=True, tracking=True, copy=False,
                             index=True)
    urgent = fields.Boolean(tracking=True)

    # ------------------------------------------------------------- where from
    source = fields.Selection(
        KINDS, required=True, default='whatsapp', tracking=True, index=True,
        help="How the scan reached the lab. It decides which details the form asks for.")
    channel_id = fields.Many2one(
        'lab.scan.channel', string='Received via', required=True, tracking=True,
        domain="[('kind', '=', source)]",
        help="The WhatsApp group, e-mail source or transfer platform it came in on.")
    member_id = fields.Many2one(
        'lab.scan.channel.member', string='Posted by', tracking=True,
        domain="[('channel_id', '=', channel_id)]",
        help="Who posted it in the group. Their clinic fills the doctor in.")
    posted_by = fields.Char('Poster as shown', help="The name or number WhatsApp showed.")
    posted_phone = fields.Char('Poster number')
    acknowledged_on = fields.Datetime(readonly=True, copy=False)
    received_on = fields.Datetime(default=fields.Datetime.now, required=True, tracking=True)
    email_from = fields.Char('Sent by', readonly=True, help="The e-mail it arrived from.")
    email_subject = fields.Char(readonly=True)

    # ------------------------------------------------------------- who and what
    partner_id = fields.Many2one(
        'res.partner', string='Doctor / Clinic', tracking=True, index=True,
        domain="['|', ('is_company', '=', True), ('is_clinic', '=', True)]")
    doctor_name = fields.Char(help="As written on the request, when it could not be matched to a contact.")
    clinic_name = fields.Char(help="As written on the request.")
    doctor_email = fields.Char()
    patient = fields.Char(tracking=True, index=True)
    appliance = fields.Char('Asked for', tracking=True,
                            help="What the doctor wants made, in their words.")
    product_id = fields.Many2one(
        'product.product', string='Product', domain="[('sale_ok', '=', True)]",
        help="The product this maps to, if you want the order to open with a line.")
    notes = fields.Text()
    delivery_requested = fields.Date('Delivery asked for', tracking=True)
    external_ref = fields.Char('Case / order no.', index=True,
                               help="The platform's own number for this case.")
    pasted_message = fields.Text(
        'Paste the message',
        help="Paste the WhatsApp message here and the patient, what is asked for and "
             "the delivery date fill themselves. Nothing you have already typed is "
             "overwritten.")

    # ------------------------------------------------------------- the files
    link = fields.Char('Download link')
    link_expires = fields.Date(tracking=True)
    link_state = fields.Selection(LINK_STATES, compute='_compute_link_state',
                                  search='_search_link_state')
    expiry_warned = fields.Boolean(copy=False)
    file_count = fields.Integer('Files', readonly=True, copy=False)
    file_size = fields.Char('Size', readonly=True, copy=False)
    files_received = fields.Boolean(compute='_compute_files_received', store=True)

    # ------------------------------------------------------------- the order
    sale_order_id = fields.Many2one('sale.order', string='Order', readonly=True, copy=False)
    user_id = fields.Many2one('res.users', string='Registered by', default=lambda s: s.env.user,
                              readonly=True, tracking=True)
    confirmed_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    confirmed_on = fields.Datetime(readonly=True, copy=False)
    age_days = fields.Integer(compute='_compute_age_days', string='Days waiting')
    duplicate_ids = fields.Many2many('lab.scan', compute='_compute_duplicates',
                                     string='Possible duplicates')
    duplicate_count = fields.Integer(compute='_compute_duplicates')
    link_platform_id = fields.Many2one('lab.scan.channel', compute='_compute_link_platform',
                                       string='Link platform')
    delivery_in_days = fields.Integer(compute='_compute_delivery', string='Delivery in (days)')
    delivery_at_risk = fields.Boolean(
        compute='_compute_delivery', search='_search_delivery_at_risk',
        help="Delivery is asked for within two days and the scan is not an order yet.")
    history_count = fields.Integer(compute='_compute_history_count', string='Earlier orders')
    whatsapp_to = fields.Char(compute='_compute_whatsapp_to', string='Acknowledge to')

    # ------------------------------------------------------------------ computes
    @api.depends('link', 'link_expires')
    def _compute_link_state(self):
        today = fields.Date.context_today(self)
        for scan in self:
            if not scan.link:
                scan.link_state = 'none'
            elif scan.link_expires and scan.link_expires < today:
                scan.link_state = 'expired'
            elif scan.link_expires and scan.link_expires <= today + timedelta(days=EXPIRY_WARNING_DAYS):
                scan.link_state = 'soon'
            else:
                scan.link_state = 'ok'

    def _search_link_state(self, operator, value):
        """The filter's date arithmetic done here, not in the browser: a search
        domain is evaluated by py.js, which has no relativedelta or timedelta."""
        if operator not in ('=', '!=', 'in', 'not in'):
            raise NotImplementedError()
        # an 'in' value arrives as an OrderedSet, not a list
        wanted = {value} if isinstance(value, str) or not hasattr(value, '__iter__') else set(value)
        if operator in ('!=', 'not in'):
            wanted = {k for k, _l in LINK_STATES} - wanted
        today = fields.Date.context_today(self)
        soon = today + timedelta(days=EXPIRY_WARNING_DAYS)
        parts = []
        if 'none' in wanted:
            parts.append([('link', '=', False)])
        if 'expired' in wanted:
            parts.append([('link', '!=', False), ('link_expires', '<', today)])
        if 'soon' in wanted:
            parts.append([('link', '!=', False), ('link_expires', '>=', today), ('link_expires', '<=', soon)])
        if 'ok' in wanted:
            parts.append(['&', ('link', '!=', False),
                          '|', ('link_expires', '=', False), ('link_expires', '>', soon)])
        if not parts:
            return [('id', '=', False)]
        # a part is several leaves ANDed; OR-ing them needs the ORM to write the
        # operators, not a '|' dropped in front of a flat list
        return Domain.OR(Domain(part) for part in parts)

    @api.depends('link', 'company_id')
    def _compute_link_platform(self):
        Channel = self.env['lab.scan.channel']
        for scan in self:
            scan.link_platform_id = Channel._match_link(scan.link, scan.company_id)

    @api.depends('delivery_requested', 'state')
    def _compute_delivery(self):
        today = fields.Date.context_today(self)
        for scan in self:
            days = (scan.delivery_requested - today).days if scan.delivery_requested else 0
            scan.delivery_in_days = days
            scan.delivery_at_risk = bool(scan.delivery_requested and scan.state in OPEN
                                         and days <= DELIVERY_RISK_DAYS)

    def _search_delivery_at_risk(self, operator, value):
        if operator not in ('=', '!='):
            raise NotImplementedError()
        horizon = fields.Date.context_today(self) + timedelta(days=DELIVERY_RISK_DAYS)
        risky = Domain([('state', 'in', OPEN), ('delivery_requested', '!=', False),
                        ('delivery_requested', '<=', horizon)])
        return risky if (operator == '=') == bool(value) else ~risky

    def _history_domain(self):
        self.ensure_one()
        domain = [('partner_id', 'child_of', self.partner_id.commercial_partner_id.id),
                  ('patient', '=ilike', (self.patient or '').strip()), ('state', '!=', 'cancel')]
        if self.sale_order_id:
            domain.append(('id', '!=', self.sale_order_id.id))
        return domain

    @api.depends('partner_id', 'patient', 'sale_order_id')
    def _compute_history_count(self):
        """Earlier orders for the same patient at the same clinic: a rescan, a remake
        or a second appliance, and the order to look at before making this one."""
        SaleOrder = self.env['sale.order']
        for scan in self:
            scan.history_count = SaleOrder.search_count(scan._history_domain()) \
                if scan.partner_id and (scan.patient or '').strip() else 0

    @api.depends('member_id.phone', 'posted_phone', 'partner_id')
    def _compute_whatsapp_to(self):
        """The number to acknowledge on: the poster's own, else the clinic's WhatsApp
        number. Never a plain phone - a wa.me link to a landline goes nowhere."""
        has_wa = 'whatsapp_number' in self.env['res.partner']._fields
        for scan in self:
            number = digits(scan.member_id.phone) or digits(scan.posted_phone)
            if not number and has_wa:
                number = digits(scan.partner_id.whatsapp_number)
            scan.whatsapp_to = scan._international(number) if number else False

    def _international(self, number):
        """A 10-digit number gets the company's country code; WhatsApp needs it."""
        if len(number) == 10:
            code = digits(str(self.company_id.country_id.phone_code or ''))
            return (code + number) if code else number
        return number

    @api.depends('name', 'patient')
    def _compute_display_name(self):
        for scan in self:
            scan.display_name = '%s · %s' % (scan.name, scan.patient) if scan.patient else scan.name

    @api.depends('file_count')
    def _compute_files_received(self):
        for scan in self:
            scan.files_received = scan.file_count > 0

    @api.depends('received_on', 'state')
    def _compute_age_days(self):
        now = fields.Datetime.now()
        for scan in self:
            end = scan.confirmed_on if scan.state == 'ordered' and scan.confirmed_on else now
            scan.age_days = (end - scan.received_on).days if scan.received_on else 0

    @api.depends('patient', 'partner_id', 'received_on', 'state')
    def _compute_duplicates(self):
        """The same patient arriving twice within a week - by WhatsApp and again by
        e-mail, say. Flagged, not blocked: a lower-jaw rescan a week on is real."""
        for scan in self:
            patient = (scan.patient or '').strip()
            if not patient or not scan.received_on:
                scan.duplicate_ids = False
                scan.duplicate_count = 0
                continue
            window = timedelta(days=DUPLICATE_WINDOW_DAYS)
            domain = [('id', '!=', scan.id), ('company_id', '=', scan.company_id.id),
                      ('state', '!=', 'cancelled'), ('patient', 'ilike', patient),
                      ('received_on', '>=', scan.received_on - window),
                      ('received_on', '<=', scan.received_on + window)]
            if scan.partner_id:
                domain += ['|', ('partner_id', '=', scan.partner_id.id), ('partner_id', '=', False)]
            dups = self.search(domain) if isinstance(scan.id, int) else self.browse()
            scan.duplicate_ids = dups
            scan.duplicate_count = len(dups)

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
        if self.channel_id and not self.member_id and (self.posted_by or self.posted_phone):
            self.member_id = self.channel_id._match_member(self.posted_by, self.posted_phone)
        if not self.partner_id:
            self.partner_id = self.member_id.partner_id or self.channel_id.partner_id

    @api.onchange('member_id')
    def _onchange_member_id(self):
        if self.member_id:
            self.posted_by = self.member_id.name
            self.posted_phone = self.member_id.phone
            if self.member_id.partner_id and not self.partner_id:
                self.partner_id = self.member_id.partner_id

    @api.onchange('link')
    def _onchange_link(self):
        """Knowing the platform from the link: a WeTransfer link dies in days, so it
        gets an expiry at once rather than when somebody remembers to type one."""
        platform = self.env['lab.scan.channel']._match_link(self.link, self.company_id)
        if not platform:
            return
        if self.source == 'transfer' and not self.channel_id and platform.kind == 'transfer':
            self.channel_id = platform
        if not self.link_expires and platform.link_validity_days:
            start = fields.Datetime.to_datetime(self.received_on or fields.Datetime.now())
            self.link_expires = start.date() + timedelta(days=platform.link_validity_days)

    @api.onchange('pasted_message')
    def _onchange_pasted_message(self):
        """Read the pasted WhatsApp message(s) into the empty fields; never over a
        typed one. With WhatsApp's own header ("[12:36 pm, 11/10/2026] Name: …") the
        poster and the time come too; several messages pasted together are one scan."""
        if not self.pasted_message:
            return
        messages = parsers.split_chat(self.pasted_message)
        if not messages:
            return
        text = '\n'.join(m['text'] for m in messages if m['text'])
        found = parsers.parse_whatsapp(text)
        files = [f for m in messages for f in m['files']]
        first = next((m for m in messages if m['poster']), messages[0])
        if found.get('patient') and not self.patient:
            self.patient = found['patient']
        if found.get('appliance') and not self.appliance:
            self.appliance = found['appliance']
        if found.get('delivery') and not self.delivery_requested:
            self.delivery_requested = found['delivery']
        if first.get('poster') and not self.posted_by:
            self.posted_by = first['poster']
            self.posted_phone = first['phone'] or self.posted_phone
        if first.get('when'):
            self.received_on = self._local_to_utc(first['when'])
        if files:
            listed = _("Files in the chat: %s", ', '.join(files))
            if listed not in (self.notes or ''):
                self.notes = '\n'.join(filter(None, (self.notes, listed)))
        if self.channel_id and not self.member_id and self.posted_by:
            self.member_id = self.channel_id._match_member(self.posted_by, self.posted_phone)
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
                    and (vals.get('posted_by') or vals.get('posted_phone'))):
                member = channel._ensure_member(vals.get('posted_by'), vals.get('posted_phone'))
                vals['member_id'] = member.id
                if not vals.get('partner_id') and member.partner_id:
                    vals['partner_id'] = member.partner_id.id
        scans = super().create(vals_list)
        scans._refresh_files()
        return scans

    @api.constrains('source', 'channel_id')
    def _check_channel_source(self):
        labels = dict(KINDS)
        for scan in self:
            if scan.channel_id.kind != scan.source:
                raise ValidationError(_(
                    "%(channel)s is a %(kind)s channel, but this scan is marked as received "
                    "by %(source)s.", channel=scan.channel_id.name,
                    kind=labels.get(scan.channel_id.kind), source=labels.get(scan.source)))

    def write(self, vals):
        """Confirmed means settled: what was typed stays as it was until somebody
        resets the scan to draft. And the state moves only through the buttons -
        a kanban card dragged from Draft to Confirmed would skip every check that
        Confirm makes, and one dragged back out of Ordered would orphan its order."""
        if 'state' in vals and not self.env.context.get('scan_workflow'):
            raise UserError(_("A scan's status changes with its buttons — Confirm, Create "
                              "Order, Reset to Draft, Cancel — not by editing it."))
        locked = LOCKED_FIELDS.intersection(vals)
        if locked and not self.env.context.get('scan_workflow'):
            settled = self.filtered(lambda s: s.state != 'new')
            if settled:
                labels = ', '.join(sorted(self._fields[f].string for f in locked))
                raise UserError(_(
                    "%(scans)s is confirmed, so it can no longer be changed (%(fields)s). "
                    "Reset it to draft first — possible until its order is made.",
                    scans=', '.join(settled.mapped('name')), fields=labels))
        return super().write(vals)

    def unlink(self):
        if any(scan.state == 'ordered' for scan in self):
            raise UserError(_("A scan that became an order is part of that order's story; "
                              "cancel it instead of deleting it."))
        return super().unlink()

    # ------------------------------------------------------------------ files
    def _attachments(self):
        return self.env['ir.attachment'].search([
            ('res_model', '=', self._name), ('res_id', 'in', self.ids)])

    def _refresh_files(self):
        """The count and size of what is attached, kept on the record so the register
        can be filtered on 'no files yet' - the question the link expiry is about."""
        for scan in self:
            atts = scan._attachments()
            size = sum(atts.mapped('file_size'))
            scan.write({'file_count': len(atts),
                        'file_size': self._human_size(size) if size else False})

    @staticmethod
    def _human_size(size):
        for unit in ('B', 'KB', 'MB', 'GB'):
            if size < 1024 or unit == 'GB':
                return '%.1f %s' % (size, unit) if unit != 'B' else '%d B' % size
            size /= 1024.0

    def message_post(self, **kwargs):
        message = super().message_post(**kwargs)
        if kwargs.get('attachment_ids') or kwargs.get('attachments'):
            self._refresh_files()
        return message

    def action_view_files(self):
        self.ensure_one()
        self._refresh_files()
        return {'type': 'ir.actions.act_window', 'name': _('Files — %s', self.name),
                'res_model': 'ir.attachment', 'view_mode': 'kanban,list,form',
                'domain': [('res_model', '=', self._name), ('res_id', '=', self.id)],
                'context': {'default_res_model': self._name, 'default_res_id': self.id}}

    def action_open_link(self):
        self.ensure_one()
        if not self.link:
            raise UserError(_("This scan has no download link."))
        return {'type': 'ir.actions.act_url', 'url': self.link, 'target': 'new'}

    # ------------------------------------------------------------------ workflow
    def action_confirm(self):
        for scan in self:
            if scan.state != 'new':
                raise UserError(_("%s is not new.", scan.name))
            missing = [label for ok, label in ((scan.partner_id, _("the doctor or clinic")),
                                               (scan.patient, _("the patient")),
                                               (scan.product_id, _("the product"))) if not ok]
            if missing:
                raise UserError(_("Before %(scan)s can be confirmed it needs %(what)s.",
                                  scan=scan.name, what=_(" and ").join(missing)))
            scan.with_context(scan_workflow=True).write({
                'state': 'confirmed', 'confirmed_by_id': self.env.uid,
                'confirmed_on': fields.Datetime.now()})
        return True

    def action_cancel(self):
        for scan in self:
            if scan.state == 'ordered':
                raise UserError(_("%s already became %s — cancel the order, not the scan.",
                                  scan.name, scan.sale_order_id.name))
            scan.with_context(scan_workflow=True).write({'state': 'cancelled'})
        return True

    def action_reset(self):
        """Back to draft, and editable again - right up until the order is made."""
        for scan in self:
            if scan.state == 'ordered' or scan.sale_order_id:
                raise UserError(_("%(scan)s already became %(order)s — it cannot go back to draft.",
                                  scan=scan.name, order=scan.sale_order_id.name or _("an order")))
            scan.with_context(scan_workflow=True).write(
                {'state': 'new', 'confirmed_by_id': False, 'confirmed_on': False})
            scan.message_post(body=_("Reset to draft."))
        return True

    def _order_values(self):
        self.ensure_one()
        lines = []
        if self.product_id:
            lines.append((0, 0, {'product_id': self.product_id.id, 'product_uom_qty': 1}))
        source = _("Scan %(scan)s, received via %(channel)s on %(date)s",
                   scan=self.name, channel=self.channel_id.name,
                   date=fields.Date.to_string(self.received_on.date()))
        note = '\n'.join(filter(None, (self.notes, source)))
        vals = {
            'partner_id': self.partner_id.id,
            'origin': self.name,
            'client_order_ref': self.external_ref or False,
            'patient': self.patient,
            'instruction': self.appliance or False,
            'note': Markup('<p>%s</p>') % Markup('<br/>').join(escape(l) for l in note.split('\n')),
            'scan_id': self.id,
            'order_line': lines,
            # a scanned case, in the field the order form already has for it. Not
            # 3D Model Print: Ortho relabelled that flag "Scanned Order", the lab's
            # still means a printed model.
            'scanned_impression': 'Yes',
        }
        if self.delivery_requested:
            vals['date_due'] = self.delivery_requested
        if self.urgent:
            priority = self.env['sale.order']._fields.get('priority')
            keys = [k for k, _l in (priority.selection if isinstance(priority.selection, list) else [])]
            if 'urgent' in keys:
                vals['priority'] = 'urgent'
        return vals

    def action_create_order(self):
        """The order, with everything the scan knows and the files it carries."""
        self.ensure_one()
        if self.sale_order_id:
            return self.action_view_order()
        if self.state != 'confirmed':
            raise UserError(_("Confirm %s first — an order is made from a confirmed scan.",
                              self.name))
        order = self.env['sale.order'].create(self._order_values())
        for att in self._attachments():
            att.copy({'res_model': 'sale.order', 'res_id': order.id})
        self.with_context(scan_workflow=True).write({'sale_order_id': order.id, 'state': 'ordered'})
        self.message_post(body=_("Order %s created.", order._get_html_link()))
        order.message_post(body=_("Made from scan %s.", self._get_html_link()))
        return self.action_view_order()

    def action_view_order(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'sale.order',
                'res_id': self.sale_order_id.id, 'view_mode': 'form'}

    def action_view_history(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': _('Earlier orders — %s', self.patient),
                'res_model': 'sale.order', 'view_mode': 'list,form',
                'domain': self._history_domain(), 'context': {'create': False}}

    def _acknowledgement(self):
        self.ensure_one()
        parts = [_("Hello! %(company)s has received the scan for %(patient)s (%(scan)s).",
                   company=self.company_id.name, patient=self.patient or _("your patient"),
                   scan=self.name)]
        if self.delivery_requested:
            parts.append(_("Delivery asked for: %s.", self.delivery_requested.strftime('%d-%m-%Y')))
        parts.append(_("We will be in touch if anything is unclear. Thank you."))
        return ' '.join(parts)

    def action_acknowledge(self):
        """Tell the doctor it arrived, on WhatsApp, in one click: the message is
        written, the chat opens, and the register remembers it was done."""
        self.ensure_one()
        if not self.whatsapp_to:
            raise UserError(_("There is no WhatsApp number for the poster or the clinic."))
        text = self._acknowledgement()
        self.write({'acknowledged_on': fields.Datetime.now()})
        self.message_post(body=_("Acknowledged on WhatsApp to +%(to)s: %(text)s",
                                 to=self.whatsapp_to, text=text))
        return {'type': 'ir.actions.act_url', 'target': 'new',
                'url': 'https://wa.me/%s?text=%s' % (self.whatsapp_to, quote(text))}

    def action_view_duplicates(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('lab_scan_register.action_scan')
        action['domain'] = [('id', 'in', (self | self.duplicate_ids).ids)]
        action['context'] = {}
        return action

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
        """A notification from a scanner cloud, forwarded by the lab, becomes a scan.

        Read for the platform, the patient, the doctor, the case number, the download
        link and when it dies. What cannot be read is left blank, and the mail itself
        is in the chatter for whoever fills it in."""
        subject = msg_dict.get('subject') or ''
        text = html2plaintext(msg_dict.get('body') or '')
        email_from = msg_dict.get('email_from') or ''
        parsed = parsers.parse_email(subject, text, email_from)
        company = self.env.company
        if custom_values and custom_values.get('company_id'):
            company = self.env['res.company'].browse(custom_values['company_id'])
        Channel = self.env['lab.scan.channel']
        channel = Channel._match_email(parsed.get('sender', ''), text, company) or \
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
            'patient': parsed.get('patient', False),
            'appliance': parsed.get('appliance', False),
            'external_ref': parsed.get('external_ref', False),
            'link': parsed.get('link', False),
            'link_expires': parsed.get('link_expires', False),
            'user_id': False,
        })
        notes = parsed.get('notes', '')
        files = parsed.get('file_names')
        if files:
            notes = '\n'.join(filter(None, (notes, _("Files: %s", ', '.join(files)))))
        vals['notes'] = notes or False
        received = msg_dict.get('date')
        if received:
            try:
                vals['received_on'] = fields.Datetime.to_datetime(received)
            except (ValueError, TypeError):
                pass
        return self.create(vals)

    # ------------------------------------------------------------------ cron
    @api.model
    def _cron_watch_links(self):
        """A link about to die with no files in yet is a scan about to be lost. Say so
        once, as an activity on whoever registered it, before it is too late."""
        today = fields.Date.context_today(self)
        horizon = today + timedelta(days=EXPIRY_WARNING_DAYS)
        self.search([('state', 'in', OPEN)])._refresh_files()
        due = self.search([('state', 'in', OPEN), ('link', '!=', False),
                           ('link_expires', '!=', False), ('link_expires', '<=', horizon),
                           ('expiry_warned', '=', False), ('files_received', '=', False)])
        for scan in due:
            user = scan.user_id or scan.create_uid
            when = _("expired on %s") if scan.link_expires < today else _("expires on %s")
            scan.activity_schedule(
                'mail.mail_activity_data_todo',
                date_deadline=min(scan.link_expires, today),
                user_id=user.id,
                summary=_("Download the scan files before the link dies"),
                note=_("The download link for %(patient)s (%(scan)s) %(when)s and no files "
                       "have been attached yet.",
                       patient=scan.patient or scan.name, scan=scan.name,
                       when=when % fields.Date.to_string(scan.link_expires)))
            scan.expiry_warned = True
        return len(due)
