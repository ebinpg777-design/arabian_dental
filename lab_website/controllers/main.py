# -*- coding: utf-8 -*-
"""Public pages.

Served by controllers rather than as plain website.page records because the content is
driven by data — the services and their design counts are read at request time, so the
site cannot drift from what the lab actually makes.
"""
from urllib.parse import quote

from odoo import http
from odoo.http import request
from werkzeug.exceptions import NotFound

# How many example designs to name under each service. Enough to show the range, few
# enough that the page stays a summary rather than a price list.
SAMPLES = 12


class LabWebsite(http.Controller):

    def _values(self, **extra):
        website = request.env['website'].sudo()
        values = {'lab': website.lab_info(), 'content': website.lab_content(),
                  'services': website.lab_services()}
        values.update(extra)
        return values

    def _with_products(self, service):
        Product = request.env['product.template'].sudo()
        names = []
        if service.get('category_ids'):
            names = Product.search([
                ('categ_id', 'child_of', service['category_ids']),
                ('sale_ok', '=', True),
            ], limit=SAMPLES).mapped('name')
        service['products'] = [n.strip() for n in names if n and n.strip()]
        return service

    @http.route('/services', type='http', auth='public', website=True, sitemap=True)
    def services(self, **kw):
        return request.render('lab_website.page_services', self._values())

    @http.route('/services/<string:slug>', type='http', auth='public', website=True, sitemap=True)
    def service(self, slug, **kw):
        website = request.env['website'].sudo()
        service = website.lab_service(slug)
        if not service:
            raise NotFound()
        services = website.lab_services()
        index = next(i for i, s in enumerate(services) if s['slug'] == slug)
        return request.render('lab_website.page_service', self._values(
            service=self._with_products(service),
            wa_text=quote('Hello, I have a question about %s.' % service['title']),
            prev_service=services[index - 1],
            next_service=services[(index + 1) % len(services)],
        ))

    @http.route('/technology', type='http', auth='public', website=True, sitemap=True)
    def technology(self, **kw):
        return request.render('lab_website.page_technology', self._values())

    @http.route('/gallery', type='http', auth='public', website=True, sitemap=True)
    def gallery(self, **kw):
        return request.render('lab_website.page_gallery', self._values())

    @http.route('/academy', type='http', auth='public', website=True, sitemap=True)
    def academy(self, **kw):
        return request.render('lab_website.page_academy', self._values())

    @http.route('/about-us', type='http', auth='public', website=True, sitemap=True)
    def about(self, **kw):
        return request.render('lab_website.page_about', self._values(
            stats=request.env['website'].sudo().lab_stats()))

    @http.route('/send-a-case', type='http', auth='public', website=True, sitemap=True)
    def send_a_case(self, **kw):
        return request.render('lab_website.page_send_case', self._values())

    @http.route('/careers', type='http', auth='public', website=True, sitemap=True)
    def careers(self, **kw):
        return request.render('lab_website.page_careers', self._values())

    @http.route('/faq', type='http', auth='public', website=True, sitemap=True)
    def faq(self, **kw):
        return request.render('lab_website.page_faq', self._values())
