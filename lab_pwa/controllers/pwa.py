# -*- coding: utf-8 -*-
"""The three files a phone asks for before it will install a site.

Odoo ships all three already — for ``/odoo``. Core's manifest sets
``scope: '/odoo'`` and its service worker is served with
``Service-Worker-Allowed: /odoo``, so the back office is installable and the
public site, which is where the doctors are, is not. These are the public
site's own, at the root scope, and the two do not collide: a page under
``/odoo`` is matched by core's narrower scope.
"""
import base64
import io
import logging
import math

from odoo import http
from odoo.addons.web.controllers.webmanifest import WebManifest
from odoo.http import request
from PIL import Image, ImageChops

_logger = logging.getLogger(__name__)

ICON_SIZES = (192, 512)
# A phone re-reads the manifest rarely and the icons almost never; a day is long
# enough to be cheap and short enough that re-branding lands within a day.
ICON_MAX_AGE = 86400


def icon_entries(website):
    """The manifest's icon list, each URL stamped with the icon's version.

    Module level because the two manifests that need it do not share a base:
    the public one is this module's own controller, the back-office one
    extends Odoo's WebManifest.
    """
    stamp = website._pwa_icon_version()
    return [{
        'src': '/pwa/icon/%s.png?v=%s' % (size, stamp),
        'sizes': '%sx%s' % (size, size),
        'type': 'image/png',
        'purpose': 'any',
    } for size in ICON_SIZES] + [{
        'src': '/pwa/icon/%s-maskable.png?v=%s' % (size, stamp),
        'sizes': '%sx%s' % (size, size),
        'type': 'image/png',
        'purpose': 'maskable',
    } for size in ICON_SIZES]


