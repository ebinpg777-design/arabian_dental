# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebin P G
# License OPL-1. See LICENSE file for full copyright and licensing details.
{
    'name': 'Excel Report Builder',
    "version": "19.0.0.3.0",
    'description': """
            Dynamic Excel Report Builder for Odoo.
            Build and export custom Excel reports for any Odoo model — no developer needed.
            Select fields including Many2one, Many2many, and related fields with ease.
            Apply domain filters, style your Excel output, and preview before downloading.
            Save templates for reuse, add computed columns, and control report structure fully.
            A powerful self-service reporting tool for business users.
    """,   
    'summary': """Dynamic Excel Report Builder, Custom Excel Report, Report Generator, Excel Export, 
            Report Builder, Custom Report, Model Report, Field Selector, Excel Styling, Report Template, 
            Saved Templates, Domain Filter, Report Preview, Editable Report, Column Computation, 
            Many2one Report, Many2many Report, Related Fields Export, Excel Download, Report Wizard, 
            Dynamic Report, Report Generator, Excel Builder, Custom Export, Advanced Export, 
            Data Export Excel, Report Customization, Flexible Report, Report Designer, 
            Excel Template Builder, Multi-Model Report, Field Selection Report, Computed Columns, 
            Report Access Control, User Report Builder, Odoo Excel, Odoo Export, 
            Excel Report Automation, Custom Column Report, Report Filter, Odoo Reporting Tool,
            Dynamic Field Report, Excel Sheet Generator, Report Widget, Interactive Report Builder, """,     
    'author': 'Ebin P G',
    'maintainer': 'Ebin P G',
    'category': 'Tools',
    "license": "OPL-1",
    "price": 149.00,
    "currency": "USD",
    'depends': ['base_setup', 'mail'],
    "data": [
        "security/security_groups.xml",
        "security/ir.model.access.csv",
        "security/record_rules.xml",
        "data/scheduled_cron.xml",
        "views/dynamic_export_template_views.xml",
        "views/excel_report_config_views.xml",
    ],
    'assets': {
        'web.assets_backend': [
            'excel_report_builder/static/src/xml/dialog_box_report.xml',
            'excel_report_builder/static/src/js/dialog_box_report.js',
            'excel_report_builder/static/src/css/dynamic_export.scss',
        ],
    },
    'images': [
        'static/description/banner.png',
        'static/description/icon.png',
    ],
    'installable': True,
    'application': True,
    "auto_install": False,
}