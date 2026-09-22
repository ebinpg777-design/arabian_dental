# -*- coding: utf-8 -*-
"""The site is installable, and installing it cannot break the site.

Odoo's own PWA is scoped to /odoo, so these check the half core does not do:
a manifest at the root, a worker whose scope covers the whole site, and an
icon generated from the branding the lab already maintains.
"""
import json

from odoo.tests import HttpCase, TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestManifest(HttpCase):

    def _manifest(self):
        response = self.url_open('/manifest.webmanifest')
        self.assertEqual(response.status_code, 200)
        return response

    def test_the_manifest_is_served_at_the_root(self):
        response = self._manifest()
        self.assertIn('application/manifest+json',
                      response.headers.get('Content-Type', ''))

    def test_it_carries_what_a_phone_refuses_to_install_without(self):
        data = json.loads(self._manifest().content)
        for key in ('name', 'short_name', 'start_url', 'display', 'icons'):
            self.assertIn(key, data)
        self.assertEqual(data['display'], 'standalone')
        sizes = {icon['sizes'] for icon in data['icons']}
        self.assertIn('192x192', sizes, "Chrome requires a 192 icon")
        self.assertIn('512x512', sizes, "and a 512 for the splash screen")

    def test_the_scope_is_the_whole_site_not_the_back_office(self):
        """Core's manifest scopes itself to /odoo. If this one did too, the
        doctors it exists for would still be in a browser tab."""
        data = json.loads(self._manifest().content)
        self.assertEqual(data['scope'], '/')

    def test_the_app_has_a_stable_identity(self):
        """Without an explicit id a phone identifies the app by its start_url,
        so changing where it opens installs a second icon beside the first."""
        data = json.loads(self._manifest().content)
        self.assertTrue(data.get('id'))

    def test_a_long_name_is_left_whole_not_chopped(self):
        """Truncating at twelve turned "Arabian Dental Lab" into "Arabian Dent".
        Android ellipsises for itself; a server-side cut is just a typo.
        (2026-09-05)"""
        website = self.env['website'].search([], limit=1)
        website.write({'pwa_name': 'Arabian Dental Lab', 'pwa_short_name': False})
        data = json.loads(self._manifest().content)
        self.assertEqual(data['short_name'], 'Arabian Dental Lab')
        website.pwa_short_name = 'ADL'
        self.assertEqual(json.loads(self._manifest().content)['short_name'],
                         'ADL')
        website.write({'pwa_name': False, 'pwa_short_name': False})

    def test_it_opens_on_the_case_list_out_of_the_box(self):
        """The default was declared on a method the field never called, so
        every install opened the marketing home page. (2026-09-05)"""
        fresh = self.env['website'].new({})
        self.assertEqual(
            fresh.pwa_start_url,
            self.env['website']._pwa_default_start_url())

    def test_the_service_worker_can_control_the_whole_site(self):
        """A worker only governs what sits under the path it was served from,
        which is why it is at the root and not under /pwa/."""
        response = self.url_open('/service-worker.js')
        self.assertEqual(response.status_code, 200)
        self.assertIn('javascript', response.headers.get('Content-Type', ''))
        self.assertEqual(response.headers.get('Service-Worker-Allowed'), '/')
        body = response.content.decode()
        self.assertIn("addEventListener('fetch'", body,
                      "a worker with no fetch handler does not make a site "
                      "installable")

    def test_the_worker_leaves_everything_but_navigations_alone(self):
        """Odoo's assets are versioned by URL; a worker caching them serves
        last week's page and costs a day to explain."""
        body = self.url_open('/service-worker.js').content.decode()
        self.assertIn("request.mode !== 'navigate'", body)
        self.assertIn("request.method !== 'GET'", body)

    def test_the_offline_page_is_the_sites_own(self):
        response = self.url_open('/pwa/offline')
        self.assertEqual(response.status_code, 200)
        self.assertIn('No connection', response.content.decode())

    def test_the_icons_are_real_pngs_at_the_size_asked_for(self):
        """MEASURED, not declared. image_process only ever shrinks, so a
        192 asked of an 82x63 logo came back 63x63 while the manifest went on
        claiming 192x192 — and Chrome, which wants a real 192 or larger before
        it will offer to install, silently declined. The site had a valid
        manifest, a live worker and no Install button. (client, 2026-09-05)
        """
        import io
        from PIL import Image
        for size in (192, 512):
            response = self.url_open('/pwa/icon/%s.png' % size)
            self.assertEqual(response.status_code, 200, size)
            self.assertEqual(response.headers.get('Content-Type'), 'image/png')
            # PNG magic number: proof it is an image and not an error page.
            self.assertTrue(response.content.startswith(b'\x89PNG'), size)
            image = Image.open(io.BytesIO(response.content))
            self.assertEqual(image.size, (size, size),
                             "the file must be the size the manifest claims")

    def test_a_tiny_source_is_enlarged_rather_than_served_short(self):
        """The live logo is 82x63. Chrome's floor is 192."""
        import base64, io
        from PIL import Image
        website = self.env['website'].search([], limit=1)
        buf = io.BytesIO()
        Image.new('RGB', (82, 63), '#123456').save(buf, format='PNG')
        website.pwa_icon = base64.b64encode(buf.getvalue())
        response = self.url_open('/pwa/icon/192.png')
        self.assertEqual(
            Image.open(io.BytesIO(response.content)).size, (192, 192))
        website.pwa_icon = False

    def test_a_square_icon_is_not_shrunk_into_a_margin(self):
        """A designed square icon fills the canvas; only a wide logo is
        padded into the maskable safe zone."""
        import base64, io
        from PIL import Image
        website = self.env['website'].search([], limit=1)
        buf = io.BytesIO()
        Image.new('RGB', (512, 512), '#654321').save(buf, format='PNG')
        website.pwa_icon = base64.b64encode(buf.getvalue())
        image = Image.open(io.BytesIO(
            self.url_open('/pwa/icon/192.png').content)).convert('RGB')
        self.assertEqual(image.size, (192, 192))
        self.assertEqual(image.getpixel((4, 4)), (0x65, 0x43, 0x21),
                         "a square source reaches the corners")
        website.pwa_icon = False

    def test_an_ico_favicon_does_not_take_the_icon_down_with_it(self):
        """This lab's favicon is a 16x16 ICO, which Odoo's image_process
        refuses outright. Before the fallback chain that 404'd the icon and
        made the whole site uninstallable. (2026-09-05)"""
        website = self.env['website'].search([], limit=1)
        website.write({'pwa_icon': False})       # force it down the fallbacks
        self.assertEqual(self.url_open('/pwa/icon/192.png').status_code, 200)

    def test_a_site_with_no_usable_image_says_so_instead_of_guessing(self):
        website = self.env['website'].search([], limit=1)
        source, width = website._pwa_icon_source()
        if not source:
            self.assertIn('upload', (website.pwa_icon_warning or '').lower())
        else:
            self.assertGreater(width, 0)

    def test_an_unasked_for_size_is_not_generated_on_demand(self):
        """The manifest names two sizes. Anything else is a stranger asking the
        server to resize images for them."""
        self.assertEqual(self.url_open('/pwa/icon/1024.png').status_code, 404)

    def test_switching_it_off_removes_it_from_the_page(self):
        website = self.env['website'].search([], limit=1)
        website.pwa_enabled = True
        self.assertIn('manifest.webmanifest', self.url_open('/').content.decode())
        website.pwa_enabled = False
        self.assertNotIn('manifest.webmanifest',
                         self.url_open('/').content.decode())
        website.pwa_enabled = True


