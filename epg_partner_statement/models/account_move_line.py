# -*- coding: utf-8 -*-
from odoo import api, fields, models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    # Every opening-balance line is dated 2026-04-01, because that is when the
    # Odoo 10 position was brought in - not when the item arose. On a statement
    # that reads as if a doctor's whole history started on one April morning, so
    # this keeps the date the document actually carried in the old system.
    # Distinct from date_maturity, which is when the item fell DUE: the two differ
    # wherever credit terms applied. (client, 2026-08-22)
    original_date = fields.Date(
        string="Document Date",
        index='btree_not_null',
        copy=False,
        help="The date this item carried in the system it came from. Set on "
             "opening-balance lines migrated from Odoo 10, where the entry date "
             "is the migration date rather than the document's own. Statements "
             "show this in place of the entry date when it is set.",
    )

    # The date the STATEMENT works on: the document's own date wherever one was carried
    # over, otherwise the accounting date.
    #
    # The statement already SHOWED `original_date or date` while FILTERING on `date`, so
    # a migrated opening item printed as 2019 and was included or excluded by its 2026
    # migration date. A period could therefore contain a line dated years outside it,
    # and "last month" could miss one that reads as last month. Stored and indexed
    # rather than computed in Python, because the period filter, the sort and the
    # opening-balance cut all have to run in the database. (client, 2026-08-27)
    statement_date = fields.Date(
        string="Statement Date", compute="_compute_statement_date", store=True,
        index='btree', readonly=True,
        help="The date this item is filtered and sorted by on a statement of account: "
             "the Document Date where one is set, otherwise the accounting date.")

    @api.depends('original_date', 'date')
    def _compute_statement_date(self):
        for line in self:
            line.statement_date = line.original_date or line.date
