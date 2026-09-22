# -*- coding: utf-8 -*-
"""Switch the active languages to dd/mm/yyyy.

The lab is in India: 08/09/2026 must read as 8 September. Odoo formats every date - on
screen and in every report - from the language's `date_format`, and English (US) ships
%m/%d/%Y. (Same logic as hooks.py, inlined: migration scripts are loaded standalone, so
they cannot import from their own module package.)
"""
import logging

_logger = logging.getLogger(__name__)

DATE_FORMAT = '%d/%m/%Y'


def migrate(cr, version):
    if not version:
        return
    cr.execute("UPDATE res_lang SET date_format = %s WHERE active AND date_format != %s RETURNING code",
               (DATE_FORMAT, DATE_FORMAT))
    codes = [r[0] for r in cr.fetchall()]
    if codes:
        _logger.info("date format set to %s for: %s", DATE_FORMAT, ', '.join(codes))