@tagged('post_install', '-at_install')
class TestPwaSettings(TransactionCase):

    def test_the_defaults_make_it_work_before_anyone_configures_it(self):
        website = self.env['website'].search([], limit=1)
        website.write({'pwa_name': False, 'pwa_short_name': False})
        values = website._pwa_values()
        self.assertEqual(values['name'], website.name)
        self.assertTrue(values['theme_color'].startswith('#'))
        self.assertTrue(values['start_url'].startswith('/'))

    def test_it_opens_where_the_doctors_are_when_that_screen_exists(self):
        """An icon opening the marketing home page is a bookmark, not an app."""
        website = self.env['website'].search([], limit=1)
        expected = '/my/cases' if self.env['ir.module.module'].sudo().search(
            [('name', '=', 'lab_portal'), ('state', '=', 'installed')],
            limit=1) else '/'
        self.assertEqual(website._pwa_default_start_url(), expected)

    def test_the_settings_screen_reaches_the_website_record(self):
        settings = self.env['res.config.settings'].create({})
        self.assertIn('pwa_enabled', settings._fields)
        self.assertEqual(tuple(settings._fields['pwa_enabled'].related.split('.')
                               if isinstance(settings._fields['pwa_enabled'].related, str)
                               else settings._fields['pwa_enabled'].related),
                         ('website_id', 'pwa_enabled'))

    def test_a_small_source_is_flagged_rather_than_shipped_blurry(self):
        """The live site's best image was 82x63. Blown up to the 512 a splash
        screen wants, that is a smear nobody notices until it is on a phone."""
        website = self.env['website'].search([], limit=1)
        website.pwa_icon = False
        website.invalidate_recordset(['pwa_icon_warning'])
        source, width = website._pwa_icon_source()
        if source and width < 512:
            self.assertTrue(website.pwa_icon_warning)
            self.assertIn(str(width), website.pwa_icon_warning)

    def test_a_proper_icon_clears_the_warning(self):
        import base64, io
        from PIL import Image
        buf = io.BytesIO()
        Image.new('RGB', (512, 512), '#714B67').save(buf, format='PNG')
        website = self.env['website'].search([], limit=1)
        website.pwa_icon = base64.b64encode(buf.getvalue())
        website.invalidate_recordset(['pwa_icon_warning'])
        self.assertFalse(website.pwa_icon_warning)
        source, width = website._pwa_icon_source()
        self.assertEqual(width, 512)
        website.pwa_icon = False

