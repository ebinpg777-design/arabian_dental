# -*- coding: utf-8 -*-
import base64
import re

from odoo import api, fields, models, tools
from odoo.tools import file_open


class ResPartner(models.Model):
    _inherit = 'res.partner'

    is_clinic = fields.Boolean(string='Clinic')
    is_doctor = fields.Boolean(string='Doctor')
    # v10 kept the sales team ("Sales Route") on the partner and every order took it
    # from there; v19 dropped res.partner.team_id, so it is carried here and
    # sale.order._compute_team_id prefers it (see models/sale_order.py).
    team_id = fields.Many2one(
        'crm.team', string='Sales Route', index=True, tracking=True,
        help="The route (sales team) this clinic belongs to. New orders for this "
             "clinic take their Sales Route from here.")
    # -- v17 dental_sale's clinic fields (client, 2026-10-20) ------------------------
    # The migration had turned the doctor text into a child contact flagged is_doctor
    # (540 of them, never used) and kept the district only as a tag; the lab wants the
    # Odoo 17 fields back on the clinic itself.
    doctor_name = fields.Char(string='Doctor', help="The doctor at this clinic.")
    billing_name = fields.Char(
        string='Billing Name', help="The name the clinic is billed under, when it differs.")
    district_id = fields.Many2one('lab.district', string='District', index=True)

    # -- Sales Route in partner pickers (client, 2026-08-17) -------------------------
    # In every partner many2one / many2many the dropdown entry reads
    #   "DR PHILIP P.C  KEK"   (route muted, after the name)
    # and typing "KEK" lists the clinics on that route. Only the DROPDOWN label changes
    # (Odoo renders it from display_name computed with `formatted_display_name`); the
    # partner's normal display name - reports, chatter, headers, list cells - is untouched.
    @api.model
    def default_get(self, fields_list):
        """A contact belongs to the lab, not to the company of whoever types it in.

        Odoo leaves res.partner.company_id empty (a shared contact); it only fills in
        when a `default_company_id` is inherited from the screen the contact is being
        created from — a sale order, an invoice — which then locks that clinic to one
        company and hides it from the other. Drop that inherited default; picking a
        company by hand on the form still works. (client, 2026-08-20)
        """
        values = super().default_get(fields_list)
        values.pop('company_id', None)
        return values

    @property
    def _rec_names_search(self):
        # Also findable by Sales Route and by ADDRESS: the counter often knows the clinic
        # by where it is ("Kacherithazham", "Muvattupuzha") rather than by its exact name.
        # NOT by city: one city matches hundreds of clinics and buried the one being
        # typed, so it is left out entirely. (client, 2026-08-19)
        names = list(super()._rec_names_search or []) + [
            'team_id.name', 'commercial_partner_id.team_id.name',
            'street', 'street2', 'phone',
        ]
        return list(dict.fromkeys(names))

    @api.depends('team_id.name', 'phone')
    @api.depends_context('formatted_display_name', 'show_address')
    def _compute_display_name(self):
        super()._compute_display_name()
        ctx = self.env.context
        if ctx.get('formatted_display_name'):
            for partner in self:
                route = partner.team_id.name or partner.commercial_partner_id.team_id.name
                if route:
                    partner.display_name = f"{partner.display_name}\t--{route}--"
        elif ctx.get('show_address'):
            # The address block under "Customer" on orders and invoices (client,
            # 2026-08-17): the phone belongs there — the counter calls the clinic from
            # that screen. Not in the dropdown (formatted branch above), where core keeps
            # the label address-free too.
            for partner in self:
                phone = partner.phone or partner.commercial_partner_id.phone
                if phone:
                    partner.display_name = f"{partner.display_name}\nPh: {phone}"
    gst_number = fields.Char(string='GSTIN', size=20)
    pan_number = fields.Char(string='PAN (Lab Record)', size=20)
    dci_number = fields.Char(string='DCI Number', size=30)


