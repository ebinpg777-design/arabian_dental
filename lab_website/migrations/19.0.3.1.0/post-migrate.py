# -*- coding: utf-8 -*-
"""Replay the install hook: the contact thank-you view it creates was being removed by
the end-of-upgrade cleanup until its xmlid became noupdate. Idempotent."""
from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    from odoo.addons.lab_website.hooks import post_init_hook
    post_init_hook(env)
