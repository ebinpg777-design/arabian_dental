# -*- coding: utf-8 -*-
"""The public site must survive a root menu the visitor cannot see."""
from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestPublicLayoutSurvivesHiddenRootMenu(HttpCase):
    """On 2026-09-08 the site answered 500 on /, /contactus and /web/login.

    Menu 122 (Calendar) is a root menu with no action of its own, restricted to
    Role/User. For a visitor who is not logged in, `load_menus_root` returns it
    while `load_web_menus` correctly drops it, and website's override indexed
    the second by the first: `web_menus[menu['id']]['actionModel']` -> KeyError.

    These go through real anonymous HTTP rather than `with_user(public)`, which
    cannot read ir.ui.menu at all and so never reaches the code that broke.
    """

    def test_the_data_shape_that_caused_it_is_still_here(self):
        """If Calendar ever gains an action or loses its group, the pages below
        would pass for a reason that has nothing to do with the fix."""
        menu = self.env.ref('calendar.mail_menu_calendar', raise_if_not_found=False)
        if not menu:
            self.skipTest('calendar is not installed here')
        self.assertFalse(menu.parent_id, 'Calendar is a root menu')
        self.assertFalse(menu.action, 'with no action of its own')
        self.assertTrue(menu.group_ids, 'restricted to a group a visitor lacks')

    def test_the_pages_that_went_down_come_back_200(self):
        """The cache is cleared first: the crash only appeared once something
        asked for the force_action variant on a cold ormcache."""
        self.env.registry.clear_cache()
        for url in ('/', '/contactus', '/web/login'):
            self.assertEqual(self.url_open(url).status_code, 200, url)

    def test_it_keeps_working_on_the_warm_cache(self):
        """The forced pass copies the tree before touching it. Editing the
        ormcache's own dict would leave later readers with an action that was
        never theirs, so ask twice and expect the same answer."""
        self.env.registry.clear_cache()
        first = [self.url_open(u).status_code for u in ('/', '/web/login')]
        second = [self.url_open(u).status_code for u in ('/', '/web/login')]
        self.assertEqual(first, [200, 200])
        self.assertEqual(second, [200, 200], 'the warmed cache stopped serving')
