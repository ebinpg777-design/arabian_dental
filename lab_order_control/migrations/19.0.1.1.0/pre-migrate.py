# -*- coding: utf-8 -*-
"""Strip the old <listdashboard> KPI blocks out of every stored view before loading.

The KPI ribbons moved from `list_view_dashboard` (an XML tag inside the list arch) to
`ebshel_dynamic_filter` (filter.tile records). The old module is removed from the code base
in the same release, so its view-validation override is gone the moment the new code is
deployed — and any inherited list view whose combined arch still carries the tag fails
RNG validation while the upgrade is loading (a sibling view of this very module hits it).
This runs BEFORE this module's data loads and cleans the tag out of every arch in the
database, whichever module owns the view; each module's own upgrade then re-writes its
views without the tag anyway. Idempotent.
"""
import json
import logging
import re

_logger = logging.getLogger(__name__)

TAG_RE = re.compile(r'\s*<listdashboard\b[^>]*>.*?</listdashboard>\s*', re.S)


def migrate(cr, version):
    if not version:
        return
    cr.execute("SELECT id, arch_db FROM ir_ui_view WHERE arch_db::text LIKE %s", ('%listdashboard%',))
    rows = cr.fetchall()
    cleaned = 0
    for view_id, arch_db in rows:
        if isinstance(arch_db, str):           # untranslated column (older layouts)
            new = TAG_RE.sub('\n', arch_db)
            if new != arch_db:
                cr.execute("UPDATE ir_ui_view SET arch_db=%s WHERE id=%s", (new, view_id))
                cleaned += 1
            continue
        changed = False
        for lang, arch in list(arch_db.items()):  # jsonb {lang: arch}
            new = TAG_RE.sub('\n', arch or '')
            if new != arch:
                arch_db[lang] = new
                changed = True
        if changed:
            cr.execute("UPDATE ir_ui_view SET arch_db=%s WHERE id=%s", (json.dumps(arch_db), view_id))
            cleaned += 1
    _logger.info("listdashboard blocks stripped from %s stored view(s)", cleaned)
