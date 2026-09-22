# -*- coding: utf-8 -*-
"""Apply the shipped app icon to sites upgrading, not only to fresh installs.

`post_init_hook` runs on INSTALL. The lab's production already has this module
installed, so a `-u` would have carried the code and left the 82x63 logo behind
it — the exact thing the upgrade is being done to fix.
"""
from odoo.addons.lab_pwa.hooks import set_default_icon


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID
    set_default_icon(api.Environment(cr, SUPERUSER_ID, {}))