@tagged('post_install', '-at_install')
class TestTheWorkerActuallyStarts(TransactionCase):
    """The manifest can be perfect and the site still not installable.

    The bootstrap was first written as an Odoo service in the registry. The
    services registry is a back-office construct; a public website page does
    not reliably start it, so the worker never registered — silently, with
    every endpoint returning 200 and the site simply not offering to install.
    Verified in a browser afterwards: registered, scope '/', and an offline
    navigation served the site's own page. (2026-09-05)
    """

    def test_the_bootstrap_does_not_depend_on_the_services_registry(self):
        import odoo.modules.module as module
        from pathlib import Path
        path = Path(module.get_module_path('lab_pwa'))
        source = (path / 'static' / 'src' / 'js' / 'pwa.js').read_text()
        self.assertIn('start();', source,
                      "the bootstrap has to run when the bundle loads")
        self.assertNotIn('registry.category("services")', source,
                         "a public page may never start the services registry")
        self.assertIn('navigator.serviceWorker', source)

    def test_the_asset_is_declared_on_the_frontend_bundle(self):
        """In web.assets_frontend, not assets_backend: the doctors this is for
        never see the back office."""
        import odoo.modules.module as module
        manifest = module.get_manifest('lab_pwa')
        bundles = manifest.get('assets', {})
        self.assertIn('web.assets_frontend', bundles)
        self.assertNotIn('web.assets_backend', bundles)
        self.assertTrue(any('pwa.js' in path
                            for path in bundles['web.assets_frontend']))


@tagged('post_install', '-at_install')
class TestShippedIconAndMaskable(HttpCase):
    """The icon that ships with the module, and the two renditions.

    A single file declared 'any maskable' is one of the two done wrong: the
    plain icon should fill its plate, while a maskable one is cropped by the
    launcher to whatever shape it likes and must keep its art inside a safe
    circle. Measured on this lab's own mark, the shared file reached 405px
    from centre against a 338px conservative safe radius. (2026-09-05)
    """

    def test_the_module_ships_a_real_square_icon(self):
        import base64, io
        from PIL import Image
        from odoo.tools import file_open
        with file_open('lab_pwa/static/src/img/app_icon.png', 'rb') as fh:
            image = Image.open(io.BytesIO(fh.read()))
        self.assertEqual(image.size, (1024, 1024))
        self.assertEqual(image.format, 'PNG')

    def test_installing_gives_the_site_that_icon(self):
        """A deploy should be the whole job, not a deploy plus an upload."""
        website = self.env['website'].search([], limit=1)
        self.assertTrue(website.pwa_icon,
                        "the post-init hook must have filled it in")
        self.assertFalse(website.pwa_icon_warning,
                         "and a 1024 source leaves nothing to warn about")

    def test_both_renditions_are_served_at_the_right_size(self):
        import io
        from PIL import Image
        for size in (192, 512):
            for url in ('/pwa/icon/%s.png' % size,
                        '/pwa/icon/%s-maskable.png' % size):
                response = self.url_open(url)
                self.assertEqual(response.status_code, 200, url)
                self.assertEqual(
                    Image.open(io.BytesIO(response.content)).size, (size, size),
                    url)

    def test_the_manifest_offers_each_purpose_separately(self):
        import json
        data = json.loads(self.url_open('/manifest.webmanifest').content)
        purposes = {icon['purpose'] for icon in data['icons']}
        self.assertEqual(purposes, {'any', 'maskable'},
                         "one file cannot be correct for both")
        for icon in data['icons']:
            self.assertEqual(self.url_open(icon['src']).status_code, 200,
                             icon['src'])

    def test_the_maskable_art_stays_inside_the_crop_circle(self):
        """The whole reason the rendition exists: a launcher mask must not be
        able to shave the crown or the root tips."""
        import io
        import math
        from PIL import Image
        content = self.url_open('/pwa/icon/512-maskable.png').content
        image = Image.open(io.BytesIO(content)).convert('RGB')
        pixels = image.load()
        centre = image.width / 2
        # Ink is whatever DIFFERS from the plate, sampled at a corner — the
        # same rule the renderer pads by. Measuring "white" instead assumed an
        # orange plate, and read a white-plate icon as ink from edge to edge.
        plate = pixels[1, 1]
        farthest = 0
        for y in range(0, image.height, 2):
            for x in range(0, image.width, 2):
                pixel = pixels[x, y]
                if max(abs(pixel[i] - plate[i]) for i in range(3)) > 40:
                    farthest = max(farthest, math.hypot(x - centre, y - centre))
        self.assertLess(farthest, image.width * 0.33,
                        "art outside the conservative safe circle gets cropped")


