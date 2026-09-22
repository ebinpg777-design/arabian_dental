# -*- coding: utf-8 -*-
"""Give the site a real app icon on install, so a deploy is the whole job.

The images a website already has are the wrong ones for this: a favicon is
16x16, a logo is a wide strip, and neither survives being blown up to the 512
a splash screen wants. Rather than leave the lab with a warning and a manual
upload, the module ships the mark cut square and sets it once — only when
nothing has been chosen, so it can never overwrite a decision somebody made.
"""
import base64
import logging

from odoo.tools import file_open

_logger = logging.getLogger(__name__)


def set_default_icon(env):
    websites = env['website'].sudo().search([('pwa_icon', '=', False)])
    if not websites:
        return
    try:
        with file_open('lab_pwa/static/src/img/app_icon.png', 'rb') as handle:
            icon = base64.b64encode(handle.read())
    except OSError:
        _logger.warning("lab_pwa: shipped app icon is missing")
        return
    websites.write({'pwa_icon': icon})
    _logger.info("lab_pwa: set the default app icon on %s website(s)",
                 len(websites))
