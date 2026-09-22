# -*- coding: utf-8 -*-
"""Public pages.

Served by controllers rather than as plain website.page records because the content is
driven by the catalogue — the families and their counts are read at request time, so the
site cannot drift from what the lab actually makes.
"""
from odoo import http
from odoo.http import request

# How many example designs to name under each family. Enough to show the range, few
# enough that the page stays a summary rather than a price list.
SAMPLES = 12


class LabWebsite(http.Controller):

    def _families(self, with_products=False):
        families = request.env['website'].sudo().lab_families()
        if not with_products:
            return families
        Product = request.env['product.template'].sudo()
        for family in families:
            names = []
            if family.get('category_ids'):
                names = Product.search([
                    ('categ_id', 'child_of', family['category_ids']),
                    ('sale_ok', '=', True),
                ], limit=SAMPLES).mapped('name')
            family['products'] = [n.strip() for n in names if n and n.strip()]
        return families

    @http.route('/restorations', type='http', auth='public', website=True, sitemap=True)
    def restorations(self, **kw):
        return request.render('lab_website.page_restorations', {
            'families': self._families(with_products=True),
        })

    @http.route('/academy', type='http', auth='public', website=True, sitemap=True)
    def academy(self, **kw):
        return request.render('lab_website.page_academy', {})

    @http.route('/about-us', type='http', auth='public', website=True, sitemap=True)
    def about(self, **kw):
        return request.render('lab_website.page_about', {})
