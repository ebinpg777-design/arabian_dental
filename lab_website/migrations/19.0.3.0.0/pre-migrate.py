# -*- coding: utf-8 -*-
"""The site was rebuilt with a new page set, so the old menu goes.

The menus are noupdate data: an upgrade leaves the existing rows alone, which would
keep "What We Make -> /restorations" pointing at a page that no longer exists. Odoo also
copies every generic menu into each website as a plain row (website_id set, no link
back), so those copies have to go by URL. Removing both lets the new tree load as if
for the first time. A menu the lab added by hand at another URL is not touched.
"""

OLD_URLS = ('/restorations', '/appliances', '/academy', '/about-us', '/my/cases')


def migrate(cr, version):
    cr.execute("""
        SELECT res_id FROM ir_model_data
        WHERE module = 'lab_website' AND model = 'website.menu'
    """)
    ids = [r[0] for r in cr.fetchall()]
    cr.execute("SELECT id FROM website_menu WHERE url IN %s", (OLD_URLS,))
    ids += [r[0] for r in cr.fetchall()]
    if ids:
        cr.execute("DELETE FROM website_menu WHERE id = ANY(%s)", (ids,))
    cr.execute("DELETE FROM ir_model_data WHERE module = 'lab_website' AND model = 'website.menu'")
