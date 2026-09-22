# -*- coding: utf-8 -*-
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestTimezoneGuard(TransactionCase):
    """A timezone PostgreSQL cannot use takes down every view that groups by date."""

    def _pg_knows(self, tz):
        self.env.cr.execute("SELECT 1 FROM pg_timezone_names WHERE name = %s", (tz,))
        return bool(self.env.cr.fetchone())

    def test_a_legacy_alias_is_stored_under_its_current_name(self):
        """Asia/Calcutta and Asia/Kolkata are the same zone. Python accepts the old
        name, Odoo offers it, and PostgreSQL 16+ refuses it — so it is substituted rather
        than rejected: the user's intent is correct, only the spelling is not.

        Substituted on EVERY server, including one whose PostgreSQL still lists the
        alias (staging runs 14): the database is restored onto servers that do not."""
        user = self.env['res.users'].create({
            'name': 'TZ Test', 'login': 'tz_test', 'tz': 'Asia/Calcutta'})
        self.assertEqual(user.tz, 'Asia/Kolkata')
        self.assertTrue(self._pg_knows(user.tz))

        user.tz = 'Asia/Calcutta'
        self.assertEqual(user.tz, 'Asia/Kolkata', "a later write must be caught too")

    def test_the_substituted_zone_actually_works_in_sql(self):
        """The point of the guard, proven the way the failure happened."""
        user = self.env['res.users'].create({
            'name': 'TZ SQL', 'login': 'tz_sql', 'tz': 'Asia/Calcutta'})
        self.env.cr.execute("SELECT now() AT TIME ZONE %s", (user.tz,))
        self.assertTrue(self.env.cr.fetchone())

    def test_partners_are_covered_too(self):
        """res.partner carries its own tz, and the same query reads it."""
        partner = self.env['res.partner'].create({
            'name': 'TZ Partner', 'tz': 'Asia/Calcutta'})
        self.assertEqual(partner.tz, 'Asia/Kolkata')

    def test_the_alias_is_replaced_even_where_this_postgres_still_accepts_it(self):
        """The failure this guards against happens on the NEXT server, not this one."""
        norm = self.env['lab.tz.normaliser']
        with patch.object(type(norm), '_pg_timezones',
                          lambda self: {'Asia/Calcutta', 'Asia/Kolkata'}):
            self.assertEqual(norm._normalise_tz({'tz': 'Asia/Calcutta'})['tz'],
                             'Asia/Kolkata')
        with patch.object(type(norm), '_pg_timezones', lambda self: {'Asia/Calcutta'}):
            self.assertEqual(norm._normalise_tz({'tz': 'Asia/Calcutta'})['tz'],
                             'Asia/Calcutta',
                             "never swap to a name this server cannot use either")

    def test_a_good_timezone_is_left_exactly_as_it_is(self):
        user = self.env['res.users'].create({
            'name': 'TZ Fine', 'login': 'tz_fine', 'tz': 'Europe/London'})
        self.assertEqual(user.tz, 'Europe/London')

    def test_an_unknown_zone_is_not_guessed_at(self):
        """Substituting something plausible would silently move somebody's working day.

        Tested on the normaliser directly: `tz` is a Selection built from pytz, so an
        arbitrary string cannot reach it through a write at all — which also means the
        only values the guard ever sees are real zone names, and the alias table is the
        whole of the problem.
        """
        norm = self.env['lab.tz.normaliser']
        self.assertEqual(norm._normalise_tz({'tz': 'Mars/Olympus'})['tz'],
                         'Mars/Olympus')
        self.assertNotIn('tz', norm._normalise_tz({'name': 'no tz here'}))
