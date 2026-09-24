# -*- coding: utf-8 -*-
"""Everything the public site says about the lab, in one place.

The facts (`LAB`), the services, the academy programmes, the FAQ and the gallery are
plain data here rather than typed into twelve templates, so a changed phone number or
a renamed service is one edit. Where a number can be measured from the database — how
many designs are in the catalogue, how many clinics are served — it is, so the site
cannot quietly drift from what the lab actually does.
"""
from markupsafe import Markup

from odoo import api, models

IMG = '/lab_website/static/src/img/'

# Facts about the lab that are not in any record. Address as on Google Maps (plus code
# 44RC+H65); phone, hours and email from the lab's own listing.
LAB = {
    'name': 'Arabian Dental Lab',
    'short': 'ADL',
    'tagline': 'Your clear choice for a beautiful smile',
    'street': 'Nilambur – Malappuram Road',
    'street2': 'Melakkam, Manjeri – Wandoor Road',
    'city': 'Manjeri',
    'district': 'Malappuram',
    'state': 'Kerala',
    'zip': '676123',
    'plus_code': '44RC+H65',
    'phones': [('+91 94478 49858', '+919447849858')],
    'whatsapp': '919447849858',
    'email': 'arabiandentallab@gmail.com',
    'hours': 'Monday – Saturday, 9:00 AM – 6:00 PM',
    'hours_short': 'Mon – Sat, 9 AM – 6 PM',
    'closed': 'Sunday closed',
    'website': 'https://arabiandentallab.com',
    'instagram': 'https://www.instagram.com/arabiandentallab_uae_kerala',
    'instagram_handle': '@arabiandentallab_uae_kerala',
    'maps': 'https://www.google.com/maps/search/?api=1&query=Arabian+Dental+Lab+Manjeri',
    'maps_embed': 'https://www.google.com/maps?q=Arabian+Dental+Lab+Manjeri+Kerala&output=embed',
    'google_rating': '4.6',
    'google_reviews': 17,
    'academy': 'ADL Dental Academy',
    'hall': 'Crown Conference Hall',
}

# The homepage slider. Each slide is one promise over one photograph of the lab's work.
# The implant case opens it (client, 2026-09-23).
HERO = [
    {
        'img': IMG + 'implant-bridge.jpg',
        'pos': '70% 50%',
        'kicker': 'Implant prosthetics',
        'title': 'Seated first time,<br/>on every system',
        'text': 'Screw-retained and cemented crowns, custom abutments, bars and full-arch '
                'hybrids — passive fit verified before dispatch.',
        'cta': ('Implant work', '/services/implant-prosthetics'),
        'cta2': ('Talk to the lab', '/contactus'),
    },
    {
        'img': IMG + 'anterior-veneers.jpg',
        'pos': '62% 55%',          # the incisors, right of the headline
        'kicker': 'Crown & bridge · Veneers',
        'title': 'Made to disappear<br/>in the smile',
        'text': 'Layered ceramics matched to the shade you send, finished by hand and '
                'checked on the model before they leave the bench.',
        'cta': ('See what we make', '/services'),
        'cta2': ('Send us a case', '/send-a-case'),
    },
    {
        'img': IMG + 'technician-bench.jpg',
        'pos': '75% 30%',
        'kicker': 'Doorstep service · Malappuram & beyond',
        'title': 'A laboratory that<br/>comes to your clinic',
        'text': 'Our own executives ride fixed routes, collecting impressions from your '
                'chair-side and bringing the finished work back to it.',
        'cta': ('How to send a case', '/send-a-case'),
        'cta2': ('Track your cases', '/my/cases'),
    },
]