@tagged('post_install', '-at_install')
class TestBackOfficeAppIdentity(HttpCase):
    """Installing from a /odoo page must not produce an app called Odoo.

    The back office links Odoo's own manifest, whose name comes from
    web.web_app_name (default "Odoo") and whose icons are hardcoded. The public
    site's manifest never reaches it, because the back office does not load the
    frontend layout — so the two had to be branded separately.
    (client, 2026-09-05)
    """

    def _backend_manifest(self):
        import json
        response = self.url_open('/web/manifest.webmanifest')
        self.assertEqual(response.status_code, 200)
        return json.loads(response.content)

    def test_the_back_office_app_wears_the_labs_name(self):
        website = self.env['website'].search([], limit=1)
        data = self._backend_manifest()
        self.assertNotEqual(data['name'], 'Odoo')
        self.assertEqual(data['name'], website._pwa_values()['name'])

    def test_the_back_office_app_wears_the_labs_icon(self):
        data = self._backend_manifest()
        sources = {icon['src'] for icon in data['icons']}
        self.assertTrue(all(src.startswith('/pwa/icon/') for src in sources),
                        "Odoo's own icon files must not be offered: %s" % sources)
        for src in sources:
            self.assertEqual(self.url_open(src).status_code, 200, src)

    def test_it_still_scopes_itself_to_the_back_office(self):
        """Identity only. If this started claiming '/' it would fight the
        public site's app for the same pages."""
        data = self._backend_manifest()
        self.assertEqual(data['scope'], '/odoo')
        self.assertEqual(data['start_url'], '/odoo')

    def test_turning_the_feature_off_leaves_core_alone(self):
        website = self.env['website'].search([], limit=1)
        website.pwa_enabled = False
        try:
            self.assertEqual(self._backend_manifest()['name'], 'Odoo')
        finally:
            website.pwa_enabled = True


