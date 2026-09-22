# -*- coding: utf-8 -*-
"""Switch the field-work record rules back on.

Every record rule this module ships was inactive on the live database - all 14 of
them - which is why an executive could read every visit, every trip and every
clinic regardless of the rules being written correctly. Module upgrades do not
restore `active`: once a rule is switched off by hand it stays off, because the XML
does not carry the field.

The module's own tests were failing on exactly this ("an executive sees only their
own visits"), so the rules being off was already costing correctness, not just
privacy. Only rules OWNED BY THIS MODULE are touched - the rest of the database's
disabled rules are somebody else's decision to review. (client, 2026-08-24)
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE ir_rule r SET active = TRUE
          FROM ir_model_data d
         WHERE d.model = 'ir.rule' AND d.res_id = r.id
           AND d.module = 'lab_fieldwork'
           AND r.active IS NOT TRUE
     RETURNING d.name
    """)
    revived = [row[0] for row in cr.fetchall()]
    if revived:
        _logger.info("lab_fieldwork: %s record rules switched back on: %s",
                     len(revived), ', '.join(sorted(revived)))
    else:
        _logger.info("lab_fieldwork: all record rules were already active")
