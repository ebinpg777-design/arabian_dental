# -*- coding: utf-8 -*-
##############################################################################
#
#    Odoo ERP ,Open Source Management Solution
#    Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################
{
    'name': 'Payments for Multiple Vendors & Customers',
    'version': '19.0.0.0.3',
    'category': 'Accounting/Accounting',
    'summary': 'Create one payment and allocate it across multiple customers or vendors.',
    'description': """
Payments for Multiple Vendors & Customers
=========================================
Create a single customer receipt or vendor payment and split the counterpart accounting lines by partner, account, memo, and amount.
    """,
    'author': 'Ebshel Technologies',
    'maintainer': 'Ebshel Technologies',
    'website': 'https://ebshel.com',
    'license': 'OPL-1',
    'price': 49.00,
    'currency': 'USD',
    'depends': ['account'],
    'data': [
        'security/ir.model.access.csv',
        'views/account_payment_views.xml',
    ],
    'images': [
        'static/description/banner.png',
        'static/description/icon.png',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
}
