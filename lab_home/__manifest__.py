# -*- coding: utf-8 -*-
{
    'name': 'Home Screen',
    'version': '19.0.1.0.0',
    'summary': "The lab's own home screen: wallpaper and the warnings that belong there",
    'description': """
Home Screen
===========

Two things live on the screen every user lands on.

**The wallpaper.** The app grid sat on the theme's stock gradient. It now sits
on the lab's own photography, dimmed behind the icons so the app names stay
easy to read, with the wordmark in the corner.

**What is wrong.** A warning strip above the apps for the things nobody would
otherwise go looking for. The first of them is the nightly backup: if the last
run did not report success, it says so here rather than in a log under a second
menu, which is opened on the one day it is already too late.

Only users who can act on a warning are shown it.
""",
    'author': 'Arabian Dental Lab',
    'category': 'Technical',
    'license': 'LGPL-3',
    'depends': ['web', 'zxs_entp_theme'],
    'assets': {
        'web.assets_backend': [
            'lab_home/static/src/scss/home_screen.scss',
            'lab_home/static/src/js/home_screen.esm.js',
            'lab_home/static/src/xml/home_screen.xml',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
}