class ResCompany(models.Model):
    _inherit = "res.company"

    gst_number = fields.Char(string='GSTIN', size=20)
    pan_number = fields.Char(string='PAN (Lab Record)', size=20)
    emergency_service_id = fields.Many2one(
        'product.product', string='Emergency Service Product')
    emergency_service_perc = fields.Float(string="Emergency Service Percentage")
    invoice_signature = fields.Binary('Signature', copy=False)
    logo1 = fields.Binary('Logo1', copy=False)
    logo2 = fields.Binary('Logo2', copy=False)
    # UPI / payment QR printed on the Tax Invoice and the account statement. Embedded
    # inline (data URI) instead of the old `<img src="/sale_custom/static/...">`: that
    # made wkhtmltopdf fetch the image over HTTP from the server's own base URL on every
    # print - slow, and a hang when the box cannot reach that URL.
    # No default (2026-10-19): the module used to ship a QR image and install it on
    # every company - Ortho Creation's UPI code, carried over in the fork - so the
    # lab's invoices and statements asked clinics to pay Ortho. A company shows no
    # pay card until its own QR is uploaded.
    payment_qr = fields.Binary(
        'Payment QR', copy=False, attachment=True,
        help="Shown on invoices and statements. Upload the clinic-facing UPI QR here.")

    company_warning = fields.Boolean('Company Warning', copy=False)

    # The letterhead's contact block, written the way the lab wants it printed - one
    # line per line, as on its own stationery (client, 2026-10-19: "add more
    # information in header by adding more fields in the company"). Each falls back to
    # the company's own address / phone / e-mail when left empty.
    letterhead_address = fields.Text(
        'Letterhead Address',
        help="The address on invoices, one line per line. Empty: the company address.")
    letterhead_phones = fields.Text(
        'Letterhead Phones',
        help="Phone numbers on invoices, one line per line. Empty: the company phone.")
    letterhead_emails = fields.Text(
        'Letterhead E-mails',
        help="E-mail addresses on invoices, one line per line. Empty: the company e-mail.")
    # Printed in a box of its own at the foot of every tax invoice (client, 2026-10-19).
    gst_declaration = fields.Text(
        'GST Declaration',
        default='We declare that this invoice shows the actual price of the goods described '
                'and that all particulars are true and correct.',
        help="Printed at the foot of every tax invoice and credit note.")

    _LETTERHEAD_ICONS = {'address': 'lh_location.svg', 'phones': 'lh_phone.svg', 'emails': 'lh_mail.svg'}

    def _lab_letterhead(self):
        """[(kind, icon data URI, [lines])] for the letterhead - 'address', 'phones',
        'emails' - empty kinds left out. The icons travel inside the page as data URIs:
        a /static URL makes wkhtmltopdf fetch it from the server on every print (see
        payment_qr)."""
        self.ensure_one()

        def lines(text):
            return [' '.join(line.split()) for line in (text or '').splitlines() if line.strip()]

        address = lines(self.letterhead_address)
        if not address:
            # "ADL Tower,Karikkad" as typed in the address fields: a space after
            # every comma, none before
            tidy = lambda s: re.sub(r'\s*,\s*', ', ', ' '.join((s or '').split())).strip(' ,')
            first = ', '.join(x for x in (tidy(self.street), tidy(self.street2)) if x)
            second = ', '.join(x for x in (tidy(self.city), self.state_id.name or '') if x)
            if self.zip:
                second = '%s - %s' % (second, self.zip) if second else self.zip
            address = [x for x in (first, second) if x]
        blocks = {
            'address': address,
            'phones': lines(self.letterhead_phones) or lines(self.phone),
            'emails': lines(self.letterhead_emails) or lines(self.email),
        }
        result = []
        for kind in ('address', 'phones', 'emails'):
            if blocks[kind]:
                with file_open('sale_custom/static/src/img/' + self._LETTERHEAD_ICONS[kind], 'rb') as f:
                    icon = 'data:image/svg+xml;base64,' + base64.b64encode(f.read()).decode()
                result.append((kind, icon, blocks[kind]))
        return result


class ProductTemplate(models.Model):
    _inherit = "product.template"

    taxable_percentage = fields.Float(string='GST Taxable Percentage', default=100.0)
    hsn_number = fields.Char(string='HSN', size=20)
    appliance_style = fields.Many2one('product.appliance.style', string='Appliance Style')


class ProductApplianceStyle(models.Model):
    _name = "product.appliance.style"
    _description = "Product Appliance Style"

    name = fields.Char(string='Name', required=True)
    description = fields.Text(string='Description')
    active = fields.Boolean(string='Active', default=True)
