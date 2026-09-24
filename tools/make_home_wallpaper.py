# -*- coding: utf-8 -*-
"""Compose the home screen wallpapers.

    instances/arabian_dental/venv/bin/python projects/arabian_dental/tools/make_home_wallpaper.py

Four of them, all committed under lab_home/static/src/img/. Which one a database
shows is the system parameter `lab_home.wallpaper`; anything else falls back to
the default.

    studio    pearl and warm white, the lab's work as a whisper in the corner
    daylight  white swept diagonally by the brand red, no photograph
    midnight  deep navy lit from two corners, nothing to look at
    ceramic   the lab's layered ceramic work, held in a dark bottom right

Two rules shape every one. The app grid takes the middle and the top of the
screen, so nothing worth looking at may go there. And a wallpaper sits behind
text: each variant therefore declares whether it needs light captions (DARK
below) rather than leaving legibility to chance.

Masks are drawn at 320x180 and scaled up - they are smooth by definition, and a
per-pixel loop over 3.7 million pixels to arrive at a gradient is a minute spent
on nothing. Pillow only: no venv here has numpy.
"""
import os

from PIL import Image, ImageChops, ImageEnhance, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, '..', 'lab_website', 'static', 'src', 'img')
OUT_DIR = os.path.join(HERE, '..', 'lab_home', 'static', 'src', 'img')

W, H = 2560, 1440
MW, MH = 320, 180

RED = (237, 28, 36)        # the brand sheet's red, CMYK 0 100 100 0
RED_DEEP = (155, 15, 21)
GREY = (96, 106, 112)      # the brand sheet's grey, CMYK 10 0 0 70
SLATE = (58, 70, 84)
PEARL_TOP = (253, 252, 251)
PEARL_BOTTOM = (232, 236, 243)

# The variants that need light text over them.
DARK = {'midnight', 'ceramic'}


# --------------------------------------------------------------------- helpers

def mask_from(fn, size=None):
    """An L mask built by calling fn(x, y) with fractions of the area."""
    small = Image.new('L', (MW, MH))
    small.putdata([
        max(0, min(255, int(fn(x / (MW - 1), y / (MH - 1)) * 255)))
        for y in range(MH) for x in range(MW)])
    return small.resize(size or (W, H), Image.BICUBIC)


def ramp(top, bottom, curve=1.0):
    strip = Image.new('RGB', (1, 256))
    for y in range(256):
        f = (y / 255) ** curve
        strip.putpixel((0, y), tuple(
            int(top[i] + (bottom[i] - top[i]) * f) for i in range(3)))
    return strip.resize((W, H), Image.BICUBIC)


def wash(frame, colour, cx, cy, radius, strength, squash=1.0):
    """A soft pool of colour PAINTED over the frame - for light backgrounds,
    where screening a light onto white achieves nothing."""
    def fall(x, y):
        dx, dy = (x - cx) / radius, (y - cy) / (radius * squash * H / W)
        return max(0.0, 1.0 - (dx * dx + dy * dy) ** 0.5) ** 1.8 * strength
    return Image.composite(Image.new('RGB', (W, H), colour), frame, mask_from(fall))


def light(frame, colour, cx, cy, radius, strength):
    """A lamp SCREENED onto the frame - for dark backgrounds, where it lifts
    what is under it instead of covering it."""
    def fall(x, y):
        dx, dy = (x - cx) / radius, (y - cy) / (radius * H / W)
        return max(0.0, 1.0 - (dx * dx + dy * dy) ** 0.5) ** 2.0 * strength
    lamp = Image.composite(Image.new('RGB', (W, H), colour),
                           Image.new('RGB', (W, H)), mask_from(fall))
    return ImageChops.screen(frame, lamp)


def grain(frame, amount=5):
    """A breath of noise. Long smooth gradients band under JPEG, and the banding
    shows as stripes running the width of the screen behind the icons."""
    noise = Image.effect_noise((W, H), 12).convert('RGB')
    return Image.blend(frame, ImageChops.overlay(frame, noise), amount / 100.0)


