# -*- coding: utf-8 -*-
"""The held-case message's "Work" line printed the order's Appliance Type.

The lab's orders no longer have one - fixed / removable / clear retainer is an
orthodontics question (client, 2026-09-30) - so the placeholder would come out as
itself in a doctor's WhatsApp. The seed data is noupdate, which is why the stored
templates are rewritten here: the works, by name, are what "Work" means at a dental
lab. Anything else in a template the lab has edited is left as it is.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    changed = 0
    # the body is translated: every language's copy carries its own placeholder
    for lang in env['res.lang'].search([]).mapped('code'):
        Template = env['epg.whatsapp.template'].with_context(active_test=False, lang=lang)
        for template in Template.search([('body', 'like', '{{appliance_type}}')]):
            template.body = template.body.replace('{{appliance_type}}', '{{product_names}}')
            changed += 1
    _logger.info("lab_whatsapp: %s template(s) now name the works instead of the appliance type", changed)
