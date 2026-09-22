# -*- coding: utf-8 -*-
import re

from odoo.tests.common import TransactionCase, tagged

# Functions the client-side Python evaluator will actually run. The list is the
# `allowedFns` whitelist in addons/web/static/src/core/py_js/py_interpreter.js,
# intersected with the date methods PyDate/PyDateTime implement in py_date.js.
#
# The trap this guards is that py.js is *not* Python: a domain like
#   context_today().replace(day=1).strftime('%Y-%m-%d')
# is valid Python and parses fine, but PyDate has no `replace`, so the web client
# throws "Function.prototype.apply was called on undefined" and the whole view
# fails to render. Nothing server-side catches it — the module installs clean.
#
# Use context_today().strftime('%Y-%m-01') for "start of this month": strftime
# passes non-%X characters through literally.
PYJS_CALLABLES = {
    'context_today', 'strftime', 'bool', 'set', 'min', 'max',
    'now', 'today', 'combine', 'relativedelta', 'datetime', 'date', 'time',
    'to_utc', 'to_timezone',
}

EVALUATED_ATTRS = (
    'domain', 'context', 'invisible', 'readonly', 'required', 'column_invisible',
)

# `state in ('a', 'b')` is an operator followed by a tuple, not a call.
PY_KEYWORDS = {'in', 'not', 'and', 'or', 'if', 'else', 'is', 'for', 'None', 'True', 'False'}

CALL_RE = re.compile(r'([A-Za-z_][A-Za-z_0-9]*)\s*\(')


@tagged('post_install', '-at_install')
class TestViewExpressionsEvaluateInTheBrowser(TransactionCase):

    def _module_views(self, module):
        data = self.env['ir.model.data'].search(
            [('module', '=', module), ('model', '=', 'ir.ui.view')])
        return self.env['ir.ui.view'].browse(data.mapped('res_id')).exists()

    def _assert_client_evaluable(self, module):
        offenders = []
        for view in self._module_views(module):
            arch = view.arch_db or ''
            for attr in EVALUATED_ATTRS:
                for value in re.findall(rf'{attr}="([^"]*)"', arch):
                    if '(' not in value:
                        continue
                    for fn in CALL_RE.findall(value):
                        if fn not in PYJS_CALLABLES and fn not in PY_KEYWORDS:
                            offenders.append(f"{view.name}: {attr}=\"{value}\" calls {fn}()")
        self.assertFalse(offenders,
                         "View expressions call functions the web client cannot "
                         "evaluate:\n  " + "\n  ".join(offenders))

    def test_view_expressions_use_only_pyjs_callables(self):
        self._assert_client_evaluable('lab_finance_ops')

    def test_no_date_replace_in_view_expressions(self):
        """The specific construct that broke the Doctor-wise Outstanding search view."""
        for view in self._module_views('lab_finance_ops'):
            self.assertNotIn(
                '.replace(day=', view.arch_db or '',
                f"{view.name} uses date.replace(), which py.js does not implement")