@tagged('post_install', '-at_install')
class TestIconsSurviveACdn(HttpCase):
    """A CDN cached the icons and Chrome went on refusing to install.

    Cloudflare sat in front of the live site and served the pre-fix 63px files
    for hours after the deploy that fixed them (cf-cache-status: HIT, age:
    11552). The manifest promised 192x192, the bytes on the phone were 63x63,
    and Chrome — which wants a real 192 before it offers to install — declined.
    Nothing was wrong with the deploy. (client, 2026-09-05)
    """

    def test_every_icon_url_carries_the_icons_own_version(self):
        import json
        data = json.loads(self.url_open('/manifest.webmanifest').content)
        website = self.env['website'].search([], limit=1)
        stamp = website._pwa_icon_version()
        self.assertNotEqual(stamp, '0')
        for icon in data['icons']:
            self.assertIn('?v=%s' % stamp, icon['src'], icon['src'])

    def test_a_new_icon_is_a_new_url(self):
        """The whole point: changed bytes must not be reachable at an address
        something else has already cached."""
        import base64, io, json
        from PIL import Image
        website = self.env['website'].search([], limit=1)
        before = json.loads(self.url_open('/manifest.webmanifest').content)
        buf = io.BytesIO()
        Image.new('RGB', (512, 512), '#0055aa').save(buf, format='PNG')
        website.pwa_icon = base64.b64encode(buf.getvalue())
        after = json.loads(self.url_open('/manifest.webmanifest').content)
        self.assertNotEqual({i['src'] for i in before['icons']},
                            {i['src'] for i in after['icons']})
        website.pwa_icon = False

    def test_the_versioned_url_still_serves_the_icon(self):
        import io, json
        from PIL import Image
        data = json.loads(self.url_open('/manifest.webmanifest').content)
        for icon in data['icons']:
            response = self.url_open(icon['src'])
            self.assertEqual(response.status_code, 200, icon['src'])
            size = int(icon['sizes'].split('x')[0])
            self.assertEqual(
                Image.open(io.BytesIO(response.content)).size, (size, size))

    def test_the_manifest_itself_is_not_cached(self):
        """It is a few hundred bytes and it is what has to change first: cached
        for a day, a new icon version would not be read for a day."""
        response = self.url_open('/manifest.webmanifest')
        self.assertIn('no-cache', response.headers.get('Cache-Control', ''))

    def test_the_back_office_manifest_is_versioned_too(self):
        import json
        data = json.loads(self.url_open('/web/manifest.webmanifest').content)
        stamp = self.env['website'].search([], limit=1)._pwa_icon_version()
        for icon in data['icons']:
            self.assertIn('?v=%s' % stamp, icon['src'], icon['src'])


@tagged('post_install', '-at_install')
class TestTheTwoAppsAreDistinct(HttpCase):
    """Two installable apps live on this domain and they must not be confused.

    The lab pointed the public app's start_url at /odoo, so both manifests
    opened the same page under the same name. Core sets no `id` at all, so
    Chrome derived the back office's identity from its start_url — and the two
    apps then collided on identity as well as on name, which is exactly the
    situation where a phone starts arguing about whether an app can be
    installed or updated. (client, 2026-09-05)
    """

    def _manifest(self, url):
        import json
        response = self.url_open(url)
        self.assertEqual(response.status_code, 200, url)
        return json.loads(response.content)

    def test_the_back_office_app_names_itself(self):
        back = self._manifest('/web/manifest.webmanifest')
        self.assertTrue(back.get('id'), "core leaves this unset")

    def test_the_two_apps_have_different_identities(self):
        back = self._manifest('/web/manifest.webmanifest')
        front = self._manifest('/manifest.webmanifest')
        self.assertNotEqual(back['id'], front.get('id'))

    def test_each_keeps_its_own_scope(self):
        """The staff app owns /odoo; the public app owns the site. If the
        back office ever claimed '/' the two would fight over every page."""
        self.assertEqual(self._manifest('/web/manifest.webmanifest')['scope'],
                         '/odoo')
        self.assertEqual(self._manifest('/manifest.webmanifest')['scope'], '/')


@tagged('post_install', '-at_install')
class TestTheTwoAppsAreDistinct(HttpCase):
    """Two installable apps live on this domain and they must not be confused.

    The lab pointed the public app's start_url at /odoo, so both manifests
    opened the same page under the same name. Core sets no `id` at all, so
    Chrome derived the back office's identity from its start_url — and the two
    apps then collided on identity as well as on name, which is exactly the
    situation where a phone starts arguing about whether an app can be
    installed or updated. (client, 2026-09-05)
    """

    def _manifest(self, url):
        import json
        response = self.url_open(url)
        self.assertEqual(response.status_code, 200, url)
        return json.loads(response.content)

    def test_the_back_office_app_names_itself(self):
        back = self._manifest('/web/manifest.webmanifest')
        self.assertTrue(back.get('id'), "core leaves this unset")

    def test_the_two_apps_have_different_identities(self):
        back = self._manifest('/web/manifest.webmanifest')
        front = self._manifest('/manifest.webmanifest')
        self.assertNotEqual(back['id'], front.get('id'))

    def test_each_keeps_its_own_scope(self):
        """The staff app owns /odoo; the public app owns the site. If the
        back office ever claimed '/' the two would fight over every page."""
        self.assertEqual(self._manifest('/web/manifest.webmanifest')['scope'],
                         '/odoo')
        self.assertEqual(self._manifest('/manifest.webmanifest')['scope'], '/')


