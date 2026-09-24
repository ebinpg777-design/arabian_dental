# -*- coding: utf-8 -*-
"""The warning strip on the home screen.

What the backup warning *says* is the backup module's own business and is tested
there. What is asserted here is the strip itself: who is shown it, and that a
warning which breaks cannot take the home screen down with it, because this runs
inside session_info and session_info runs on every single page load.
"""
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestHomeAlerts(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.IrHttp = cls.env['ir.http']
        cls.klass = type(cls.IrHttp)
        cls.alert = {'id': 'backup_failed', 'level': 'danger',
                     'title': 'The last database backup did not work'}
        cls.clerk = cls.env['res.users'].create({
            'name': 'Store keeper', 'login': 'home.screen.clerk',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })

    def _alerts(self, user=None):
        http = self.IrHttp.with_user(user) if user else self.IrHttp
        return http._home_alerts()

    # ------------------------------------------------------------ who sees it
    def test_an_administrator_is_shown_what_a_builder_reports(self):
        with patch.object(self.klass, '_home_alert_backup', lambda _self: self.alert):
            self.assertEqual(self._alerts(), [self.alert])

    def test_an_ordinary_user_is_shown_nothing(self):
        """Not a matter of taste: this one names the state of the database's
        backups, and a warning shown to somebody who cannot act on it is noise."""
        with patch.object(self.klass, '_home_alert_backup', lambda _self: self.alert):
            self.assertEqual(self._alerts(user=self.clerk), [])

    def test_nothing_to_report_is_an_empty_strip(self):
        with patch.object(self.klass, '_home_alert_backup', lambda _self: None):
            self.assertEqual(self._alerts(), [])

    # -------------------------------------------------------- when it breaks
    def test_a_broken_warning_does_not_take_the_home_screen_down(self):
        """The direction that matters. session_info runs on every page load, so
        an exception here is not a missing warning, it is a client that will not
        start."""
        def boom(_self):
            raise ValueError("the log moved")

        with patch.object(self.klass, '_home_alert_backup', boom):
            self.assertEqual(self._alerts(), [])

    # --------------------------------------------------------- the real thing
    def test_the_backup_warning_is_asked_for_by_name(self):
        """lab_home does not depend on the backup module - it asks the log model
        for its own warning if that model is installed at all. Whether the
        wording is right is tested where the wording lives."""
        if 'auto.database.backup.status' in self.env:
            self.assertIsNotNone(
                getattr(self.env['auto.database.backup.status'], '_home_alert', None),
                "the installed backup module must answer what the home screen asks")
        else:
            self.assertIsNone(self.IrHttp._home_alert_backup())


@tagged('post_install', '-at_install')
class TestWallpaper(TransactionCase):
    """Which picture the home screen shows.

    A setting rather than a preference: the home screen is the lab's front door
    and should look the same to everyone who walks through it.
    """

    def setUp(self):
        super().setUp()
        self.IrHttp = self.env['ir.http']
        self.param = self.env['ir.config_parameter'].sudo()

    def _wallpaper(self, value=None):
        if value is None:
            self.param.search([('key', '=', 'lab_home.wallpaper')]).unlink()
        else:
            self.param.set_param('lab_home.wallpaper', value)
        return self.IrHttp._home_wallpaper()

    def test_nothing_set_gives_the_default(self):
        self.assertEqual(self._wallpaper()['name'], 'ceramic')

    def test_a_name_that_was_typed_carelessly_still_gives_a_wallpaper(self):
        """The direction that matters: a name nobody shipped must not leave the
        home screen with no background at all."""
        for typo in ('Midnight ', 'midnigth', 'wallpaper-midnight.jpg', ''):
            self.assertIn(self._wallpaper(typo)['name'],
                          self.IrHttp.WALLPAPERS, typo)

    def test_the_names_that_were_shipped_are_taken_as_given(self):
        for name in self.IrHttp.WALLPAPERS:
            self.assertEqual(self._wallpaper(name)['name'], name)

    def test_each_wallpaper_says_whether_it_needs_light_captions(self):
        """Captions are dark by default. A dark wallpaper that forgot to say so
        would put dark grey app names on a near-black background."""
        self.assertTrue(self._wallpaper('midnight')['dark'])
        self.assertFalse(self._wallpaper('studio')['dark'])

    def test_every_wallpaper_named_here_is_a_file_that_exists(self):
        import os
        from odoo.modules.module import get_module_path
        folder = os.path.join(get_module_path('lab_home'), 'static', 'src', 'img')
        for name in self.IrHttp.WALLPAPERS:
            self.assertTrue(
                os.path.exists(os.path.join(folder, 'wallpaper-%s.jpg' % name)),
                "wallpaper-%s.jpg is offered but not shipped" % name)
