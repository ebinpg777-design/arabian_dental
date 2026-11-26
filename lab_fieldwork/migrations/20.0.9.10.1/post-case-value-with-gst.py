# -*- coding: utf-8 -*-
"""Every visit's Case Value counted again, GST included (models/lab_visit.py).

Told to the ORM rather than written in SQL: the trips and the monthly targets add
the visits' values up, and they are recounted only when the ORM is told the value
moved. A day sheet already closed keeps the figure it was closed with."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    orders = env['sale.order'].search([('visit_id', '!=', False)])
    orders.modified(['amount_total'])
    env.flush_all()
