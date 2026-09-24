# -*- coding: utf-8 -*-
"""The public pages answer, say where the lab is, and the hook filled in the blanks."""
from markupsafe import escape

from odoo.tests import HttpCase, TransactionCase, tagged

from ..hooks import post_init_hook
from ..models.website import LAB, SERVICES

PAGES = ('/', '/services', '/technology', '/gallery', '/academy', '/about-us',
         '/send-a-case', '/careers', '/faq', '/contactus', '/contactus-thank-you')


@tagged('post_install', '-at_install')
class TestPublicPages(HttpCase):

    def test_every_public_page_answers_200(self):
        for url in PAGES + tuple('/services/%s' % s['slug'] for s in SERVICES):
            self.assertEqual(self.url_open(url).status_code, 200, url)

    def test_an_unknown_service_is_a_404_not_a_crash(self):
        self.assertEqual(self.url_open('/services/no-such-thing').status_code, 404)

    def test_the_homepage_is_ours_and_names_the_lab(self):
        """The hook must have pointed "/" at page_home — otherwise the visitor gets
        Odoo's stock homepage, which passes a 200 check for the wrong reason."""
        html = self.url_open('/').text
        self.assertIn('o_lab_hero', html)
        self.assertIn('id="labHero"', html, 'the hero slider')
        self.assertIn('Arabian Dental Lab', html)

    def test_the_slider_has_one_slide_per_promise(self):
        html = self.url_open('/').text
        self.assertEqual(html.count('class="carousel-item'), 3)
        self.assertEqual(html.count('data-bs-slide-to='), 3)

    def test_the_address_phone_and_email_are_on_the_contact_pages(self):
        for url in ('/', '/about-us', '/contactus', '/send-a-case'):
            html = self.url_open(url).text
            self.assertIn(LAB['city'], html, url)
            self.assertIn(LAB['zip'], html, url)
            self.assertIn('tel:' + LAB['phones'][0][1], html, url)
            self.assertIn(LAB['email'], html, url)

    def test_no_demo_company_text_survives_anywhere(self):
        """Odoo types "+1 555-555-5556", "info@yourcompany.example.com" and "3575 Fake
        Buena Vista Avenue" into the header, the footer, the contact page and its
        thank-you page. Every one of them is replaced.

        The cache is cleared first: in an upgrade-then-test run the template cache can
        still hold the header as it was before this module's override existed."""
        self.env.registry.clear_cache()
        for url in PAGES:
            html = self.url_open(url).text
            for demo in ('555-555-5556', 'yourcompany.example.com', 'Fake Buena Vista', 'My Company',
                         'passionate people', '80781 02515'):
                at = html.find(demo)
                self.assertEqual(at, -1, '%s still shows %r in: %s' % (url, demo, html[max(0, at - 300):at + 60]))

    def test_every_service_is_listed_and_has_its_own_page(self):
        html = self.url_open('/services').text
        for service in self.env['website'].lab_services():
            self.assertIn('id="%s"' % service['slug'], html)
            self.assertIn(escape(service['title']), html)   # "Crown &amp; Bridge"
            page = self.url_open('/services/%s' % service['slug']).text
            self.assertIn(escape(service['title']), page)
            for item in service['send']:
                self.assertIn(escape(item), page, 'the "what to send" list')

    def test_the_enquiry_forms_post_to_the_lab_inbox(self):
        """Every form on the site is Odoo's own website form writing a mail.mail, so
        it must carry the fields the /website/form/ controller needs."""
        for url in ('/send-a-case', '/academy', '/careers'):
            html = self.url_open(url).text
            self.assertIn('data-model_name="mail.mail"', html, url)
            self.assertIn('name="email_to" value="%s"' % LAB['email'], html, url)
            self.assertIn('name="subject"', html, url)
            self.assertIn('s_website_form_send', html, url)

    def test_the_gallery_opens_into_the_lightbox(self):
        html = self.url_open('/gallery').text
        self.assertIn('id="labLightbox"', html)
        self.assertGreaterEqual(html.count('data-bs-target="#labLightbox"'), 10)

    def test_the_menu_points_at_pages_that_exist(self):
        """Odoo copies the generic menu tree into every website, so compare URLs, not
        rows — and every menu URL must be one this module answers."""
        menus = self.env['website.menu'].search([('url', 'like', '/%')])
        ours = {m.url for m in menus if m.url.startswith(('/services', '/technology', '/academy',
                                                          '/gallery', '/about-us', '/send-a-case',
                                                          '/faq', '/careers'))}
        expected = {'/services', '/technology', '/academy', '/gallery', '/about-us',
                    '/send-a-case', '/faq', '/careers'} | {'/services/%s' % s['slug'] for s in SERVICES}
        self.assertEqual(ours, expected)
        self.assertFalse(menus.filtered(lambda m: m.url in ('/restorations', '/appliances')),
                         'the old site\'s menus are gone')
        for url in expected:
            self.assertEqual(self.url_open(url).status_code, 200, url)

    def test_the_header_sends_a_case_and_the_footer_knows_the_lab(self):
        html = self.url_open('/technology').text
        self.assertIn('href="/send-a-case" class="oe_unremovable btn btn-primary btn_cta"', html)
        self.assertIn('o_lab_footer', html)
        self.assertIn('logo-white.png', html)


