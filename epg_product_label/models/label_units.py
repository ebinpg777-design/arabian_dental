# -*- coding: utf-8 -*-
"""Unit conversion and small formatting helpers shared by the PDF and ZPL engines.

Everything the designer stores is in the template's own unit (mm or inch).  The two
render engines want different things - wkhtmltopdf wants CSS millimetres, a Zebra
printer wants dots - so both go through here rather than each growing its own
rounding rules.
"""

MM_PER_INCH = 25.4

# Zebra print densities.  The key is what the user picks, the value is dots per mm.
ZPL_DENSITIES = {
    '6': 6.0,     # 152 dpi
    '8': 8.0,     # 203 dpi
    '12': 11.8,   # 300 dpi
    '24': 23.6,   # 600 dpi
}

ZPL_DENSITY_SELECTION = [
    ('6', '6 dpmm (152 dpi)'),
    ('8', '8 dpmm (203 dpi)'),
    ('12', '12 dpmm (300 dpi)'),
    ('24', '24 dpmm (600 dpi)'),
]


def to_mm(value, unit):
    """Convert a designer value expressed in ``unit`` into millimetres."""
    if unit == 'in':
        return float(value or 0.0) * MM_PER_INCH
    return float(value or 0.0)


def from_mm(value, unit):
    """Convert millimetres back into the designer ``unit``."""
    if unit == 'in':
        return float(value or 0.0) / MM_PER_INCH
    return float(value or 0.0)


def to_dots(value_mm, density):
    """Convert millimetres into printer dots for the given ZPL density key."""
    return int(round(float(value_mm or 0.0) * ZPL_DENSITIES.get(density, 8.0)))


def pt_to_mm(points):
    """Typographic points -> millimetres (1 pt = 1/72 in)."""
    return float(points or 0.0) * MM_PER_INCH / 72.0


def mm_to_pt(value_mm):
    return float(value_mm or 0.0) * 72.0 / MM_PER_INCH


def css(value):
    """Render a float for CSS without trailing noise (``12.0`` -> ``12``)."""
    value = round(float(value or 0.0), 3)
    if value == int(value):
        return str(int(value))
    return ('%.3f' % value).rstrip('0').rstrip('.')
