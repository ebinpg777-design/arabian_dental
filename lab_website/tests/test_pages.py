# -*- coding: utf-8 -*-
"""The public pages answer, say where the lab is, and the hook filled in the blanks."""
from markupsafe import escape

from odoo.tests import HttpCase, TransactionCase, tagged

from ..hooks import post_init_hook
from ..models.website import LAB


@tagged('post_install', '-at_install')
class TestPublicPages(HttpCase):

    def test_every_public_page_answers_200(self):
        for url in ('/', '/restorations', '/academy', '/about-us'):
            self.assertEqual(self.url_open(url).status_code, 200, url)

    def test_the_homepage_is_ours_and_names_the_lab(self):
        """The hook must have pointed "/" at page_home — otherwise the visitor gets
        Odoo's stock homepage, which passes a 200 check for the wrong reason."""
        html = self.url_open('/').text
        self.assertIn('o_lab_hero', html)
        self.assertIn('Arabian Dental Lab', html)

    def test_the_address_and_phone_are_on_the_contact_pages(self):
        for url in ('/', '/about-us'):
            html = self.url_open(url).text
            self.assertIn(LAB['city'], html, url)
            self.assertIn(LAB['zip'], html, url)
            self.assertIn('tel:' + LAB['phones'][0][1], html, url)

    def test_no_demo_company_text_survives_anywhere(self):
        """Odoo types "+1 555-555-5556", "info@yourcompany.example.com" and "3575 Fake
        Buena Vista Avenue" into the header, the footer, the contact page and its
        thank-you page. Every one of them is replaced.

        The cache is cleared first: in an upgrade-then-test run the template cache can
        still hold the header as it was before this module's override existed."""
        self.env.registry.clear_cache()
        for url in ('/', '/restorations', '/academy', '/about-us', '/contactus', '/contactus-thank-you'):
            html = self.url_open(url).text
            for demo in ('555-555-5556', 'yourcompany.example.com', 'Fake Buena Vista', 'My Company'):
                at = html.find(demo)
                self.assertEqual(at, -1, '%s still shows %r in: %s' % (url, demo, html[max(0, at - 300):at + 60]))

    def test_the_restoration_families_are_all_listed(self):
        html = self.url_open('/restorations').text
        for family in self.env['website'].lab_families():
            self.assertIn('id="%s"' % family['slug'], html)
            # "Crown & Bridge" arrives as "Crown &amp; Bridge"
            self.assertIn(escape(family['title']), html)

    def test_the_menu_points_at_pages_that_exist(self):
        """Odoo copies the generic menu tree into every website, so count URLs, not rows."""
        urls = ('/restorations', '/academy', '/about-us', '/my/cases')
        menus = self.env['website.menu'].search([('url', 'in', urls)])
        self.assertEqual(set(menus.mapped('url')), set(urls))


class TestFamilies(TransactionCase):

    def test_a_family_counts_only_saleable_products_in_matching_categories(self):
        Category = self.env['product.category']
        crowns = Category.create({'name': 'Zirconia Crowns'})
        other = Category.create({'name': 'Consumables'})
        Product = self.env['product.template']
        Product.create({'name': 'Anterior zirconia', 'categ_id': crowns.id, 'sale_ok': True})
        Product.create({'name': 'Posterior zirconia', 'categ_id': crowns.id, 'sale_ok': True})
        Product.create({'name': 'Old design', 'categ_id': crowns.id, 'sale_ok': False})
        Product.create({'name': 'Alginate', 'categ_id': other.id, 'sale_ok': True})

        fam = {f['slug']: f for f in self.env['website'].lab_families()}
        self.assertEqual(fam['crown-bridge']['count'], 2)
        self.assertIn(crowns.id, fam['crown-bridge']['category_ids'])
        self.assertNotIn(other.id, fam['crown-bridge']['category_ids'])

    def test_a_family_with_no_category_still_shows(self):
        """A lab that has not typed its catalogue in yet must not lose the block."""
        slugs = [f['slug'] for f in self.env['website'].lab_families()]
        self.assertEqual(slugs, ['crown-bridge', 'removable', 'implant', 'orthodontic'])

    def test_the_stats_strip_is_always_four_tiles(self):
        tiles = self.env['website'].lab_stats()
        self.assertEqual(len(tiles), 4)
        self.assertTrue(all(t['n'] and t['l'] for t in tiles))


class TestHook(TransactionCase):

    def test_the_hook_fills_blanks_and_never_overwrites(self):
        company = self.env.company
        company.write({'street': 'Somewhere else', 'phone': False, 'zip': False,
                       'city': False, 'website': False})
        post_init_hook(self.env)
        self.assertEqual(company.street, 'Somewhere else', 'a typed-in street is kept')
        self.assertEqual(company.phone, LAB['phones'][0][0])
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
