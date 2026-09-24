# -*- coding: utf-8 -*-
"""Compose the home screen wallpaper from the lab's own photography.

    instances/arabian_dental/venv/bin/python projects/arabian_dental/tools/make_home_wallpaper.py

Baked into one image rather than layered in CSS, because the layering that makes
it work - the lab's own crown work held in the bottom right, fading up and to
the left, lit from two corners - depends on where things sit in the FRAME, and a
CSS layer knows only where things sit on the screen.

Where the picture may go is decided by what is already on that screen: the app
grid takes the middle and the top, so the photograph gets the bottom right,
which is otherwise empty, and everything left of centre stays flat navy so the
app names have something quiet to sit on.

Masks are drawn small and scaled up. They are smooth by definition, and a
per-pixel loop over 3.7 million pixels to arrive at a gradient is a minute spent
on nothing. Pillow only, because nothing here has numpy.

Re-run it after changing the source photograph; the result is committed.
"""
import os

from PIL import Image, ImageChops, ImageEnhance, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, '..', 'lab_website', 'static', 'src', 'img')
OUT = os.path.join(HERE, '..', 'lab_home', 'static', 'src', 'img', 'wallpaper.jpg')

W, H = 2560, 1440
MW, MH = 320, 180          # the size every mask is drawn at
NAVY_TOP = (8, 14, 26)
NAVY_BOTTOM = (19, 30, 53)
RED = (227, 30, 37)
BLUE = (46, 84, 150)


def mask_from(fn, size=None):
    """An L mask built by calling fn(x, y) with fractions of the area."""
    small = Image.new('L', (MW, MH))
    small.putdata([
        max(0, min(255, int(fn(x / (MW - 1), y / (MH - 1)) * 255)))
        for y in range(MH) for x in range(MW)])
    return small.resize(size or (W, H), Image.BICUBIC)


def base_gradient():
    strip = Image.new('RGB', (1, 256))
    for y in range(256):
        f = (y / 255) ** 0.85
        strip.putpixel((0, y), tuple(
            int(NAVY_TOP[i] + (NAVY_BOTTOM[i] - NAVY_TOP[i]) * f) for i in range(3)))
    return strip.resize((W, H), Image.BICUBIC)


def photo_inset(frame):
    """The lab's own crown work, set into the bottom right of the frame.

    Set in near its own size rather than blown up to fill the frame: the app
    grid owns the middle, so the picture only ever needed a corner, and
    stretching a 1,072-pixel photograph across 2,560 turned fine ceramic
    layering into mush. The photograph's own black background is what lets it
    melt into the navy without an edge.
    """
    photo = Image.open(os.path.join(SRC, 'layered-ceramic.jpg')).convert('RGB')
    scale = 1.2
    pw, ph = int(photo.width * scale), int(photo.height * scale)
    photo = photo.resize((pw, ph), Image.LANCZOS)
    photo = ImageEnhance.Color(photo).enhance(0.72)
    photo = ImageEnhance.Brightness(photo).enhance(0.9)
    photo = photo.filter(ImageFilter.GaussianBlur(0.6))

    # Feathered away at its own edges, so that it has none.
    feather = mask_from(
        lambda x, y: max(0.0, 1.0 - ((x - 0.5) ** 2 + (y - 0.5) ** 2) ** 0.5 / 0.5) ** 1.5 * 0.9,
        size=(pw, ph))
    frame.paste(photo, (int(W * 0.79) - pw // 2, int(H * 0.72) - ph // 2), feather)
    return frame


def glow(cx, cy, radius, colour, strength):
    """A light to screen onto the frame, as a flat colour behind a falloff."""
    def fall(x, y):
        dx, dy = (x - cx) / radius, (y - cy) / (radius * H / W)
        return max(0.0, 1.0 - (dx * dx + dy * dy) ** 0.5) ** 2.0 * strength
    layer = Image.new('RGB', (W, H), colour)
    return Image.composite(layer, Image.new('RGB', (W, H)), fall_mask(fall))


def fall_mask(fn):
    return mask_from(fn)


def main():
    frame = photo_inset(base_gradient())

    # Two lights, screened on: brand red in the top right corner, a cool blue
    # low on the left. Screened and not painted, so they lift what is under
    # them instead of covering it.
    for spot in (glow(0.95, -0.02, 0.55, RED, 0.55),
                 glow(0.02, 1.02, 0.52, BLUE, 0.55)):
        frame = ImageChops.screen(frame, spot)

    # A last wash across the band the app icons sit in. Whatever the photograph
    # is doing up there, the app names have to stay readable.
    band = mask_from(lambda x, y: max(0.0, 1.0 - abs(y - 0.34) / 0.42) ** 1.4 * 0.5)
    frame = Image.composite(
        Image.blend(frame, Image.new('RGB', (W, H), (6, 11, 20)), 1.0), frame, band)

    frame.save(OUT, 'JPEG', quality=86, optimize=True, progressive=True)
    print("%s  %dx%d  %.0f kB" % (OUT, W, H, os.path.getsize(OUT) / 1024))


if __name__ == '__main__':
    main()
