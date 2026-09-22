# -*- coding: utf-8 -*-
"""Site content that comes from the lab's own records.

The restoration families on the public site are the same categories the lab actually
makes and invoices against, counted live. Hard-coding them would mean the day somebody
adds a family in Odoo, the website quietly starts lying — and the website is the thing a
new dentist judges the lab by.
"""
from odoo import api, models

# The public families, in the order they are presented, with the words that identify
# their product categories in the catalogue. Matching is a case-insensitive "contains"
# on the category name so the lab is free to name categories as it likes ("Zirconia
# Crowns", "PFM CROWN & BRIDGE", ...). A family with no matching category still shows,
# just without a design count — a lab that makes crowns should not lose the crowns
# block because nobody has typed the catalogue in yet.
FAMILIES = [
    {
        'slug': 'crown-bridge',
        'title': 'Crown & Bridge',
        'blurb': "Zirconia, lithium disilicate, PFM and full-metal — layered and glazed "
                 "to the shade you send, seated first time.",
        'icon': 'fa-diamond',
        'match': ['CROWN', 'BRIDGE', 'ZIRCONIA', 'PFM', 'CERAMIC', 'VENEER', 'INLAY'],
    },
    {
        'slug': 'removable',
        'title': 'Removable Prosthetics',
        'blurb': "Complete and partial dentures, cast partials and flexible dentures, "
                 "finished so the patient will actually wear them.",
        'icon': 'fa-smile-o',
        'match': ['DENTURE', 'REMOVABLE', 'PARTIAL', 'FLEXIBLE', 'ACRYLIC'],
    },
    {
        'slug': 'implant',
        'title': 'Implant Prosthetics',
        'blurb': "Screw-retained and cemented crowns, custom abutments, bars and "
                 "hybrid prostheses on every major implant system.",
        'icon': 'fa-anchor',
        'match': ['IMPLANT', 'ABUTMENT', 'HYBRID'],
    },
    {
        'slug': 'orthodontic',
        'title': 'Orthodontic & Speciality',
        'blurb': "Aligners, retainers, splints, night guards and the one-off "
                 "speciality work a general catalogue never lists.",
        'icon': 'fa-magic',
        'match': ['ORTHO', 'ALIGNER', 'RETAINER', 'APPLIANCE', 'SPLINT', 'GUARD'],
    },
]

# Facts about the lab that are not in any record, kept in one place so the pages and
# the hook agree. Address as it appears on Google Maps (plus code 44RC+H65).
LAB = {
    'name': 'Arabian Dental Lab',
    'tagline': 'Your clear choice for a beautiful smile',
    'street': 'Nilambur – Malappuram Road',
    'street2': 'Melakkam, Manjeri – Wandoor Road',
    'city': 'Manjeri',
    'district': 'Malappuram',
    'state': 'Kerala',
    'zip': '676123',
    'plus_code': '44RC+H65',
    'phones': [('+91 94478 49858', '+919447849858'),
               ('+91 80781 02515', '+918078102515')],
    'hours': 'Monday – Saturday, 9:00 AM – 6:00 PM · Sunday closed',
    'website': 'https://arabiandentallab.com',
    'instagram': 'https://www.instagram.com/arabiandentallab_uae_kerala',
    'maps': 'https://www.google.com/maps/search/?api=1&query=Arabian+Dental+Lab+Manjeri',
    'google_rating': '4.6',
    'academy': 'ADL Dental Academy',
    'hall': 'Crown Conference Hall',
}


class Website(models.Model):
    _inherit = 'website'

    @api.model
    def lab_info(self):
        """The lab's own facts, for the templates."""
        return dict(LAB)

    @api.model
    def lab_families(self):
        """The restoration families, with a live count of what the lab makes in each."""
        Category = self.env['product.category'].sudo()
        Product = self.env['product.template'].sudo()
        out = []
        for family in FAMILIES:
            cats = Category.search(['|'] * (len(family['match']) - 1)
                                   + [('name', 'ilike', word) for word in family['match']])
            count = Product.search_count([
                ('categ_id', 'child_of', cats.ids), ('sale_ok', '=', True)]) if cats else 0
            out.append(dict(family, count=count, category_ids=cats.ids))
        return out

    @api.model
    def lab_stats(self):
        """Four tiles for the homepage, measured where a number exists.

        Always four, so the strip never renders lopsided on an empty database: a live
        count that is zero is replaced by a fact about the lab rather than shown as 0.
        """
        Product = self.env['product.template'].sudo()
        Partner = self.env['res.partner'].sudo()
        families = self.lab_families()
        designs = sum(f['count'] for f in families) or Product.search_count([('sale_ok', '=', True)])
        clinic_domain = [('is_clinic', '=', True)] if 'is_clinic' in Partner._fields \
            else [('customer_rank', '>', 0)]
        clinics = Partner.search_count(clinic_domain)

        tiles = []
        if designs:
            tiles.append({'n': '%d+' % designs, 'l': 'Restoration designs'})
        if clinics:
            tiles.append({'n': '%d+' % clinics, 'l': 'Clinics served'})
        tiles += [
            {'n': LAB['google_rating'] + ' ★', 'l': 'Google rating'},
            {'n': 'Mon – Sat', 'l': 'Open 9 AM to 6 PM'},
            {'n': 'Doorstep', 'l': 'Pickup & delivery'},
            {'n': 'Academy', 'l': 'Training on site'},
        ]
        return tiles[:4]