class TestServices(TransactionCase):

    def test_a_service_counts_only_saleable_products_in_matching_categories(self):
        Category = self.env['product.category']
        crowns = Category.create({'name': 'Zirconia Crowns'})
        other = Category.create({'name': 'Consumables'})
        Product = self.env['product.template']
        Product.create({'name': 'Anterior zirconia', 'categ_id': crowns.id, 'sale_ok': True})
        Product.create({'name': 'Posterior zirconia', 'categ_id': crowns.id, 'sale_ok': True})
        Product.create({'name': 'Old design', 'categ_id': crowns.id, 'sale_ok': False})
        Product.create({'name': 'Alginate', 'categ_id': other.id, 'sale_ok': True})

        svc = {s['slug']: s for s in self.env['website'].lab_services()}
        self.assertEqual(svc['crown-and-bridge']['count'], 2)
        self.assertIn(crowns.id, svc['crown-and-bridge']['category_ids'])
        self.assertNotIn(other.id, svc['crown-and-bridge']['category_ids'])

    def test_a_service_with_no_category_still_shows(self):
        """A lab that has not typed its catalogue in yet must not lose the page."""
        slugs = [s['slug'] for s in self.env['website'].lab_services()]
        self.assertEqual(slugs, [s['slug'] for s in SERVICES])
        self.assertIsNone(self.env['website'].lab_service('nope'))

    def test_the_stats_strip_is_always_four_tiles(self):
        tiles = self.env['website'].lab_stats()
        self.assertEqual(len(tiles), 4)
        self.assertTrue(all(t['n'] and t['l'] for t in tiles))

    def test_the_content_is_complete(self):
        content = self.env['website'].lab_content()
        self.assertEqual(len(content['hero']), 3)
        self.assertEqual(len(content['process']), 5, 'the five portal stages')
        self.assertGreaterEqual(len(content['faq']), 6)
        self.assertGreaterEqual(len(content['gallery']), 10)
        for slide in content['hero']:
            self.assertTrue(slide['img'].startswith('/lab_website/static/src/img/'))


class TestHook(TransactionCase):

    def test_the_hook_fills_blanks_and_never_overwrites(self):
        company = self.env.company
        company.write({'street': 'Somewhere else', 'phone': False, 'zip': False,
                       'city': False, 'website': False, 'email': False})
        post_init_hook(self.env)
        self.assertEqual(company.street, 'Somewhere else', 'a typed-in street is kept')
        self.assertEqual(company.phone, LAB['phones'][0][0])
        self.assertEqual(company.email, LAB['email'])
        self.assertEqual(company.zip, LAB['zip'])
        self.assertEqual(company.city, LAB['city'])
        self.assertEqual(company.website, LAB['website'])
        website = self.env['website'].search([], limit=1)
        self.assertEqual(website.name, company.name)
        home = self.env['website.page'].search([('url', '=', '/')])
        self.assertEqual(len(home), 1)
        self.assertEqual(home.view_id, self.env.ref('lab_website.page_home'))

    def test_a_placeholder_company_is_named_after_the_lab(self):
        company = self.env.company
        company.name = 'My Company'
        post_init_hook(self.env)
        self.assertEqual(company.name, LAB['name'])