# The services, in the order they are presented. `match` are the words that identify
# their product categories in the catalogue (case-insensitive "contains"), so the site
# can count and list the lab's real designs without anyone typing them twice.
SERVICES = [
    {
        'slug': 'crown-and-bridge',
        'title': 'Crown & Bridge',
        'kicker': 'Fixed prosthetics',
        'icon': 'fa-diamond',
        'img': IMG + 'zirconia-bridge.jpg',
        'summary': 'Monolithic and layered zirconia, lithium disilicate, PFM and full-metal '
                   'crowns and bridges, from a single unit to a full arch.',
        'body': [
            'Every crown starts on a poured or printed model, is designed against the '
            'opposing arch and the contacts you asked for, and is checked for margin, '
            'contact and occlusion on that model before it is glazed.',
            'Choose the material for the case: monolithic zirconia where strength matters, '
            'layered zirconia or lithium disilicate where it has to vanish in the smile, '
            'PFM or full metal where the budget or the bite calls for it.',
        ],
        'materials': ['Monolithic zirconia', 'Layered zirconia', 'Lithium disilicate (e.max)',
                      'Porcelain fused to metal', 'Full metal', 'PMMA temporaries'],
        'send': ['Impression or intra-oral scan', 'Opposing arch and bite',
                 'Shade (photo in daylight helps)', 'Prescription with the material'],
        'match': ['CROWN', 'BRIDGE', 'ZIRCONIA', 'PFM', 'METAL', 'EMAX', 'E.MAX', 'CERAMIC'],
    },
    {
        'slug': 'implant-prosthetics',
        'title': 'Implant Prosthetics',
        'kicker': 'Every major system',
        'icon': 'fa-anchor',
        'img': IMG + 'implant-bridge.jpg',
        'summary': 'Screw-retained and cemented crowns, custom abutments, bars and '
                   'full-arch hybrid prostheses, with passive fit verified on the model.',
        'body': [
            'Send the implant system and the transfer, and the lab builds on the matching '
            'analogue: single crowns on stock or custom abutments, screw-retained bridges, '
            'bars and hybrids for the full arch.',
            'A restoration that rocks on the model never leaves the bench — passive fit is '
            'checked on the verified cast before the case is packed.',
        ],
        'materials': ['Screw-retained crowns', 'Custom titanium & zirconia abutments',
                      'Cement-retained crowns', 'Implant bars', 'Full-arch hybrids',
                      'Surgical guides (digital cases)'],
        'send': ['Implant system and platform', 'Open or closed-tray impression, or scan body',
                 'Bite registration', 'Screw-retained or cemented'],
        'match': ['IMPLANT', 'ABUTMENT', 'HYBRID', 'BAR'],
    },
    {
        'slug': 'veneers-and-aesthetics',
        'title': 'Veneers & Aesthetics',
        'kicker': 'Smile design',
        'icon': 'fa-star-o',
        'img': IMG + 'layered-ceramic.jpg',
        'summary': 'Hand-layered ceramic veneers and anterior crowns, built to a wax-up '
                   'or a digital design and matched to the shade in daylight.',
        'body': [
            'Anterior work is judged at conversation distance. The ceramist layers dentine, '
            'enamel and effects to the photographs you send, so translucency and surface '
            'texture read as tooth, not as porcelain.',
            'Start from a diagnostic wax-up or a digital smile design and the patient sees '
            'the result in a mock-up before a tooth is prepared.',
        ],
        'materials': ['Feldspathic veneers', 'Lithium disilicate veneers', 'Layered anterior crowns',
                      'Diagnostic wax-ups', 'Mock-ups & temporaries'],
        'send': ['Impression or scan', 'Shade tab photos in daylight', 'Face and smile photographs',
                 'The look the patient wants'],
        'match': ['VENEER', 'LAMINATE', 'AESTHETIC', 'ESTHETIC', 'WAX'],
    },
    {
        'slug': 'removable-prosthetics',
        'title': 'Removable Prosthetics',
        'kicker': 'Dentures & partials',
        'icon': 'fa-smile-o',
        'img': IMG + 'model-in-hand.jpg',
        'summary': 'Complete dentures, cast partial frameworks, flexible dentures and '
                   'over-dentures, finished so the patient will actually wear them.',
        'body': [
            'A denture is worn all day, so fit and finish matter more than on anything '
            'else the lab makes: borders polished, the fitting surface clean, the occlusion '
            'balanced on the articulator.',
            'Cast partials are surveyed and designed for the case, flexible dentures for '
            'the patient who cannot tolerate a clasp, and over-dentures on the attachment '
            'system the implant was placed with.',
        ],
        'materials': ['Complete dentures', 'Cast partial dentures (Co-Cr)', 'Flexible dentures',
                      'Acrylic partials', 'Implant over-dentures', 'Relines & repairs'],
        'send': ['Primary or final impression', 'Bite blocks / jaw relation', 'Shade and mould',
                 'Design for partials'],
        'match': ['DENTURE', 'REMOVABLE', 'PARTIAL', 'FLEXIBLE', 'ACRYLIC', 'RELINE', 'REPAIR'],
    },
    {
        'slug': 'digital-dentistry',
        'title': 'Digital Dentistry',
        'kicker': 'Scan · Design · Mill · Print',
        'icon': 'fa-desktop',
        'img': IMG + 'posterior-bridge.jpg',
        'summary': 'Intra-oral scans accepted from every major scanner, designed in CAD, '
                   'milled or printed in house — and still finished by hand.',
        'body': [
            'Send a scan instead of an impression and the case is on the design screen the '
            'same day. Frameworks are milled, models and guides are printed, and the '
            'ceramist still does the layering and the glaze.',
            'Digital does not mean automatic: every design is reviewed against the '
            'prescription before it goes to the mill, and every milled unit is checked on '
            'a printed model.',
        ],
        'materials': ['Intra-oral scan intake', 'CAD design', 'Milled zirconia & PMMA',
                      '3D-printed models', 'Surgical guides', 'Digital smile design'],
        'send': ['Scan export (STL / PLY) from any scanner', 'Bite scan', 'Shade and photographs',
                 'Prescription'],
        'match': ['DIGITAL', 'CAD', 'CAM', 'MILL', 'PRINT', 'SCAN'],
    },
    {
        'slug': 'orthodontic-appliances',
        'title': 'Orthodontic & Speciality',
        'kicker': 'Appliances & guards',
        'icon': 'fa-magic',
        'img': IMG + 'anterior-crowns-model.jpg',
        'summary': 'Retainers, aligners, splints, night guards, space maintainers and the '
                   'one-off speciality work a general catalogue never lists.',
        'body': [
            'The appliances a general practice sends alongside its restorative work: '
            'Hawley and clear retainers, occlusal splints and night guards, space '
            'maintainers, bleaching trays.',
            'For anything unusual, send the case with a note and the lab will propose a '
            'design and tell you what it costs before starting.',
        ],
        'materials': ['Hawley & clear retainers', 'Aligners', 'Occlusal splints & night guards',
                      'Space maintainers', 'Bleaching trays', 'Sports guards'],
        'send': ['Impression or scan', 'Bite', 'Type of appliance', 'Any special instruction'],
        'match': ['ORTHO', 'ALIGNER', 'RETAINER', 'APPLIANCE', 'SPLINT', 'GUARD', 'TRAY'],
    },
]