def watermark(frame, cx, cy, height, strength):
    """The shield, laid into the picture at a whisper."""
    mark = Image.open(os.path.join(SRC, 'logo-mark.png')).convert('RGBA')
    scale = height / mark.height
    mark = mark.resize((int(mark.width * scale), height), Image.LANCZOS)
    alpha = mark.split()[3].point(lambda v: int(v * strength))
    frame = frame.copy()
    frame.paste(mark, (int(W * cx) - mark.width // 2, int(H * cy) - mark.height // 2), alpha)
    return frame


def crowns(scale=1.0, colour=0.8, brightness=1.0, blur=0.6):
    """The lab's own layered ceramic, feathered to nothing at its own edges so
    that it has none."""
    photo = Image.open(os.path.join(SRC, 'layered-ceramic.jpg')).convert('RGB')
    pw, ph = int(photo.width * scale), int(photo.height * scale)
    photo = photo.resize((pw, ph), Image.LANCZOS)
    photo = ImageEnhance.Color(photo).enhance(colour)
    photo = ImageEnhance.Brightness(photo).enhance(brightness)
    photo = photo.filter(ImageFilter.GaussianBlur(blur))
    feather = mask_from(
        lambda x, y: max(0.0, 1.0 - ((x - 0.5) ** 2 + (y - 0.5) ** 2) ** 0.5 / 0.5) ** 1.5,
        size=(pw, ph))
    return photo, feather


# -------------------------------------------------------------------- variants

def studio():
    """Pearl: warm white falling to a cool grey, a soft red bloom off the top
    right corner, and the shield watermarked into the bottom right.

    The shield and not a photograph: a photograph faded far enough to sit behind
    dark text stops being a photograph and becomes a smudge, whereas the mark is
    a flat shape and survives being whispered."""
    frame = ramp(PEARL_TOP, PEARL_BOTTOM, curve=1.15)
    frame = wash(frame, (255, 246, 243), 0.9, 0.02, 0.6, 0.85)
    frame = wash(frame, RED, 0.99, -0.06, 0.4, 0.14)
    frame = wash(frame, (226, 230, 234), 0.04, 1.05, 0.55, 0.8)
    frame = watermark(frame, 0.82, 0.72, int(H * 0.72), 0.085)
    return grain(frame, 4)


def daylight():
    """White, swept diagonally by the brand red out of the bottom right corner,
    with one thin band of light to give the sweep a direction."""
    frame = ramp((255, 255, 255), (240, 243, 248), curve=1.0)
    frame = wash(frame, (252, 226, 226), 1.0, 0.92, 0.8, 0.95, squash=1.4)
    frame = wash(frame, RED, 1.08, 1.06, 0.5, 0.45)
    frame = wash(frame, RED_DEEP, 1.14, 1.14, 0.32, 0.4)
    frame = wash(frame, (233, 236, 239), -0.08, -0.06, 0.52, 0.85)
    band = mask_from(
        lambda x, y: max(0.0, 1.0 - abs((y - 0.78) - (x - 0.5) * 0.5) / 0.05) ** 2 * 0.55)
    frame = Image.composite(Image.new('RGB', (W, H), (255, 252, 252)), frame, band)
    return grain(frame, 4)


def midnight():
    """Deep navy, lit from two corners. Nothing in it to look at, which is the
    point: it is the quietest of the four to read icons against."""
    frame = ramp((10, 13, 17), (38, 45, 52), curve=0.85)
    frame = light(frame, RED, 0.96, -0.04, 0.58, 0.5)
    frame = light(frame, SLATE, -0.02, 1.04, 0.62, 0.55)
    frame = light(frame, (150, 165, 180), 0.45, 0.3, 0.5, 0.08)
    return grain(frame, 5)


def ceramic():
    """The lab's layered ceramic work, held in the bottom right of a navy frame
    and lifted enough to be seen for what it is."""
    frame = ramp((10, 13, 17), (34, 41, 48), curve=0.85)
    photo, feather = crowns(scale=1.25, colour=0.85, brightness=1.2, blur=0.5)
    frame.paste(photo, (int(W * 0.78) - photo.width // 2,
                        int(H * 0.68) - photo.height // 2), feather)
    frame = light(frame, RED, 0.97, -0.05, 0.5, 0.42)
    frame = light(frame, SLATE, -0.02, 1.05, 0.5, 0.5)
    # Keep the band the icons sit in quiet, whatever the photograph is doing.
    band = mask_from(lambda x, y: max(0.0, 1.0 - abs(y - 0.3) / 0.4) ** 1.5 * 0.45)
    frame = Image.composite(Image.new('RGB', (W, H), (11, 14, 19)), frame, band)
    return grain(frame, 5)


VARIANTS = {'studio': studio, 'daylight': daylight,
            'midnight': midnight, 'ceramic': ceramic}


def main():
    for name, build in sorted(VARIANTS.items()):
        path = os.path.join(OUT_DIR, 'wallpaper-%s.jpg' % name)
        build().save(path, 'JPEG', quality=88, optimize=True, progressive=True)
        print("%-9s %-24s %4.0f kB  %s captions" % (
            name, os.path.basename(path), os.path.getsize(path) / 1024,
            'light' if name in DARK else 'dark'))


if __name__ == '__main__':
    main()
