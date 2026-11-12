# -*- coding: utf-8 -*-
"""The browser recorder itself, run outside a browser.

Everything the server knows of a browser's day is what `live_track.js` chose to send,
and nothing checked that file but a phone on the road. On waking after a spell in a
pocket its heartbeat - "still here", the last position seen, stamped now - went out
for the place the phone had left, a second before the true fix: on the server an
impossible jump, and the true fixes thrown away (client, 2026-11-12: "GPS has some
issues in the map and KM"). `js/live_track_harness.js` runs the recorder in Node
with a fake clock and a fake phone through those wakings, and through the stop it
must still report while it is watching.
"""
import os
import shutil
import subprocess

from odoo.tests import TransactionCase, tagged

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, 'js', 'live_track_harness.js')
RECORDER = os.path.join(os.path.dirname(HERE), 'static', 'src', 'js', 'live_track.js')


@tagged('post_install', '-at_install')
class TestLiveTrackRecorder(TransactionCase):

    def test_the_recorder_repeats_no_place_it_has_not_seen_since_the_page_slept(self):
        node = shutil.which('node') or shutil.which('nodejs')
        if not node:
            self.skipTest('no Node.js on this machine to run the recorder in')
        run = subprocess.run([node, HARNESS, RECORDER], capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('ok   woken, timer first', run.stdout)
        self.assertNotIn('FAIL', run.stdout)