class LabPwa(http.Controller):

    def _website(self):
        """The site being served, on routes that are and are not `website=True`.

        `request.website` is set by the website router and simply does not
        exist on a plain route - the icon route is not a page and should not
        pay for language prefixes to be a picture - so it falls back to the
        same lookup the router itself uses.
        """
        website = getattr(request, 'website', None)
        if website:
            return website.sudo()
        return request.env['website'].sudo().get_current_website()

    # ------------------------------------------------------------- manifest
    @http.route('/manifest.webmanifest', type='http', auth='public',
                methods=['GET'], website=True, sitemap=False, readonly=True)
    def manifest(self):
        """What the phone shows while installing, and what it installs to.

        `id` is set explicitly: without it a phone identifies the app by its
        start_url, so changing where the app opens would look like a different
        app and install a second icon beside the first.
        """
        website = self._website()
        values = website._pwa_values()
        manifest = {
            'id': '/?pwa=%s' % website.id,
            'name': values['name'],
            'short_name': values['short_name'],
            'description': website.company_id.name or values['name'],
            'scope': '/',
            'start_url': values['start_url'],
            'display': 'standalone',
            'orientation': 'portrait-primary',
            'background_color': '#ffffff',
            'theme_color': values['theme_color'],
            'lang': (request.env.lang or 'en_US').split('_')[0],
            'prefer_related_applications': False,
            # Two RENDITIONS, not two purposes on one file. Android crops a
            # maskable icon to whatever shape the launcher uses, so the art has
            # to sit inside a safe circle of 40% radius - while the plain icon
            # should fill its plate. Declaring one file as 'any maskable' means
            # one of those two is wrong, and it was the maskable one: measured,
            # the mark reached 405px from centre against a 338px conservative
            # safe radius, so an aggressive launcher mask shaved the crown and
            # the root tips. (client, 2026-09-05)
            'icons': icon_entries(website),
        }
        return request.make_json_response(
            manifest, [('Content-Type', 'application/manifest+json'),
                       # The manifest itself is a few hundred bytes and is the
                       # thing that has to change first: cached for a day, a
                       # new icon version would not be read for a day.
                       ('Cache-Control', 'no-cache')])



    # ---------------------------------------------------------------- icons
    @http.route('/pwa/icon/<int:size>-maskable.png', type='http', auth='public',
                methods=['GET'], sitemap=False, readonly=True)
    def icon_maskable(self, size, **kw):
        """The same art, pulled into the circle a launcher mask may crop to."""
        return self.icon(size, maskable=True)

    @http.route('/pwa/icon/<int:size>.png', type='http', auth='public',
                methods=['GET'], sitemap=False, readonly=True)
    def icon(self, size, maskable=False, **kw):
        """The site's own branding, at the size the phone asked for.

        Generated rather than shipped: an icon committed as a file is a picture
        of the branding on the day it was committed, and this one follows the
        favicon the lab already maintains.
        """
        if size not in ICON_SIZES:
            raise request.not_found()
        website = self._website()
        source, _width = website._pwa_icon_source()
        if not source:
            raise request.not_found()
        try:
            data = self._render_icon(source, size, website, maskable=maskable)
        except Exception:                                       # noqa: BLE001
            # A broken image must cost the icon, never the page that links it.
            _logger.warning("PWA icon could not be generated", exc_info=True)
            raise request.not_found()
        return request.make_response(io.BytesIO(data).read(), [
            ('Content-Type', 'image/png'),
            ('Cache-Control', 'public, max-age=%s' % ICON_MAX_AGE),
        ])

    def _render_icon(self, source, size, website, maskable=False):
        """A square PNG of EXACTLY `size` pixels, whatever the source was.

        Not image_process: Odoo's helper only ever shrinks. Asked for 192 from
        this lab's 82x63 logo it returned a 63x63 file while the manifest went
        on claiming 192x192 - and Chrome, which needs a real 192 or larger
        icon before it will offer to install anything, quietly declined. The
        site had a valid manifest, a live service worker and no Install button.
        (client, 2026-09-05)

        A source that is already square is taken as a designed icon and fills
        the canvas. Anything else is a logo, so it is fitted inside 80% and
        padded - which is also the safe zone a `maskable` icon needs before
        Android crops it to the launcher's shape.
        """
        image = Image.open(io.BytesIO(base64.b64decode(source)))
        image = image.convert('RGBA')
        plate = self._plate_colour(image)
        # Work from the ART, not the file. A source may already be a finished
        # icon carrying its own margin, and scaling the whole file to a
        # fraction then pads what was padded once already: the maskable
        # rendition came out at 53% of the circle - visibly a different icon
        # from its own square version. Trimming to the content first makes both
        # renditions land where they are meant to, whatever the source was.
        # (client, 2026-09-05)
        image = self._trim_to_art(image, plate)
        if maskable:
            # ANDROID's figure, not the web spec's. The maskable spec calls a
            # 40%-radius circle safe; Chrome hands the icon to Android as an
            # adaptive icon, which guarantees only the central 72 of 108 dp -
            # a radius of 33%. The smaller number is the one that governs the
            # phone this actually lands on. Scaled so the art's own farthest
            # pixel sits there, measured rather than guessed from a bounding
            # box whose corners this mark does not reach.
            ratio = self._maskable_ratio(image, plate, size)
        else:
            # 80%: a 10% margin all round, so nothing touches an edge and a
            # rounded-corner mask has something to bite on.
            box = int(size * 0.80)
            ratio = min(box / image.width, box / image.height)
        target = (max(1, round(image.width * ratio)),
                  max(1, round(image.height * ratio)))
        # LANCZOS both ways: this is usually an ENLARGEMENT, and the default
        # filter turns a small logo into porridge.
        image = image.resize(target, Image.LANCZOS)
        # The plate colour is SAMPLED from the source's own corners rather than
        # assumed white. An icon that already fills its own plate - the one this
        # module ships does - padded onto white becomes an orange square
        # floating in a white border, and its maskable rendition is then mostly
        # white canvas. Sampling makes the padding invisible. Falls back to
        # white, which is the manifest's own background_color. (2026-09-05)
        canvas = Image.new('RGBA', (size, size), plate)
        canvas.paste(image, ((size - target[0]) // 2, (size - target[1]) // 2),
                     image)
        out = io.BytesIO()
        canvas.save(out, format='PNG', optimize=True)
        return out.getvalue()

    def _trim_to_art(self, image, plate):
        """The source with its own plate cropped away, so what is left is art."""
        background = Image.new('RGBA', image.size, plate)
        diff = ImageChops.difference(image.convert('RGB'),
                                     background.convert('RGB'))
        box = diff.getbbox()
        return image.crop(box) if box else image

    def _maskable_ratio(self, art, plate, size):
        """Scale that puts the art's farthest INK pixel on the 40% safe radius.

        Per pixel, not per bounding box: this mark's corners are empty, and
        assuming they are full shrinks the icon by a fifth for nothing. Read on
        a 64x64 reduction so it stays a few thousand comparisons rather than a
        million, which is ample for choosing a scale factor.
        """
        probe = 64
        small = art.convert('RGB').resize((probe, probe), Image.BILINEAR)
        flat = Image.new('RGB', (probe, probe), plate[:3])
        ink = ImageChops.difference(small, flat).convert('L')
        pixels = ink.load()
        half = probe / 2
        reach = 0.0
        for y in range(probe):
            for x in range(probe):
                if pixels[x, y] > 40:
                    reach = max(reach, math.hypot(x + 0.5 - half,
                                                  y + 0.5 - half))
        if not reach:
            return (size * 0.80) / max(art.width, art.height)
        # Back into the art's own pixels, then into the target radius.
        reach = reach / probe * max(art.width, art.height)
        return (size * 0.33) / reach

    def _plate_colour(self, image):
        """The colour to pad with: the source's own, when it has one.

        Read from the four corners. They agree only when the artwork is a full
        plate; a logo on transparency or on white disagrees or comes back
        white, and either way white is the right answer for it.
        """
        corners = [image.getpixel(p) for p in (
            (0, 0), (image.width - 1, 0),
            (0, image.height - 1), (image.width - 1, image.height - 1))]
        if any(len(c) > 3 and c[3] < 250 for c in corners):
            return (255, 255, 255, 255)
        reds, greens, blues = zip(*[c[:3] for c in corners])
        if max(reds) - min(reds) > 12 or max(greens) - min(greens) > 12 \
                or max(blues) - min(blues) > 12:
            return (255, 255, 255, 255)
        return (corners[0][0], corners[0][1], corners[0][2], 255)

    # -------------------------------------------------------- service worker
    @http.route('/service-worker.js', type='http', auth='public',
                methods=['GET'], sitemap=False, readonly=True)
    def service_worker(self):
        """Served from the ROOT so its scope is the whole site.

        A service worker may only control paths under the directory it is
        served from, which is why this is not inside /pwa/ — and why installing
        the site as an app needs it: a phone refuses to install a page that has
        no worker able to answer for it.

        Deliberately thin. It answers NAVIGATIONS only, network-first, and falls
        back to a cached shell when the phone is offline. It does not cache
        Odoo's assets: those are versioned by URL and a stale copy served from a
        worker is the kind of bug that shows a doctor last week's page and takes
        a day to explain.
        """
        return request.make_response(SERVICE_WORKER, [
            ('Content-Type', 'text/javascript'),
            # Belt and braces: the scope is already '/' by virtue of the path.
            ('Service-Worker-Allowed', '/'),
            ('Cache-Control', 'no-cache'),
        ])

    @http.route('/pwa/offline', type='http', auth='public', website=True,
                sitemap=False, readonly=True)
    def offline(self):
        """The page the worker shows when the phone has no signal."""
        return request.render('lab_pwa.offline', {})


# The worker itself. Kept as a plain string rather than a static file because a
# file under /lab_pwa/static/ could only ever control /lab_pwa/static/.
SERVICE_WORKER = """
'use strict';
// Bump this to retire every previous cache in one go.
const CACHE = 'lab-pwa-v1';
const OFFLINE_URL = '/pwa/offline';

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE).then((cache) => cache.addAll([OFFLINE_URL]))
              .then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(
                keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
            .then(() => self.clients.claim())
    );
});

// Navigations only, and network first. Anything else - assets, XHR, POSTs -
// goes straight to the network untouched, which is what keeps a stale worker
// from ever serving yesterday's page.
self.addEventListener('fetch', (event) => {
    const request = event.request;
    if (request.method !== 'GET' || request.mode !== 'navigate') {
        return;
    }
    event.respondWith(
        fetch(request).catch(() => caches.match(OFFLINE_URL))
    );
});
"""


class LabBackendManifest(WebManifest):
    """Brand the BACK OFFICE's app too.

    Odoo's own manifest lives at /web/manifest.webmanifest and the back-office
    page links it directly, with the name read from `web.web_app_name`
    (default "Odoo") and the icons hardcoded to odoo-icon-*.png. So installing
    from any /odoo page produced an app called Odoo wearing Odoo's logo,
    whatever the public site's manifest said — the two never meet, because the
    back office never loads the frontend layout. (client, 2026-09-05)

    Only the identity is changed. The scope stays '/odoo', which is core's and
    is correct: this is the staff app, and it must not start claiming the
    public site's pages.
    """

    def _get_webmanifest(self):
        manifest = super()._get_webmanifest()
        try:
            website = request.env['website'].sudo().get_current_website()
        except Exception:                                       # noqa: BLE001
            return manifest
        if not website or not website.pwa_enabled:
            return manifest
        values = website._pwa_values()
        # An explicit id, which core does not set. Without one Chrome derives
        # the app's identity from start_url — so the back-office app is
        # identified as "/odoo", and a public app whose start_url is also
        # /odoo (which is what this lab configured) becomes a second app with
        # the same name opening the same page. Two identical icons, and a
        # WebAPK update path that has to guess which is which. Naming it
        # explicitly keeps the staff app one thing for good, whatever
        # start_url either side is pointed at. (client, 2026-09-05)
        manifest['id'] = '/odoo?app=backoffice'
        manifest['name'] = values['name']
        manifest['short_name'] = values['short_name']
        manifest['theme_color'] = values['theme_color']
        manifest['icons'] = icon_entries(website)
        return manifest

    def _icon_path(self):
        """The lab's mark on the back office's offline page as well."""
        return 'lab_pwa/static/src/img/app_icon.png'
