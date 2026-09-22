# -*- coding: utf-8 -*-
"""Delivering an order no longer completes its manufacturing orders.

The switch now defaults to off, and it is written as off here once, so a database
that never set it - production - stops closing MOs the moment it is upgraded,
along with the scheduled catch-up that uses the same switch. Setting it back to
1 turns both on again. (client, 2026-09-17)
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['ir.config_parameter'].set_param('lab_workcenter_scan.close_mo_on_delivery', '0')
