# -*- coding: utf-8 -*-
"""The lab's labels are 60 x 40 mm (client, 2026-10-21): the new Roll 60 x 40 format
becomes the default, once - a default chosen later is left alone."""


def migrate(cr, version):
    cr.execute("""
        SELECT res_id FROM ir_model_data
         WHERE module = 'epg_sticker_print' AND name = 'format_roll_60x40'
           AND model = 'epg.sticker.format'
    """)
    row = cr.fetchone()
    if not row:
        return
    cr.execute("UPDATE epg_sticker_format SET is_default = (id = %s)", (row[0],))
