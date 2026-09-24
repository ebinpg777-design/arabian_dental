# -*- coding: utf-8 -*-
"""19.0.2.3.0: tiles are a quarter narrower.

The standard width went from 186 to 140 px. A tile somebody resized by hand
carries its own width, which the stylesheet change does not reach - so it is
scaled by the same factor here, and the ribbon keeps its proportions. Never
below the new 90 px minimum; 0 ("the standard width") is left alone.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE filter_tile
           SET width = GREATEST(90, ROUND(width * 0.75))
         WHERE width > 0
    """)
