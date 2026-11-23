# -*- coding: utf-8 -*-
"""Field Live stepping from day to day, run outside a browser.

With a person in focus, a day with no positions (a day off) left its empty trail held,
and the next day picked was drawn with it: doors, halts and story, no line (client,
2026-11-23: "the map route is not available for Abdul Latheef in some days" - the
server sent his full route every one of those days). Stepping back a day also kept the
later day's roads for everybody. `js/field_live_day_harness.js` runs the real LiveMap
and FieldLive classes in Node against a fake server through those steps.
"""
import os
import shutil
import subprocess

from odoo.tests import TransactionCase, tagged

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, 'js', 'field_live_day_harness.js')
JS = os.path.join(os.path.dirname(HERE), 'static', 'src', 'js')


@tagged('post_install', '-at_install')
class TestFieldLiveDays(TransactionCase):

    def test_each_day_picked_draws_its_own_trail_and_roads(self):
        node = shutil.which('node') or shutil.which('nodejs')
        if not node:
            self.skipTest('no Node.js on this machine to run Field Live in')
        run = subprocess.run([node, HARNESS, os.path.join(JS, 'live_map.js'),
                              os.path.join(JS, 'field_live.js')],
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('ok   the day after a day off draws its own trail', run.stdout)
        self.assertNotIn('FAIL', run.stdout)
