# -*- coding: utf-8 -*-
"""Fill purpose_ids / outcome_ids from the legacy Selection values.

ORM, not SQL: volumes are field-visit sized, and the sync helper is the single
definition of how a legacy value maps to a tag.
"""


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    visits = env['lab.visit'].with_context(active_test=False).search([])
    visits._sync_legacy_tags()