@tagged('post_install', '-at_install')
class TestInstallUiFollowsTheSpec(TransactionCase):
    """The install entry is back, built so it can never be a dead button.

    It was removed on 2026-09-05 because, shown always, most taps arrived with
    no offer in hand and it could only print a line of text. The written spec
    (2026-09-08) asks for it back with a real fallback: a help sheet whose
    words match the browser in use. Chrome withholds `beforeinstallprompt`
    when the app is installed, when it was dismissed recently, before its
    engagement heuristic is met, inside a WebView, and always on iOS — none of
    which a page can override, which is why the fallback is the feature.
    """

    def _source(self):
        import odoo.modules.module as module
        from pathlib import Path
        return (Path(module.get_module_path('lab_pwa'))
                / 'static' / 'src' / 'js' / 'pwa.js').read_text()

    def test_the_prompt_is_only_ever_called_from_a_click(self):
        """spec §6: never automatically."""
        source = self._source()
        self.assertIn('deferred.prompt()', source)
        # The only call site sits inside the click handler.
        before = source.split('deferred.prompt()')[0]
        self.assertIn('const onClick', before)
        self.assertIn('ev.preventDefault()', source)

    def test_a_tap_without_an_offer_shows_instructions(self):
        source = self._source()
        self.assertIn('if (!deferred)', source)
        self.assertIn('showSheet()', source)

    def test_each_platform_gets_its_own_words(self):
        source = self._source()
        for phrase in ('Add to Home Screen', 'Install app',
                       'Open in Chrome', 'address bar'):
            self.assertIn(phrase, source, phrase)

    def test_embedded_browsers_are_detected(self):
        """spec §9: WhatsApp, Facebook, Instagram, WebViews."""
        source = self._source()
        for token in ('FBAN', 'Instagram', 'WhatsApp', 'wv'):
            self.assertIn(token, source, token)

    def test_an_installed_app_shows_no_install_ui(self):
        """spec §7: detect standalone and stop."""
        source = self._source()
        self.assertIn('display-mode: standalone', source)
        self.assertIn('navigator.standalone', source)
        self.assertIn('if (standalone())', source)

    def test_appinstalled_takes_the_offer_away(self):
        source = self._source()
        self.assertIn("appinstalled", source)
        self.assertIn('hideEntries()', source)

    def test_diagnostics_are_quiet_unless_asked_for(self):
        """spec §14: no noisy production logging."""
        source = self._source()
        self.assertIn('pwa-debug', source)
        self.assertIn('window.odoo.debug', source)
        # Every log goes through the gate, so none can leak.
        self.assertNotIn('console.log(', source)
        self.assertEqual(source.count('console.info'), 1)

    def test_android_is_told_about_the_miui_shortcut_permission(self):
        """Reported from a Redmi on Android 15 where the web side was sound.
        MIUI refuses the shortcut unless Chrome may create one, and Chrome
        does not report the refusal — the install looks like it worked and no
        icon appears.

        Shown to every Android visitor because the phone cannot be identified:
        Chrome's UA on a Redmi carries a model code (23129RAA4G) and never the
        brand, so sniffing for "redmi" matches nothing. Verified against a real
        Redmi UA. (client, 2026-09-08)"""
        source = self._source()
        self.assertIn('Home screen shortcuts', source)
        self.assertIn('Xiaomi, Redmi and POCO', source)
        self.assertNotIn('function xiaomi()', source,
                         "brand sniffing cannot work and must not come back")

    def test_the_worker_is_registered_at_the_root(self):
        source = self._source()
        self.assertIn('"/service-worker.js", { scope: "/" }', source)


@tagged('post_install', '-at_install')
class TestPortalPagesCarryTheManifest(HttpCase):
    """spec §2: installing must not require a trip to /odoo."""

    def test_the_public_and_portal_pages_link_exactly_one_manifest(self):
        for url in ('/', '/web/login'):
            body = self.url_open(url).content.decode()
            self.assertEqual(body.count('rel="manifest"'), 1, url)
            self.assertIn('/manifest.webmanifest', body, url)

    def test_our_apple_touch_icon_declares_its_size(self):
        """Core's points at the favicon — a 16x16 ICO on this site. With no
        sizes on either, iOS may pick the 16px one for the home screen."""
        body = self.url_open('/').content.decode()
        self.assertIn('rel="apple-touch-icon" sizes="192x192"', body)
