# -*- coding: utf-8 -*-
"""Point the site at the new homepage, name it after the lab, and fill in the blanks.

Done in a hook rather than as data because everything here depends on records that
already exist in this database (the website record, the company), and none of them
should be recreated. Nothing that has been filled in by hand is ever overwritten: the
address, phone and social links are only written where the field is still empty, so
installing on a configured database changes nothing but the homepage.
"""
import logging

from .models.website import LAB

_logger = logging.getLogger(__name__)

# What a fresh Odoo database calls its company before anyone renames it.
PLACEHOLDER_COMPANY_NAMES = {'My Company', 'YourCompany', 'My Company (San Francisco)'}


def _fill_blanks(record, values):
    """Write only the fields that are empty; return what was written."""
    todo = {k: v for k, v in values.items()
            if k in record._fields and not record[k]}
    if todo:
        record.write(todo)
    return todo


def _configure_company(env):
    company = env.company
    values = {
        'street': LAB['street'],
        'street2': LAB['street2'],
        'city': LAB['city'],
        'zip': LAB['zip'],
        'phone': LAB['phones'][0][0],
        'website': LAB['website'],
        'social_instagram': LAB['instagram'],
    }
    state = env.ref('base.state_in_kl', raise_if_not_found=False)
    country = env.ref('base.in', raise_if_not_found=False)
    if country and (not company.country_id or company.country_id == country):
        values['country_id'] = country.id
        if state and (not company.state_id or company.state_id.country_id == country):
            values['state_id'] = state.id
    # The name is a decision somebody makes; only a never-renamed company gets it.
    if company.name in PLACEHOLDER_COMPANY_NAMES:
        company.write({'name': LAB['name']})
        _logger.info("lab_website: named the company %s", LAB['name'])
    written = _fill_blanks(company, values)
    if written:
        _logger.info("lab_website: filled in company %s", ', '.join(sorted(written)))


THANKS_VIEW_KEY = 'lab_website.contactus_thanks_lab'
THANKS_ARCH = """<data>
    <xpath expr="//h5[text()='My Company']/parent::div" position="replace">
        <div class="col-lg-4 offset-lg-1">
            <h5>%(name)s</h5>
            <ul class="list-unstyled mb-0 ps-2">
                <li><i class="fa fa-map-marker fa-fw me-2"/><span class="o_force_ltr">%(address)s</span></li>
                <li><i class="fa fa-phone fa-fw me-2"/><a href="tel:%(tel)s"><span class="o_force_ltr">%(phone)s</span></a></li>
                <li><i class="fa fa-clock-o fa-fw me-2"/><span>Mon – Sat, 9:00 AM – 6:00 PM</span></li>
            </ul>
        </div>
    </xpath>
</data>"""


def _fix_contact_thanks_page(env):
    """Give the contact thank-you page the lab's sidebar instead of Odoo's demo one.

    That page is a website.page record, so its view has no xmlid of its own and cannot
    be inherited from XML (the page's xmlid resolves to the page, not the view). The
    inheriting view is created here against the real view id instead — with its own
    xmlid, so a second install finds it rather than making another.
    """
    page = env.ref('website.contactus_thanks', raise_if_not_found=False)
    if not page or 'My Company' not in (page.view_id.arch or ''):
        return
    View = env['ir.ui.view']
    existing = env.ref(THANKS_VIEW_KEY, raise_if_not_found=False)
    if existing:
        return
    view = View.create({
        'name': 'Contact thanks: %s' % LAB['name'],
        'type': 'qweb',
        'mode': 'extension',
        'inherit_id': page.view_id.id,
        'key': THANKS_VIEW_KEY,
        'arch': THANKS_ARCH % {
            'name': LAB['name'],
            'address': '%s, %s, %s %s' % (LAB['street'], LAB['street2'], LAB['city'], LAB['zip']),
            'tel': LAB['phones'][0][1],
            'phone': LAB['phones'][0][0],
        },
    })
    env['ir.model.data']._update_xmlids([{'xml_id': THANKS_VIEW_KEY, 'record': view, 'noupdate': False}])
    _logger.info("lab_website: added the lab's sidebar to the contact thank-you page")


def post_init_hook(env):
    _configure_company(env)
    _fix_contact_thanks_page(env)

    website = env['website'].search([], limit=1)
    if not website:
        _logger.warning("lab_website: no website record to configure")
        return

    vals = {'name': env.company.name or LAB['name']}
    view = env.ref('lab_website.page_home', raise_if_not_found=False)
    if view and 'homepage_url' in website._fields:
        # A key, not a URL: Odoo resolves it to the view that renders "/".
        vals['homepage_url'] = '/'
    website.write(vals)
    _fill_blanks(website, {'social_instagram': LAB['instagram']})

    # Replace what "/" renders with our homepage, leaving the stock one intact so the
    # change is reversible by pointing the page back at it.
    # No limit=1 and no website filter: a SECOND page at "/" is what stops Odoo
    # resolving a homepage at all, so any duplicate has to be found and removed rather
    # than ignored.
    home_pages = env['website.page'].search([('url', '=', '/')], order='id')
    home_page = home_pages[:1]
    if len(home_pages) > 1:
        home_pages[1:].unlink()
    if home_page and view:
        home_page.write({'view_id': view.id, 'is_published': True})
        _logger.info("lab_website: homepage now renders %s", view.key)
    elif view:
        env['website.page'].create({
            'name': 'Home', 'url': '/', 'view_id': view.id,
            'website_id': website.id, 'is_published': True,
        })
        _logger.info("lab_website: created the homepage")