# What the lab's digital and manual workflow looks like, for the Technology page.
TECHNOLOGY = [
    ('fa-camera', 'Scan intake', 'Intra-oral scans from any scanner land on the design screen the same day; impressions are poured or scanned on arrival.'),
    ('fa-desktop', 'CAD design', 'Every unit is designed against the opposing arch and reviewed against the prescription before anything is cut.'),
    ('fa-cogs', 'Milling', 'Zirconia, PMMA and metal frameworks milled in house, sintered and checked on the model.'),
    ('fa-cube', '3D printing', 'Printed models, surgical guides and try-ins — the digital case still gets a physical check.'),
    ('fa-paint-brush', 'Ceramic layering', 'The part no machine does: dentine, enamel and effects layered by hand to the shade photographs.'),
    ('fa-check-circle', 'Quality gate', 'Margin, contact, occlusion and shade signed off on the model before the case is packed.'),
]

# The five stages the doctor sees in the portal, so the promise made here and the status
# shown later are the same thing.
PROCESS = [
    ('Registered', 'Impression or scan and the prescription reach us; the case is logged with a promised date.'),
    ('In the lab', 'Model, design, framework, ceramic, finishing — each bench records its step against your case.'),
    ('Ready', 'Checked against the prescription and the shade, photographed on request, packed.'),
    ('On its way', 'With our executive on the route to your clinic, or by courier with a tracking number.'),
    ('Delivered', 'In your hands, ready for the appointment. Anything to adjust, tell us the same day.'),
]

