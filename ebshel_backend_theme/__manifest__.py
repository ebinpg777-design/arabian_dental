# -*- coding: utf-8 -*-
{
    'name': 'Ebshel Backend Theme',

    'summary': 'The backend look: home menu app grid, a greeting, gradient action buttons, '
               'WhatsApp-green WhatsApp buttons, card forms, light and dark mode.',

    'description': """
Ebshel Backend Theme
====================

The web client every user of the lab works in.

* Home menu app grid on login, with drag-and-drop app ordering and a greeting
  ("Good morning, Anil") over today's date
* Global search from the home menu (opens the command palette), Ctrl+K / Cmd+K
  anywhere in the backend
* Navigation bar with the app grid toggle and a thin line of the lab's red under it
* Main action buttons in a soft gradient with a glow; every other button white
  with a hairline
* Every button that sends a WhatsApp, whichever module adds it, in WhatsApp's
  green-to-teal gradient
* Forms as a card on a soft canvas, underlined notebook tabs, the current status
  step in charcoal, list headers and kanban cards that answer the pointer
* Light, Dark, and System theme per user preference

Built on the "Enterprise Theme for Community" module by Zarixsol
(https://www.zarixsol.com, LGPL-3), which is where the home menu, the navbar and
the colour scheme switch come from.

Do not install together with Odoo Enterprise ``web_enterprise`` (conflicting web client).
    """,

    'author': 'Ebshel Technologies, Zarixsol',
    'website': 'https://ebshel.com',

    'version': '20.0.1.1.0',
    'license': 'LGPL-3',
    'category': 'Productivity',

    'depends': [
        'web',
        'base_setup',
    ],

    'data': [
        'views/webclient_templates.xml',
        'views/res_users_views.xml',
    ],

    'assets': {
        'web._assets_primary_variables': [
            ('after', 'web/static/src/scss/primary_variables.scss', 'ebshel_backend_theme/static/src/**/*.variables.scss'),
            ('before', 'web/static/src/scss/primary_variables.scss', 'ebshel_backend_theme/static/src/scss/primary_variables.scss'),
        ],
        'web._assets_secondary_variables': [
            ('before', 'web/static/src/scss/secondary_variables.scss', 'ebshel_backend_theme/static/src/scss/secondary_variables.scss'),
        ],
        'web._assets_backend_helpers': [
            ('before', 'web/static/src/scss/bootstrap_overridden.scss', 'ebshel_backend_theme/static/src/scss/bootstrap_overridden.scss'),
        ],
        'web.assets_frontend': [
            'ebshel_backend_theme/static/src/webclient/home_menu/home_menu_background.scss',
            'ebshel_backend_theme/static/src/webclient/navbar/navbar.scss',
        ],
        'web.assets_backend': [
            'ebshel_backend_theme/static/src/webclient/**/*.scss',
            # the component layer goes after the theme's own rules, which it reshapes
            'ebshel_backend_theme/static/src/scss/backend.scss',
            'ebshel_backend_theme/static/src/views/**/*.scss',
            'ebshel_backend_theme/static/src/core/**/*.scss',
            'ebshel_backend_theme/static/src/search/**/*.scss',
            'ebshel_backend_theme/static/src/core/**/*',
            'ebshel_backend_theme/static/src/webclient/**/*.js',
            'ebshel_backend_theme/static/src/webclient/**/*.xml',
            ('remove', 'ebshel_backend_theme/static/src/views/pivot/**'),
            ('remove', 'ebshel_backend_theme/static/src/**/*.dark.scss'),
        ],
        'web.assets_backend_lazy': [
            'ebshel_backend_theme/static/src/views/pivot/**',
        ],
        'web.assets_backend_lazy_dark': [
            ('include', 'web.dark_mode_variables'),
            ('before', 'ebshel_backend_theme/static/src/scss/bootstrap_overridden.scss', 'ebshel_backend_theme/static/src/scss/bootstrap_overridden.dark.scss'),
            ('after', 'web/static/lib/bootstrap/scss/_functions.scss', 'ebshel_backend_theme/static/src/scss/bs_functions_overridden.dark.scss'),
        ],
        'web.assets_web': [
            ('replace', 'web/static/src/main.js', 'ebshel_backend_theme/static/src/main.js'),
        ],
        'web.dark_mode_variables': [
            ('before', 'ebshel_backend_theme/static/src/scss/primary_variables.scss', 'ebshel_backend_theme/static/src/scss/primary_variables.dark.scss'),
            ('before', 'ebshel_backend_theme/static/src/**/*.variables.scss', 'ebshel_backend_theme/static/src/**/*.variables.dark.scss'),
            ('before', 'ebshel_backend_theme/static/src/scss/secondary_variables.scss', 'ebshel_backend_theme/static/src/scss/secondary_variables.dark.scss'),
        ],
        'web.assets_web_dark': [
            ('include', 'web.dark_mode_variables'),
            ('before', 'ebshel_backend_theme/static/src/scss/bootstrap_overridden.scss', 'ebshel_backend_theme/static/src/scss/bootstrap_overridden.dark.scss'),
            ('after', 'web/static/lib/bootstrap/scss/_functions.scss', 'ebshel_backend_theme/static/src/scss/bs_functions_overridden.dark.scss'),
            'ebshel_backend_theme/static/src/**/*.dark.scss',
        ],
    },

    'images': ['static/description/banner.png'],
    'installable': True,
    'application': False,
    'auto_install': False,
}
