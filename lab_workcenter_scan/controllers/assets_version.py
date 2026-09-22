# -*- coding: utf-8 -*-
"""What the server would serve right now, so a bench screen can tell it is behind."""
import logging
import re

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class AssetsVersion(http.Controller):

    # NOT readonly: reading the current version can be the thing that BUILDS it, and
    # a bundle generated on a read-only cursor raises instead of answering.
    @http.route('/lab_workcenter_scan/assets_version', type='jsonrpc', auth='user')
    def assets_version(self):
        """The hash(es) in the backend bundle's URLs, as the client would load them.

        `bundle_changed` says only that SOMETHING was rebuilt, and core's payload
        carries Odoo's release version rather than the bundle's — so a screen cannot
        tell from the broadcast alone whether it is the one out of date.
        """
        versions = []
        try:
            bundle = request.env['ir.qweb'].sudo()._get_asset_bundle(
                'web.assets_backend', assets_params={})
            for link in bundle.get_links():
                match = re.search(r'/web/assets/([^/]+)/', link or '')
                if match:
                    versions.append(match.group(1))
        except Exception:  # noqa: BLE001
            # A version we cannot read must never break the page that asked; the
            # client treats an empty answer as "say nothing".
            _logger.exception("could not read the current asset version")
            return {'versions': []}
        return {'versions': versions}
