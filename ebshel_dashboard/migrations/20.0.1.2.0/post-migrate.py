# -*- coding: utf-8 -*-
"""1.1 pinned a board to one company; 1.2 lets it name several.

The old column is read once here and copied into the new relation, so a
board that was pinned stays pinned. The column itself is left in place: an
upgrade that drops data it has just copied has no way back.
"""


def migrate(cr, version):
    cr.execute("""
        INSERT INTO dashboard_board_company_rel (board_id, company_id)
        SELECT id, company_id FROM dashboard_board
        WHERE company_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM dashboard_board_company_rel rel
              WHERE rel.board_id = dashboard_board.id
                AND rel.company_id = dashboard_board.company_id)
    """)
