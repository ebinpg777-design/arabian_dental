# -*- coding: utf-8 -*-
"""Sheet lines computed before the incentive basis existed say what they were counted on.

`incentive_basis` on a sheet line is written when the sheet is computed, so every line
from before 20.0.1.6.0 has none: the "Counted On" column was blank and the working's
note ("the base is not the amount billed because...") left out the lines an incentive
price had changed. Their own flag already says which they were. Only empty ones are
touched, so this is safe to run on a database that went through 20.0.1.6.0 already.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        UPDATE lab_incentive_sheet_line
           SET incentive_basis = CASE WHEN used_incentive_price THEN 'price' ELSE 'sale' END
         WHERE incentive_basis IS NULL
    """)
    _logger.info("lab_incentive: %d existing sheet line(s) labelled with their basis", cr.rowcount)
