# -*- coding: utf-8 -*-
{
    'name': 'Font Awesome 4.7 for Odoo 20',
    'version': '20.0.1.0.0',
    'category': 'Hidden/Tools',
    'summary': 'Brings back the Font Awesome 4.7 icons Odoo 20 no longer ships, for modules written for Odoo 19',
    'description': """
Odoo 20 replaced Font Awesome with Material Symbols and its own UI icons.
Modules written for Odoo 19 use ``fa fa-*`` classes in their views,
templates, reports and scripts, and ``icon="fa-*"`` on buttons; without
the stylesheet those render as nothing (or as the icon's name).

This module ships the Font Awesome 4.7 stylesheet and fonts, exactly as
Odoo 19 carried them (font: SIL OFL 1.1, CSS: MIT), for the backend, the
website and PDF reports, and renders ``icon="fa-*"`` buttons with it.
""",
    'author': 'Ebshel Technologies',
    'website': 'https://ebshel.com',
    'license': 'LGPL-3',
    'depends': ['web'],
    'assets': {
        'web.assets_backend': [
            'web_fontawesome_compat/static/src/fontawesome/css/font-awesome.css',
            'web_fontawesome_compat/static/src/js/view_button_patch.js',
        ],
        'web.assets_frontend': [
            'web_fontawesome_compat/static/src/fontawesome/css/font-awesome.css',
        ],
        'web.report_assets_common': [
            'web_fontawesome_compat/static/src/fontawesome/css/font-awesome.css',
        ],
        'web.assets_unit_tests': [
            'web_fontawesome_compat/static/src/fontawesome/css/font-awesome.css',
        ],
    },
    'auto_install': True,
    'installable': True,
}