# Why a clinic sends here rather than anywhere else.
REASONS = [
    ('fa-diamond', 'Fit, first time', 'Margins, contacts and occlusion checked on the model before anything leaves the bench, so the chair-side adjustment is a minute, not a remake.'),
    ('fa-clock-o', 'A date you can plan around', 'Every case gets a promised date at registration, and you can see where it is instead of ringing to ask. Emergencies have their own lane.'),
    ('fa-motorcycle', 'We come to you', 'Our own field executives ride fixed routes — impressions collected from your clinic, finished work brought back to it.'),
    ('fa-comments-o', 'One message away', 'Case updates, questions about a shade or a design, and delivery notices on WhatsApp — from the lab, not a call centre.'),
    ('fa-graduation-cap', 'A lab that teaches', 'ADL Dental Academy trains technicians on the same benches; the Crown Conference Hall hosts the clinicians who send us work.'),
    ('fa-shield', 'Stands behind the work', 'A remake or an adjustment is handled as a priority, with the original case record in front of the technician.'),
]

# Academy programmes and who they are for.
COURSES = [
    ('fa-diamond', 'Crown & bridge fundamentals', 'Die work, wax-up, framework and the checks that make a crown seat first time.', 'Technicians · Hands-on'),
    ('fa-paint-brush', 'Ceramic layering & shade', 'Building natural translucency and matching the shade the clinic sent, under daylight.', 'Ceramists · Hands-on'),
    ('fa-desktop', 'Digital workflow', 'From intra-oral scan to CAD design, milling and printing — and where the hand still matters.', 'Technicians & clinicians'),
    ('fa-smile-o', 'Removable prosthetics', 'Complete and partial dentures, cast partial frameworks and flexible dentures.', 'Technicians · Hands-on'),
    ('fa-anchor', 'Implant prosthetics', 'Impression techniques, verification jigs, abutment selection and passive fit.', 'Clinicians · Lecture + bench'),
    ('fa-user-md', 'What the lab needs from you', 'Impressions, scans, shade photographs and prescriptions that get the case right the first time.', 'Clinicians · Study club'),
]

AUDIENCE = [
    ('fa-user', 'Fresh graduates', 'BDS and dental technology students who want bench skills a college cannot give.'),
    ('fa-wrench', 'Working technicians', 'Short courses on one material or one workflow, without leaving the trade for weeks.'),
    ('fa-user-md', 'Clinicians', 'See what the lab needs from an impression, a scan and a prescription — and why.'),
]

# Questions doctors actually ask when they first send a case.
FAQ = [
    ('How do I send my first case?',
     'Ring or WhatsApp the lab and we will put your clinic on a collection route. Our executive collects the impression, prescription and shade from your clinic; further afield, send it by courier to the address on the contact page.'),
    ('Do you accept intra-oral scans?',
     'Yes — export the scan (STL or PLY) from any scanner and send it with the bite scan and the prescription. Digital cases skip the impression and go straight to the design screen.'),
    ('How do I get the shade right?',
     'Send a photograph of the shade tab held next to the tooth, in daylight, with the tab number visible. For anterior work a face photograph helps the ceramist read the patient.'),
    ('How long does a case take?',
     'It depends on the work — a monolithic crown and a layered full-arch are not the same job. Every case gets a promised date when it is registered, and you can see it in your login. Emergency cases have their own lane; ask when you send it.'),
    ('What if a crown needs adjusting or remaking?',
     'Tell us the same day. Adjustments and remakes are handled as a priority with the original case record in front of the technician, and we will tell you what, if anything, it costs before we start.'),
    ('How do I track my cases?',
     'Every clinic gets a login. Doctor Login shows every case you have sent, which bench it is on, when it ships and when it was delivered, with the invoices alongside.'),
    ('Which materials do you work in?',
     'Monolithic and layered zirconia, lithium disilicate, PFM, full metal, PMMA, acrylic and flexible denture resins, cobalt-chrome frameworks, and the implant components for every major system. See each service page for the list.'),
    ('How do I join a course at the academy?',
     'Send an enquiry from the Academy page with the programme you are interested in. Batches are small and taught at the bench; we will reply with the next dates and the fee.'),
]

# Roles the lab hires for. Listed as what the lab looks for, not as open vacancies.
ROLES = [
    ('fa-paint-brush', 'Ceramist', 'Layering and staining anterior and posterior work to shade photographs.'),
    ('fa-desktop', 'CAD / CAM designer', 'Designing crowns, bridges and implant work in exocad or 3Shape; running the mill.'),
    ('fa-cube', 'Model & plaster technician', 'Pouring, trimming, die work and articulation — where every case begins.'),
    ('fa-smile-o', 'Denture technician', 'Complete and partial dentures, cast partials, flexible dentures.'),
    ('fa-motorcycle', 'Field executive', 'Riding a route, collecting and delivering cases, being the lab\'s face at the clinic.'),
    ('fa-phone', 'Front office', 'Registering cases, answering doctors on WhatsApp and the phone, keeping the promised dates honest.'),
]

