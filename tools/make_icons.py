# -*- coding: utf-8 -*-
"""Draw the app icons for the lab's own modules, in the lab's own colours.

    instances/arabian_dental/venv/bin/python projects/arabian_dental/tools/make_icons.py

The suite arrived from the ortho fork with icons in violet, teal, green and four
different blues - a home screen that looked like four products. These are one
family: the brand red (#ed1c24) for what the lab sells and what its customers
touch, the brand grey (#606a70) for what the lab runs on, a white pictogram, and
the shield in the corner so an app is recognisable as this lab's at icon size.

Only the lab's OWN modules are drawn. `epg_*`, `eh_*`, `ebshel_*` and the
third-party add-ons keep their own icons: they are sold to other customers and
carrying ADL's brand would be a lie about who made them.

Pictograms come from the Font Awesome that Odoo ships, so they are the same
symbols the menus use. A codepoint that the shipped font does not have would
render as nothing at all, so each one is checked after drawing and falls back to
the module's initials rather than shipping an empty tile.

Supersampled 4x and reduced at the end: PIL does not anti-alias, and the corner
radius is exactly where that shows.
"""
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.join(HERE, '..')
MARK_WHITE = os.path.join(PROJECT, 'lab_website', 'static', 'src', 'img', 'logo-mark-white.png')
FONT = os.path.join(HERE, '..', '..', '..', 'odoo19', 'addons', 'web', 'static',
                    'src', 'libs', 'fontawesome', 'fonts', 'fontawesome-webfont.ttf')
LABEL_FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'

S = 4                       # supersample
N = 256 * S
RADIUS = 58 * S             # matches Odoo's own app icons

RED = ((237, 28, 36), (168, 18, 25))
GREY = ((108, 119, 126), (56, 64, 72))

# module: (font awesome codepoint, family)
ICONS = {
    # what the lab sells, and what its customers and reps touch
    'sale_custom':           ('f07a', RED),    # shopping cart
    'lab_track':             ('f05b', RED),    # crosshairs
    'lab_portal':            ('f0c0', RED),    # users
    'lab_website':           ('f0ac', RED),    # globe
    'lab_whatsapp':          ('f232', RED),    # whatsapp
    'lab_collections':       ('f156', RED),    # rupee
    'lab_fieldwork':         ('f21c', RED),    # motorcycle
    'lab_delivery':          ('f0d1', RED),    # truck
    'lab_incentive':         ('f091', RED),    # trophy
    # what the lab runs on
    'lab_order_control':     ('f14a', GREY),   # check-square
    'lab_rework':            ('f021', GREY),   # refresh
    'lab_workcenter_scan':   ('f02a', GREY),   # barcode
    'material_request':      ('f0ca', GREY),   # list
    'lab_reports':           ('f15c', GREY),   # file-text
    'lab_dashboards':        ('f080', GREY),   # bar chart
    'lab_ceo_dashboard':     ('f0e4', GREY),   # tachometer
    'lab_finance_ops':       ('f1ec', GREY),   # calculator
    'lab_bank_reconciliation': ('f19c', GREY),  # bank
    'petty_cash':            ('f0d6', GREY),   # bank note
    'lab_access_control':    ('f023', GREY),   # lock
    'lab_migration':         ('f0ec', GREY),   # exchange
    'lab_pwa':               ('f10b', GREY),   # mobile
}


def plate(top, bottom):
    """A diagonal gradient, not a vertical one: vertical reads as a button,
    diagonal reads as a surface with light falling across it."""
    small = Image.new('RGB', (64, 64))
    px = small.load()
    for y in range(64):
        for x in range(64):
            t = (x + y) / 126.0
            px[x, y] = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    tile = small.resize((N, N), Image.BICUBIC).convert('RGBA')
    mask = Image.new('L', (N, N), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, N - 1, N - 1], RADIUS, fill=255)
    tile.putalpha(mask)
    return tile


def glyph_layer(codepoint, size):
    """The pictogram, white, centred on its own ink rather than on its font box -
    Font Awesome glyphs sit at wildly different heights in the em."""
    layer = Image.new('RGBA', (N, N), (0, 0, 0, 0))
    font = ImageFont.truetype(FONT, size)
    draw = ImageDraw.Draw(layer)
    draw.text((N // 2, N // 2), chr(int(codepoint, 16)), font=font,
              fill=(255, 255, 255, 255), anchor='mm')
    box = layer.split()[3].getbbox()
    if not box:
        return None
    ink = layer.crop(box)
    out = Image.new('RGBA', (N, N), (0, 0, 0, 0))
    out.alpha_composite(ink, ((N - ink.width) // 2, (N - ink.height) // 2))
    return out


def initials_layer(module):
    """The fallback: two letters, so a missing pictogram is still a tile somebody
    can tell apart."""
    letters = ''.join(part[0] for part in module.split('_')[:2]).upper()
    layer = Image.new('RGBA', (N, N), (0, 0, 0, 0))
    font = ImageFont.truetype(LABEL_FONT, int(N * 0.42))
    ImageDraw.Draw(layer).text((N // 2, N // 2), letters, font=font,
                               fill=(255, 255, 255, 255), anchor='mm')
    return layer


def badge(mark):
    """The shield, bottom right, at the size a 64-pixel tile can still show."""
    side = int(N * 0.26)
    shield = mark.copy()
    shield.thumbnail((side, side), Image.LANCZOS)
    layer = Image.new('RGBA', (N, N), (0, 0, 0, 0))
    layer.alpha_composite(shield, (N - shield.width - int(N * 0.06),
                                   N - shield.height - int(N * 0.06)))
    return layer


def build(module, codepoint, family, mark):
    icon = plate(*family)
    art = glyph_layer(codepoint, int(N * 0.44))
    used = 'glyph'
    if art is None:
        art, used = initials_layer(module), 'initials'
    # Lifted off centre: the shield takes the bottom right corner.
    icon.alpha_composite(art, (-int(N * 0.05), -int(N * 0.05)))
    icon.alpha_composite(badge(mark))
    return icon.resize((256, 256), Image.LANCZOS), used


def main():
    mark = Image.open(MARK_WHITE).convert('RGBA')
    for module in sorted(ICONS):
        codepoint, family = ICONS[module]
        folder = os.path.join(PROJECT, module, 'static', 'description')
        if not os.path.isdir(folder):
            print("%-26s no static/description - skipped" % module)
            continue
        icon, used = build(module, codepoint, family, mark)
        icon.save(os.path.join(folder, 'icon.png'), 'PNG', optimize=True)
        print("%-26s %-8s %s" % (module, used,
                                 'red' if family is RED else 'grey'))


if __name__ == '__main__':
    main()
