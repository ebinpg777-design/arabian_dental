# -*- coding: utf-8 -*-
"""What the installed app is called, where it opens, and what colour it wears.

On `website` rather than in ir.config_parameter: a database can serve several
sites, and an app installed from one of them is that site's app. The defaults
are chosen so the module does something sensible the moment it is installed and
nothing has been configured yet.
"""
import base64
import hashlib
import io

from PIL import Image

from odoo import _, api, fields, models

# The lab's own plum, matching the back office's manifest so a staff member with
# both installed does not get two different-coloured status bars.
DEFAULT_THEME = '#714B67'


class Website(models.Model):
    _inherit = 'website'

    pwa_enabled = fields.Boolean(
        'Installable as an App', default=True,
        help="Offer visitors to install this site on their phone's home screen.")
    pwa_name = fields.Char(
        'App Name',
        help="Shown while the app is installing and on the phone's app list. "
             "Defaults to the website's own name.")
    pwa_short_name = fields.Char(
        'Short Name',
        help="Shown under the icon. Phones cut this at about 12 characters, so "
             "shorter than the full name is the point of it.")
    pwa_start_url = fields.Char(
        'Opens On', default=lambda self: self._pwa_default_start_url(),
        help="The page the app opens on. A home page is a brochure; the screen "
             "a doctor came for is an app.")
    pwa_theme_color = fields.Char(
        'Status Bar Colour', default=DEFAULT_THEME,
        help="The colour of the phone's status bar while the app is open.")
    # A dedicated icon, because the images a site already has are the wrong
    # ones: this lab's favicon is a 16x16 ICO (which Odoo's image pipeline
    # cannot decode at all) and its logo is 82x63 - blown up to the 512 a
    # splash screen wants, that is a smear. Measured on the live data,
    # 2026-09-05.
    pwa_icon = fields.Image(
        'App Icon', max_width=1024, max_height=1024,
        help="A square PNG, 512x512 or larger. Falls back to the company logo "
             "and then the favicon, but both are usually far too small to be "
             "an app icon.")
    pwa_icon_warning = fields.Char(compute='_compute_pwa_icon_warning')

    @api.depends('pwa_icon', 'company_id.logo', 'favicon')
    def _compute_pwa_icon_warning(self):
        """Say so on the settings page, rather than letting a blurry icon be
        discovered on somebody's phone."""
        for site in self:
            source, width = site._pwa_icon_source()
            if not source:
                site.pwa_icon_warning = _(
                    "No usable image: upload an app icon, or the site will not "
                    "be installable.")
            elif width and width < 512:
                site.pwa_icon_warning = _(
                    "The best image available is only %spx wide, so the icon "
                    "will look soft. Upload a 512x512 app icon.", width)
            else:
                site.pwa_icon_warning = False

    def _pwa_icon_source(self):
        """(base64 image, its width) — the best source this site can offer.

        In order of how much the lab meant it: an icon chosen for this, then
        the logo, then the favicon. Anything Pillow cannot open is skipped
        rather than allowed to 404 the icon - an ICO favicon is the common
        case and Odoo's own image_process refuses it.
        """
        self.ensure_one()
        for candidate in (self.pwa_icon, self.company_id.logo, self.favicon):
            if not candidate:
                continue
            try:
                image = Image.open(io.BytesIO(base64.b64decode(candidate)))
                return candidate, image.width
            except Exception:                                   # noqa: BLE001
                continue
        return None, 0

    @api.model
    def _pwa_default_start_url(self):
        """The case list if this database has one, otherwise the home page.

        An icon that opens the marketing home page is a bookmark. The doctors
        this app is for came for their cases, so that is where it opens when the
        module can see that screen exists.
        """
        portal = self.env['ir.module.module'].sudo().search(
            [('name', '=', 'lab_portal'), ('state', '=', 'installed')], limit=1)
        return '/my/cases' if portal else '/'

    def _pwa_icon_version(self):
        """A token that changes with the icon — see the controller's note on
        the CDN that served the old bytes for hours after they changed."""
        self.ensure_one()
        source, _width = self._pwa_icon_source()
        return hashlib.sha1(source[:4096]).hexdigest()[:10] if source else '0'

    def _pwa_values(self):
        """Everything the manifest and the layout need, with the blanks filled.

        One place, so the <meta> in the page and the manifest the phone fetches
        can never disagree about the colour or the name.
        """
        self.ensure_one()
        name = self.pwa_name or self.name or 'Odoo'
        return {
            'icon_version': self._pwa_icon_version(),
            'name': name,
            # NOT truncated. Android shows about twelve characters under an
            # icon and ellipsises the rest itself; cutting the string here
            # turned "Arabian Dental Lab" into "Arabian Dent", which is not a short
            # name, it is a typo. Set one deliberately if the full name is long.
            'short_name': self.pwa_short_name or name,
            'start_url': self.pwa_start_url or '/',
            'theme_color': self.pwa_theme_color or DEFAULT_THEME,
        }
