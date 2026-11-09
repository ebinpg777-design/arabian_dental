# -*- coding: utf-8 -*-
"""20.0.9.1.0: a payment may be split - part cash, part cheque, part UPI.

`lab.visit` gained collected_cash / collected_cheque / collected_online beside the
total (`collected`) and the mode (`pay_mode`). Every reader that asks "how much
cash" now reads the cash part, so the visits already on file are given theirs:
the whole amount under the one mode they were recorded with. A visit with an
amount and no mode stays as it was - nobody ever said how that money came.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        UPDATE lab_visit
           SET collected_cash = CASE WHEN pay_mode = 'cash' THEN collected ELSE 0 END,
               collected_cheque = CASE WHEN pay_mode = 'cheque' THEN collected ELSE 0 END,
               collected_online = CASE WHEN pay_mode = 'online' THEN collected ELSE 0 END
         WHERE COALESCE(collected, 0) > 0
           AND pay_mode IN ('cash', 'cheque', 'online')
           AND COALESCE(collected_cash, 0) + COALESCE(collected_cheque, 0)
               + COALESCE(collected_online, 0) = 0
    """)
    _logger.info("lab_fieldwork 9.1.0: %s visit payment(s) given their part by mode", cr.rowcount)