# The gallery: the lab's own photographs.
GALLERY = [
    (IMG + 'layered-ceramic.jpg', 'Layered ceramic anteriors', 'Veneers & aesthetics'),
    (IMG + 'zirconia-bridge.jpg', 'Posterior zirconia bridge', 'Crown & bridge'),
    (IMG + 'implant-bridge.jpg', 'Implant-supported bridge', 'Implant prosthetics'),
    (IMG + 'anterior-veneers.jpg', 'Anterior veneers on the model', 'Veneers & aesthetics'),
    (IMG + 'posterior-bridge.jpg', 'Three-unit posterior bridge', 'Crown & bridge'),
    (IMG + 'single-crown.jpg', 'Single molar crown on its die', 'Crown & bridge'),
    (IMG + 'anterior-crowns-model.jpg', 'Anterior crowns seated on the model', 'Crown & bridge'),
    (IMG + 'model-in-hand.jpg', 'Posterior crowns on the working model', 'Quality check'),
    (IMG + 'crowns-on-model.jpg', 'Full-arch on the articulator', 'Quality check'),
    (IMG + 'technician-bench.jpg', 'Checking the work with the clinician', 'The bench'),
]

# Real quotes go here when the lab supplies them; until then the site shows the Google
# rating and nothing invented.
TESTIMONIALS = []


class Website(models.Model):
    _inherit = 'website'

    @api.model
    def lab_info(self):
        return dict(LAB)

    @api.model
    def lab_content(self):
        """Static page content, for the templates."""
        # The hero titles carry a deliberate line break; everything else is plain text.
        hero = [dict(slide, title=Markup(slide['title'])) for slide in HERO]
        return {
            'hero': hero, 'technology': TECHNOLOGY, 'process': PROCESS, 'reasons': REASONS,
            'courses': COURSES, 'audience': AUDIENCE, 'faq': FAQ, 'roles': ROLES,
            'gallery': GALLERY, 'testimonials': TESTIMONIALS,
        }

    @api.model
    def lab_services(self):
        """The services, each with a live count of the catalogue designs behind it."""
        Category = self.env['product.category'].sudo()
        Product = self.env['product.template'].sudo()
        out = []
        for service in SERVICES:
            words = service['match']
            cats = Category.search(['|'] * (len(words) - 1) + [('name', 'ilike', w) for w in words])
            count = Product.search_count([
                ('categ_id', 'child_of', cats.ids), ('sale_ok', '=', True)]) if cats else 0
            out.append(dict(service, count=count, category_ids=cats.ids))
        return out

    @api.model
    def lab_service(self, slug):
        return next((s for s in self.lab_services() if s['slug'] == slug), None)

    @api.model
    def lab_stats(self):
        """Four tiles, measured where a number exists and never shown as 0."""
        Product = self.env['product.template'].sudo()
        Partner = self.env['res.partner'].sudo()
        services = self.lab_services()
        designs = sum(s['count'] for s in services) or Product.search_count([('sale_ok', '=', True)])
        clinic_domain = [('is_clinic', '=', True)] if 'is_clinic' in Partner._fields \
            else [('customer_rank', '>', 0)]
        clinics = Partner.search_count(clinic_domain)
        tiles = []
        if designs:
            tiles.append({'n': designs, 'suffix': '+', 'l': 'Restoration designs in the catalogue'})
        if clinics:
            tiles.append({'n': clinics, 'suffix': '+', 'l': 'Clinics served'})
        tiles += [
            {'n': LAB['google_rating'], 'suffix': ' ★', 'l': 'Google rating, %d reviews' % LAB['google_reviews'], 'text': True},
            {'n': 6, 'suffix': '', 'l': 'Service lines under one roof'},
            {'n': 5, 'suffix': '', 'l': 'Case stages you can track live'},
            {'n': 'Mon – Sat', 'suffix': '', 'l': 'Open 9 AM to 6 PM', 'text': True},
        ]
        return tiles[:4]
