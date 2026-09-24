# -*- coding: utf-8 -*-
"""Cut the whole logo set out of the lab's own artwork.

    instances/arabian_dental/venv/bin/python projects/arabian_dental/tools/make_brand_assets.py

The master is `lab_website/static/src/img/brand/logo-master.png`: the shield and
wordmark from the lab's brand sheet (ADL LOGO CODE.pdf), rendered at 600 dpi with
a transparent background, committed so this can be re-run without the PDF. The
vector original sits beside it as `logo-master.svg`.

Everything else in the suite is cut from that one file, so there is one place to
change if the lab ever restyles: horizontal and stacked lock-ups, a shield on its
own, the wordmark on its own, white versions for dark backgrounds, and the square
app icons.

The white versions are made by hue, not by wholesale inversion: anything with no
colour in it (the grey of "Dental Lab", the shield's outline) goes white, and the
red is left exactly as the brand sheet specifies it. Inverting instead would give
a cyan tooth.

Brand colours, from the sheet: red #ed1c24 (CMYK 0 100 100 0), grey #606a70
(CMYK 10 0 0 70).
"""
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
IMG = os.path.join(HERE, '..', 'lab_website', 'static', 'src', 'img')
MASTER = os.path.join(IMG, 'brand', 'logo-master.png')
PWA_IMG = os.path.join(HERE, '..', 'lab_pwa', 'static', 'src', 'img')
HOME_IMG = os.path.join(HERE, '..', 'lab_home', 'static', 'src', 'img')

RED = (237, 28, 36)
GREY = (96, 106, 112)


def trimmed(image):
    box = image.split()[3].getbbox()
    return image.crop(box) if box else image


def scaled(image, width):
    """To a width, keeping the proportions and adding nothing around it."""
    height = max(1, round(image.height * width / image.width))
    return image.resize((width, height), Image.LANCZOS)


def fit(image, size):
    """Scale to fit a box and centre it there, keeping the aspect ratio."""
    w, h = size
    src = trimmed(image)
    scale = min(w / src.width, h / src.height)
    src = src.resize((max(1, int(src.width * scale)), max(1, int(src.height * scale))),
                     Image.LANCZOS)
    out = Image.new('RGBA', size, (0, 0, 0, 0))
    out.paste(src, ((w - src.width) // 2, (h - src.height) // 2), src)
    return out


def whitened(image):
    """Grey to white, red left alone - the dark-background version."""
    out = image.copy()
    pixels = out.load()
    for y in range(out.height):
        for x in range(out.width):
            r, g, b, a = pixels[x, y]
            if not a:
                continue
            spread = max(r, g, b) - min(r, g, b)
            if spread < 60:                      # no colour in it: grey, black, white
                pixels[x, y] = (255, 255, 255, a)
    return out


def split_master(master):
    """The shield, and the wordmark, told apart by the gap between them."""
    alpha = master.split()[3].load()
    columns = [sum(1 for y in range(0, master.height, 4) if alpha[x, y] > 20)
               for x in range(master.width)]
    run = 0
    for x, ink in enumerate(columns):
        if ink:
            if run > master.height // 8 and x > master.height // 2:
                return trimmed(master.crop((0, 0, x - run, master.height))), \
                    trimmed(master.crop((x, 0, master.width, master.height)))
            run = 0
        else:
            run += 1
    raise SystemExit("the shield and the wordmark are not separated by a gap")


def stacked(mark, word, size):
    """Shield over wordmark, for the places a wide lock-up does not fit."""
    out = Image.new('RGBA', size, (0, 0, 0, 0))
    top = fit(mark, (size[0], int(size[1] * 0.60)))
    bottom = fit(word, (size[0], int(size[1] * 0.28)))
    out.alpha_composite(top, (0, 0))
    out.alpha_composite(bottom, (0, int(size[1] * 0.68)))
    return out


def app_icon(mark, size, background=(255, 255, 255), radius_ratio=0.0):
    """The shield on a plain ground. Square, because every place this is used -
    a phone's home screen, a browser tab, Odoo's app list - crops to a square
    anyway, and a logo cropped by somebody else is a logo nobody chose."""
    out = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    plate = Image.new('RGBA', (size, size), background + (255,))
    if radius_ratio:
        mask = Image.new('L', (size * 4, size * 4), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, size * 4 - 1, size * 4 - 1], int(size * 4 * radius_ratio), fill=255)
        plate.putalpha(mask.resize((size, size), Image.LANCZOS))
    out.alpha_composite(plate)
    out.alpha_composite(fit(mark, (int(size * 0.66), int(size * 0.66))),
                        (int(size * 0.17), int(size * 0.17)))
    return out


def main():
    master = Image.open(MASTER).convert('RGBA')
    mark, word = split_master(master)
    white = whitened(master)
    mark_white, word_white = split_master(white)

    written = []

    def save(image, name, folder=IMG):
        path = os.path.join(folder, name)
        image.save(path, 'PNG', optimize=True)
        written.append((name, image.size, os.path.getsize(path) / 1024))

    save(fit(master, (755, 160)), 'logo-h.png')
    save(fit(white, (755, 160)), 'logo-h-white.png')
    save(stacked(mark, word, (372, 549)), 'logo.png')
    save(stacked(mark_white, word_white, (372, 549)), 'logo-white.png')
    save(fit(mark, (372, 389)), 'logo-mark.png')
    save(fit(mark_white, (372, 389)), 'logo-mark-white.png')
    save(fit(word, (320, 160)), 'logo-word.png')
    save(app_icon(mark, 256), 'icon-256.png')
    save(app_icon(mark, 512), 'icon-512.png')
    save(app_icon(mark, 1024), 'app_icon.png')
    save(app_icon(mark, 1024), 'app_icon.png', PWA_IMG)
    # The home screen draws the wordmark itself, in whichever version the
    # wallpaper under it calls for.
    # TRIMMED, not fitted into a box. `fit` centres the artwork in the box it is
    # given and pads the rest, and the home screen draws this with
    # `background-size: contain` - so the padding came straight off the rendered
    # size. The lock-up was drawing 38 pixels tall inside a 64-pixel box, which
    # put "Since 2000" at five pixels. Trimmed and wide, it fills what it is
    # given, and at 1,500px it is three times the size it is drawn at, so it
    # stays sharp on a high-density screen. (client, 2026-09-24)
    save(scaled(trimmed(master), 1500), 'wordmark.png', HOME_IMG)
    save(scaled(trimmed(white), 1500), 'wordmark-white.png', HOME_IMG)

    for name, size, kb in written:
        print("%-22s %-10s %5.0f kB" % (name, "%dx%d" % size, kb))


if __name__ == '__main__':
    main()
