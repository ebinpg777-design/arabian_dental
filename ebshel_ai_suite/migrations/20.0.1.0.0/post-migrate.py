# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
"""Odoo 20 ships no Font Awesome: assistant icons become Material Symbols names."""

FA_TO_ICON = {
    'fa-comments': 'forum',
    'fa-comments-o': 'forum',
    'fa-briefcase': 'work',
    'fa-picture-o': 'image',
    'fa-book': 'book',
    'fa-life-ring': 'support',
}


def migrate(cr, version):
    for old, new in FA_TO_ICON.items():
        cr.execute("UPDATE community_ai_assistant SET glyph = %s WHERE glyph = %s", [new, old])
    # any other Font Awesome class would be shown as plain text by the icon font
    cr.execute("UPDATE community_ai_assistant SET glyph = 'forum' WHERE glyph LIKE %s", ['fa-%'])
