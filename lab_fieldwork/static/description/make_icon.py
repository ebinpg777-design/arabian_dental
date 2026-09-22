"""Draw the Field Work app icon.

Kept in the repo because an icon that cannot be regenerated is an icon nobody dares
change. Supersampled 4x and downsampled at the end — PIL has no anti-aliasing on
polygons, and the pin's point is exactly where jaggies show.
"""
from PIL import Image, ImageDraw, ImageFilter

S = 4                      # supersample factor
N = 256 * S                # working canvas
R = 58 * S                 # corner radius, matching Odoo's other app icons

TOP = (109, 40, 217)       # violet
BOT = (162, 28, 175)       # magenta — the lab's own purple, but alive


def gradient(size, top, bottom):
    """Diagonal, not vertical: a vertical gradient reads as a button, a diagonal one
    reads as a surface with light falling across it."""
    grad = Image.new("RGB", (size, size))
    px = grad.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2.0 * size)
            px[x, y] = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return grad


def rounded_mask(size, radius):
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius, fill=255)
    return mask


def tooth(draw, cx, cy, w, h, fill):
    """A molar: rounded crown, two roots. Drawn as one polygon plus a crown ellipse so
    the shoulders stay round at any size."""
    half = w / 2.0
    top = cy - h / 2.0
    # Crown: a wide rounded cap. Drawn generously because everything below it is thin,
    # and a thin crown is what made this read as a keyhole.
    draw.rounded_rectangle([cx - half, top, cx + half, top + h * 0.56],
                           radius=half * 0.62, fill=fill)
    # Two roots with a wide gap. The gap is the whole silhouette — close it and the
    # shape stops being a tooth at any size below about 64px.
    draw.polygon([(cx - half * 0.94, top + h * 0.44),
                  (cx - half * 0.16, top + h * 0.44),
                  (cx - half * 0.30, top + h * 1.02),
                  (cx - half * 0.72, top + h * 1.00)], fill=fill)
    draw.polygon([(cx + half * 0.16, top + h * 0.44),
                  (cx + half * 0.94, top + h * 0.44),
                  (cx + half * 0.72, top + h * 1.00),
                  (cx + half * 0.30, top + h * 1.02)], fill=fill)


canvas = Image.new("RGBA", (N, N), (0, 0, 0, 0))
canvas.paste(gradient(N, TOP, BOT), (0, 0), rounded_mask(N, R))

# A light wash from the top-left, so the tile has a direction.
wash = Image.new("RGBA", (N, N), (0, 0, 0, 0))
ImageDraw.Draw(wash).ellipse([-N * 0.45, -N * 0.70, N * 0.85, N * 0.40],
                             fill=(255, 255, 255, 42))
# Blurred, or the ellipse reads as a stray shape rather than as light.
wash = wash.filter(ImageFilter.GaussianBlur(N * 0.10))
canvas = Image.alpha_composite(canvas, wash)

d = ImageDraw.Draw(canvas)

# The route: this is FIELD work, so the pin stands on a path rather than in space.
for i, (x, y, r) in enumerate([(0.30, 0.845, 0.020), (0.42, 0.876, 0.016),
                               (0.56, 0.884, 0.016), (0.70, 0.858, 0.020)]):
    cx, cy, rr = x * N, y * N, r * N
    d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=(255, 255, 255, 120))

# The pin. Head + a tapering body that meets at a real point.
HX, HY, HR = N * 0.5, N * 0.415, N * 0.225
d.ellipse([HX - HR, HY - HR, HX + HR, HY + HR], fill=(255, 255, 255, 255))
d.polygon([(HX - HR * 0.86, HY + HR * 0.50),
           (HX + HR * 0.86, HY + HR * 0.50),
           (HX, N * 0.775)], fill=(255, 255, 255, 255))

# The tooth is knocked OUT of the pin in the tile's own colour, so the icon reads at
# 24px as a pin and at full size as a pin with a tooth in it.
tooth(d, HX, HY - HR * 0.02, HR * 1.02, HR * 1.16, (124, 32, 190, 255))

canvas = canvas.filter(ImageFilter.SMOOTH)
canvas.resize((256, 256), Image.LANCZOS).save("icon.png")
canvas.resize((128, 128), Image.LANCZOS).save("icon_128.png")
print("icon.png written")
