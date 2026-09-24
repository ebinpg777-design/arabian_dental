# -*- coding: utf-8 -*-
"""Replay the install hook: it now sets the site and company logos where none was chosen. Idempotent."""
from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    from odoo.addons.lab_website.hooks import post_init_hook
    post_init_hook(env)
