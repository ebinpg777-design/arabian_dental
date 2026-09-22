"""Draw the Management dashboard app icon.

Same reasoning as the field-work icon: supersample and downsample, because PIL does not
anti-alias polygons and this one is nothing but diagonals.
"""
from PIL import Image, ImageDraw, ImageFilter

S, N, R = 4, 256 * 4, 58 * 4
TOP, BOT = (13, 90, 130), (10, 145, 140)      # deep teal → sea green


def gradient(size, top, bottom):
    g = Image.new("RGB", (size, size))
    px = g.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2.0 * size)
            px[x, y] = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return g


def rounded_mask(size, radius):
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size - 1, size - 1], radius, fill=255)
    return m


canvas = Image.new("RGBA", (N, N), (0, 0, 0, 0))
canvas.paste(gradient(N, TOP, BOT), (0, 0), rounded_mask(N, R))

wash = Image.new("RGBA", (N, N), (0, 0, 0, 0))
ImageDraw.Draw(wash).ellipse([-N * 0.45, -N * 0.70, N * 0.85, N * 0.40],
                             fill=(255, 255, 255, 40))
canvas = Image.alpha_composite(canvas, wash.filter(ImageFilter.GaussianBlur(N * 0.10)))
d = ImageDraw.Draw(canvas)

# Three rising columns: the board IS a comparison, so the icon is a comparison.
BASE = N * 0.735
for x, h, alpha in ((0.255, 0.20, 150), (0.435, 0.33, 200), (0.615, 0.47, 255)):
    left, w = x * N, N * 0.135
    d.rounded_rectangle([left, BASE - h * N, left + w, BASE],
                        radius=w * 0.28, fill=(255, 255, 255, alpha))

# The trend line over them, with a node on the last column — a bar chart alone reads as
# "a report", a bar chart with a trend reads as "how it is going".
pts = [(N * 0.245, N * 0.545), (N * 0.435, N * 0.455), (N * 0.625, N * 0.315),
       (N * 0.795, N * 0.225)]
d.line(pts, fill=(255, 255, 255, 255), width=int(N * 0.030), joint="curve")
hx, hy, hr = pts[-1][0], pts[-1][1], N * 0.048
d.ellipse([hx - hr, hy - hr, hx + hr, hy + hr], fill=(255, 255, 255, 255))
d.ellipse([hx - hr * 0.45, hy - hr * 0.45, hx + hr * 0.45, hy + hr * 0.45],
          fill=(11, 118, 135, 255))

# The baseline the columns stand on.
d.rounded_rectangle([N * 0.235, BASE, N * 0.765, BASE + N * 0.026],
                    radius=N * 0.013, fill=(255, 255, 255, 190))

canvas.filter(ImageFilter.SMOOTH).resize((256, 256), Image.LANCZOS).save("icon.png")
print("icon.png written")
