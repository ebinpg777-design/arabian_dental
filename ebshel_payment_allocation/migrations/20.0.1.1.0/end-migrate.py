# Part of ebshel_payment_allocation. See LICENSE file for full copyright and licensing details.
"""20.0.1.0.0 booked a confirmed payment's receivable or payable side as one journal item
per allocated invoice. From 20.0.1.1.0 it is one item, as Odoo books it, reconciled with
each invoice for its allocation - and the entries already booked are merged the same way,
reconciled with the same items for the same amounts.

An END script: it runs once every module is loaded. Posting the entries again needs the
whole registry - a database's own numbering rules (the lab's "PBNK1/2026-27/2221" year
ranges live in another module) are not there yet while this module's post scripts run."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["account.payment"]._merge_split_entries()
