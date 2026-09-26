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
        'email': LAB['email'],
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
                <li><i class="fa fa-envelope fa-fw me-2"/><a href="mailto:%(email)s">%(email)s</a></li>
                <li><i class="fa fa-clock-o fa-fw me-2"/><span>%(hours)s</span></li>
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
            'email': LAB['email'],
            'hours': LAB['hours_short'],
        },
    })
    # noupdate: at the end of a module upgrade Odoo deletes this module's xmlids that
    # the data files did not touch — which is every record made by a hook. A noupdate
    # xmlid is left alone.
    env['ir.model.data']._update_xmlids([{'xml_id': THANKS_VIEW_KEY, 'record': view, 'noupdate': True}])
    _logger.info("lab_website: added the lab's sidebar to the contact thank-you page")


def _set_logos(env, website):
    """The lab's mark on the site header, the company and the installed app.

    Each is replaced only while it is still Odoo's own placeholder (the company's
    res_company_logo.png, the website's website_logo.svg - they differ) or while it
    is EMPTY. A logo somebody uploaded stays.

    Empty covers more than it looks. A binary field whose attachment row survives
    but whose file does not - which is what a database copied without its
    filestore gives you - reads back as empty rather than raising, because Odoo
    swallows the missing file. The header then falls back to the img alt text and
    a 95 pixel navbar box clips "Arabian Dental Lab" to "Arabian Dental La".
    Repairing it is the same write as setting it the first time.
    """
    import base64
    from odoo.tools import file_open
    with file_open('lab_website/static/src/img/logo-h.png', 'rb') as fh:
        lockup = base64.b64encode(fh.read())
    with file_open('lab_website/static/src/img/logo.png', 'rb') as fh:
        stacked = base64.b64encode(fh.read())
    with file_open('lab_website/static/src/img/icon-512.png', 'rb') as fh:
        app_icon = base64.b64encode(fh.read())
    company = env.company
    stock = company._get_logo() if hasattr(company, '_get_logo') else None
    if not company.logo or (stock and company.logo == stock):
        company.write({'logo': stacked})
        _logger.info("lab_website: set the company logo")
    site_stock = website._default_logo() if hasattr(website, '_default_logo') else None
    if not website.logo or website.logo in (stock, site_stock):
        website.write({'logo': lockup})
        _logger.info("lab_website: set the website logo")
    # The icon a doctor sees once they have added the site to a phone's home
    # screen. Square, because Android crops anything else to a circle.
    if 'pwa_icon' in website._fields and not website.pwa_icon:
        website.write({'pwa_icon': app_icon})
        _logger.info("lab_website: set the app icon")


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
    _set_logos(env, website)

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
