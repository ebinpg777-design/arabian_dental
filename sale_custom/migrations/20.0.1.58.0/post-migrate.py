# -*- coding: utf-8 -*-
"""Odoo 17's Material, back on the orders (client, 2026-10-28: "In old server there
is an option of entering Material in the sale order. Please do the same here and
migrate the information to old SOs").

The migration had nowhere to put dental_sale's material_name, so it wrote it into
Special Instructions as one line, "Material: VITA", and a rework copies the
instructions of the order it reworks. Every order whose instructions are still
exactly that one line gets the material as a real field, from a list built from those
names, and the line leaves the instructions. Instructions anybody added to are left as
they are. SQL, so neither the write dates nor the chatter of 15,000 orders change.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

ONLY_MATERIAL = r"^Material: [^\n]+$"


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        SELECT DISTINCT btrim(substr(instruction, 11)) FROM sale_order
         WHERE instruction ~ %s AND material_id IS NULL
    """, (ONLY_MATERIAL,))
    names = sorted(n for (n,) in cr.fetchall() if n)
    if not names:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Material = env['lab.material'].with_context(active_test=False)
    known = set(Material.search([]).mapped('name'))
    Material.create([{'name': n} for n in names if n not in known])
    env.flush_all()
    cr.execute("""
        UPDATE sale_order so
           SET material_id = m.id, instruction = NULL
          FROM lab_material m
         WHERE so.instruction ~ %s AND so.material_id IS NULL
           AND m.name = btrim(substr(so.instruction, 11))
    """, (ONLY_MATERIAL,))
    _logger.info("sale_custom: %s order(s) now carry their material as a field (%s material(s) listed)",
                 cr.rowcount, len(names))
