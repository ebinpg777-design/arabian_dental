# -*- coding: utf-8 -*-
"""The install hook gained the contact thank-you sidebar and the company blanks after
the first installs; a hook only runs on install, so an upgrade replays it here. It is
idempotent — nothing typed in by hand is touched."""
from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    from odoo.addons.lab_website.hooks import post_init_hook
    post_init_hook(env)
