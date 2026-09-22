# -*- coding: utf-8 -*-
"""Point the existing delivery tiles at OUTBOUND work only.

data/filter_tiles.xml is noupdate: the four tiles it shipped (out today, delayed,
emergency, delivered today) live in the database now and an upgrade will not touch
them. With pickups sharing the model, each of those tiles would silently start
counting inbound bags as deliveries to doctors. The rewrite happens here, once,
by xml id - a tile an administrator has since re-pointed keeps their domain if it
no longer matches the shipped text.
"""
import logging

_logger = logging.getLogger(__name__)

SHIPPED = {
    'tile_delivery_out_today': (
        "[('state','=','out'),('out_datetime','>=', context_today().strftime('%Y-%m-%d'))]",
        "[('direction','=','out'),('state','=','out'),"
        "('out_datetime','>=', context_today().strftime('%Y-%m-%d'))]"),
    'tile_delivery_delayed': (
        "[('is_delayed','=',True)]",
        "[('direction','=','out'),('is_delayed','=',True)]"),
    'tile_delivery_emergency': (
        "[('is_emergency','=',True),('state','not in',['delivered','cancel'])]",
        "[('direction','=','out'),('is_emergency','=',True),"
        "('state','not in',['delivered','cancel'])]"),
    'tile_delivery_delivered_today': (
        "[('state','=','delivered'),('delivered_datetime','>=', context_today().strftime('%Y-%m-%d'))]",
        "[('direction','=','out'),('state','=','delivered'),"
        "('delivered_datetime','>=', context_today().strftime('%Y-%m-%d'))]"),
}


def migrate(cr, version):
    if not version:
        return
    # Same disease lab_fieldwork had: every record rule this module ships was
    # switched off on the live database, so executives could read draft quotations
    # and each other's deliveries regardless of the rules being written correctly.
    cr.execute("""
        UPDATE ir_rule r SET active = TRUE
          FROM ir_model_data d
         WHERE d.model = 'ir.rule' AND d.res_id = r.id
           AND d.module = 'lab_delivery'
           AND r.active IS NOT TRUE
     RETURNING d.name
    """)
    revived = [row[0] for row in cr.fetchall()]
    if revived:
        _logger.info("lab_delivery: %s record rules switched back on: %s",
                     len(revived), ', '.join(sorted(revived)))
    for name, (old, new) in SHIPPED.items():
        cr.execute("""
            UPDATE filter_tile t SET domain = %s
              FROM ir_model_data d
             WHERE d.model = 'filter.tile' AND d.res_id = t.id
               AND d.module = 'lab_delivery' AND d.name = %s
               AND t.domain = %s
        """, (new, name, old))
        if cr.rowcount:
            _logger.info("lab_delivery: tile %s scoped to outbound", name)
