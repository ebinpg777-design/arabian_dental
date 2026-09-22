# -*- coding: utf-8 -*-
"""Give the shipped couriers their number patterns.

data/courier_data.xml is noupdate, so the patterns added there reach new databases
only. This sets them once, by xml id, on couriers that have none yet - a pattern an
administrator has typed in themselves is left alone. (client, 2026-08-28)
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

PATTERNS = {
    'courier_dtdc':         ('^(?=.*[A-Z])[A-Z0-9]{9,12}$',        'D12345678',     False),
    'courier_bluedart':     ('^[0-9]{8,11}$',           '12345678901',   False),
    'courier_professional': ('^[A-Z]{2,4}[0-9]{6,10}$', 'KLM123456',     False),
    'courier_delhivery':    ('^[0-9]{12,14}$',          '1234567890123', False),
    'courier_indiapost':    ('^[A-Z]{2}[0-9]{9}IN$',    'EK123456789IN', True),
}


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    for xmlid, (pattern, example, s10) in PATTERNS.items():
        courier = env.ref('lab_delivery.%s' % xmlid, raise_if_not_found=False)
        if not courier or courier.awb_pattern:
            continue
        courier.write({'awb_pattern': pattern, 'awb_example': example, 'awb_s10': s10})
        _logger.info("courier %s: pattern %s", courier.name, pattern)
