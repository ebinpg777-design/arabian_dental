# -*- coding: utf-8 -*-
"""Everybody an order already names as a technician is a Technician (client,
2026-10-29: "Set the field for all partners those are added in any of the SOs"), so
the Technicians field, now offering technicians only, still offers each of them."""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE res_partner SET is_technician = TRUE
         WHERE id IN (SELECT DISTINCT partner_id FROM sale_order_technician_rel)
           AND is_technician IS NOT TRUE
    """)
    _logger.info("sale_custom: %s contact(s) named on orders marked Technician", cr.rowcount)
