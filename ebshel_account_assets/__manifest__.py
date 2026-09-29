# -*- coding: utf-8 -*-
{
    'name': 'Assets and Depreciation',
    'version': '19.0.1.0.0',
    'category': 'Accounting/Accounting',
    'summary': 'Fixed assets from purchase to disposal: schedules, automatic depreciation, '
               'pauses, revaluations, gross increases, disposals with gain or loss, assets '
               'from vendor bills, custody and cover, a lifecycle timeline, QR labels and a '
               'depreciation schedule report',
    'description': """
Assets and Depreciation
=======================
* Categories carry the accounts, the journal and the method (straight line,
  declining balance, declining then straight line), the period (month / year),
  the duration, the prorata rule and the default salvage.
* An asset builds its depreciation schedule, posts it entry by entry or by the
  nightly job, pauses and resumes, is modified (longer, shorter, a new salvage,
  a revaluation as a gross-increase child, an impairment), and is disposed of by
  sale, scrap, donation or loss with the gain or loss posted.
* Vendor bills create assets by themselves when a line lands on an asset
  account - as drafts to review or already running - one per line or one per unit.
* Custody: who holds it, where it is, its serial, warranty and insurance dates,
  with reminders before cover runs out. A QR label per asset.
* A lifecycle timeline and a depreciation curve on every asset; a dashboard of
  book value by category, this year's additions, disposals and charge, next
  month's charge and expiring cover; a method simulator; and the depreciation
  schedule as a dynamic report.
""",
    'author': 'Ebshel Technologies',
    'website': 'https://ebshel.com',
    'support': 'ebinpg777@gmail.com',
    'license': 'OPL-1',
    'price': 149.00,
    'currency': 'USD',
    'depends': ['account', 'ebshel_account_reports'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/sequence.xml',
        'data/cron.xml',
        'data/reports.xml',
        'wizard/asset_change_views.xml',
        'wizard/asset_dispose_views.xml',
        'wizard/asset_simulate_views.xml',
        'views/asset_category_views.xml',
        'views/asset_views.xml',
        'views/account_move_views.xml',
        'report/asset_reports.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'ebshel_account_assets/static/src/widget/lifecycle.scss',
            'ebshel_account_assets/static/src/widget/lifecycle.js',
            'ebshel_account_assets/static/src/widget/lifecycle.xml',
            'ebshel_account_assets/static/src/dashboard/dashboard.scss',
            'ebshel_account_assets/static/src/dashboard/dashboard.js',
            'ebshel_account_assets/static/src/dashboard/dashboard.xml',
        ],
    },
    'application': True,
    'installable': True,
}
