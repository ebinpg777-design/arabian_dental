# -*- coding: utf-8 -*-
from lxml import etree

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCashOnEntries(TransactionCase):
    """Bank and cash accounts on a journal entry's lines (client, 2026-10-22: "remove the
    domain account_type != 'asset_cash' of account in the journal item of journal entry")."""

    def _account_domain(self, list_path):
        arch = self.env['account.move'].get_view(
            self.env.ref('account.view_move_form').id, 'form')['arch']
        fields = etree.fromstring(arch).xpath(list_path)
        self.assertEqual(len(fields), 1, list_path)
        return fields[0].get('domain')

    def test_the_journal_items_list_offers_cash_accounts_while_the_switch_is_on(self):
        domain = self._account_domain("//page[@id='aml_tab']/field[@name='line_ids']/list/field[@name='account_id']")
        self.assertIn("parent.entry_cash_accounts", domain)
        self.assertIn("('company_ids', 'parent_of', company_id)", domain, "still this company's accounts only")
        # off, Odoo's own rule comes back word for word
        self.assertIn("[] if parent.entry_cash_accounts else [('account_type', '!=', 'asset_cash')]", domain)

    def test_invoice_lines_keep_odoos_own_list(self):
        domain = self._account_domain("//field[@name='invoice_line_ids']/list/field[@name='account_id']")
        self.assertNotIn('entry_cash_accounts', domain)
        self.assertIn('asset_cash', domain)

    def test_the_switch_is_on_by_default_and_per_company(self):
        company = self.env['res.company'].create({'name': 'Cash Entries Co'})
        self.assertTrue(company.entry_cash_accounts)
        settings = self.env['res.config.settings'].with_company(company).create({})
        settings.entry_cash_accounts = False
        settings.execute()
        self.assertFalse(company.entry_cash_accounts)
        self.assertTrue(self.env.company.entry_cash_accounts, "another company keeps its own setting")
        move = self.env['account.move'].with_company(company).new({'move_type': 'entry', 'company_id': company.id})
        self.assertFalse(move.entry_cash_accounts, "the entry form reads its company's switch")
