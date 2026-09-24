# -*- coding: utf-8 -*-
"""Replay the install hook: the company email is new, and the homepage view changed.
Idempotent — nothing typed in by hand is touched."""
from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    from odoo.addons.lab_website.hooks import post_init_hook
    post_init_hook(env)
