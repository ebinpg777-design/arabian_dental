# -*- coding: utf-8 -*-
"""Put the logo back when the filestore has lost it.

`post_init_hook` sets the header logo, and it did - but a binary field lives in
the filestore, not in the database, so a copy of this database taken without its
filestore arrives with the attachment row and no file behind it. Odoo swallows
the missing file and the field reads back empty, so the header renders the img
alt text instead, and a 95 pixel navbar box clips "Arabian Dental Lab" to
"Arabian Dental La".

A hook does not run on an upgrade, which is why this is here. It calls the same
function the install does: it writes only where the logo is empty or is still
Odoo's own placeholder, so a database whose logo is fine is untouched, and one
that has had a logo uploaded by hand keeps it.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID
    from odoo.addons.lab_website.hooks import _set_logos

    env = api.Environment(cr, SUPERUSER_ID, {})
    website = env['website'].search([], limit=1)
    if not website:
        _logger.warning("lab_website: no website record to repair")
        return
    _set_logos(env, website)
