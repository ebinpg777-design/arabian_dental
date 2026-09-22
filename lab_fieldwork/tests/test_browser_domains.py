# -*- coding: utf-8 -*-
"""Every domain in this module's views must survive the browser's evaluator."""
from lxml import etree

from odoo.tests import TransactionCase, tagged

# A view domain, context or modifier is evaluated in the BROWSER by a
# python-LIKE interpreter (web/static/src/core/py_js). It knows strftime,
# relativedelta and timedelta - and nothing else on a date. Calling one of
# these kills the whole screen at render time with "Function.prototype.apply
# was called on undefined", and nothing server-side catches it: the arch
# validates, the module installs, the list opens, and the filter is a bomb.
# One of these reached production on 2026-09-10. (client, 2026-09-10)
FORBIDDEN = ('.replace(', '.weekday(', '.isocalendar(', '.toordinal(',
             '.astimezone(', '.timetuple(', '.fromisoformat(')
EVALUATED = ('domain', 'context', 'invisible', 'readonly', 'required',
             'column_invisible', 'filter_domain')


@tagged('post_install', '-at_install')
class TestViewsSurviveTheBrowser(TransactionCase):

    def test_no_view_expression_calls_a_method_the_browser_lacks(self):
        module = 'lab_fieldwork'
        views = self.env['ir.ui.view'].search([
            ('id', 'in', self.env['ir.model.data'].search([
                ('module', '=', module), ('model', '=', 'ir.ui.view')]).mapped('res_id'))])
        self.assertTrue(views, "the module should own some views")
        bad = []
        for view in views:
            root = etree.fromstring(view.arch_db.encode())
            for node in root.iter():
                for name, value in node.attrib.items():
                    if name not in EVALUATED and not name.startswith('decoration-'):
                        continue
                    for call in FORBIDDEN:
                        if call in (value or ''):
                            bad.append('%s: %s="%s"' % (view.xml_id, name, value))
        self.assertFalse(bad, "the browser cannot evaluate these:\n" + "\n".join(bad))
