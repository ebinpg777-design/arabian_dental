# -*- coding: utf-8 -*-
"""A bench screen must be able to tell that it is behind.

Core broadcasts `bundle_changed` and its watchdog knows how to offer a Refresh, but
it only offers one when the payload's `server_version` differs from the session's —
and that is Odoo's release ("19.0"), which a module deploy never changes. So core
compares a constant to itself and the prompt can never appear for our deploys.

The floor reported the finisher popup as broken on 2026-09-09 for exactly this
reason: the server answered `needs_finisher` correctly and the morning's client,
which had never heard of that answer, showed it as a toast instead. (client)
"""
import json

from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestStalePage(HttpCase):

    def _versions(self):
        self.authenticate('admin', 'admin')
        response = self.url_open(
            '/lab_workcenter_scan/assets_version',
            data=json.dumps({'jsonrpc': '2.0', 'method': 'call', 'params': {}}),
            headers={'Content-Type': 'application/json'})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn('error', payload, payload.get('error'))
        return (payload.get('result') or {}).get('versions')

    def test_the_endpoint_names_the_current_bundle(self):
        """One call, and a screen knows what it ought to be running."""
        versions = self._versions()
        self.assertTrue(versions,
                        "the backend bundle must report at least one version")
        for version in versions:
            self.assertRegex(version, r'^[0-9a-f]+$',
                             "a bundle version is a plain hash")

    def test_the_answer_is_stable_while_nothing_changes(self):
        """Two calls in a row must not make a screen believe it went stale."""
        self.assertEqual(self._versions(), self._versions())

    def test_it_takes_a_login(self):
        """The board is back office; an anonymous caller gets nothing."""
        response = self.url_open(
            '/lab_workcenter_scan/assets_version',
            data=json.dumps({'jsonrpc': '2.0', 'method': 'call', 'params': {}}),
            headers={'Content-Type': 'application/json'})
        payload = response.json()
        self.assertIn('error', payload,
                      "an unauthenticated call must not be answered")

    def test_the_service_is_on_the_backend_bundle(self):
        """It has to load where the benches actually are."""
        import odoo.modules.module as module
        bundles = module.get_manifest('lab_workcenter_scan').get('assets', {})
        self.assertTrue(
            any('stale_page.js' in path
                for path in bundles.get('web.assets_backend', [])),
            "the stale-screen service belongs on the backend bundle")
