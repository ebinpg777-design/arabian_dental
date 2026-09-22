# -*- coding: utf-8 -*-
"""Client-side templates must use JS operators, not Python ones.

Owl compiles a t-attribute expression to JavaScript through a small word map
(`WORD_REPLACEMENT` in owl.js): `and`, `or`, `gt`, `gte`, `lt`, `lte`. **`not` is
not in it.** An unmapped word is treated as a symbol and compiled to a context
lookup, so `not d.tracking_url` becomes `ctx['not']ctx['d'].tracking_url` — a
syntax error that fails the WHOLE template, not just that node. My Day went blank
for every executive on one `not`. Server-side QWeb (reports, portal) is Python and
is unaffected, which is what makes this easy to write by accident.

Nothing in the build catches it: the XML is well-formed, the module installs, and
it only breaks in the browser. (client, 2026-08-29)
"""
import os
import re

from odoo.tests import TransactionCase, tagged

# Python-only spellings with no Owl equivalent.
FORBIDDEN = [
    (re.compile(r'(?:^|[\s(!])not\s'), "not", "use ! instead"),
    (re.compile(r'\sis\s+not\s'), "is not", "use !== instead"),
    (re.compile(r'\sis\s+None'), "is None", "use === undefined instead"),
    (re.compile(r'(?:^|[\s(])None(?:$|[\s)])'), "None", "use undefined/null instead"),
    (re.compile(r'(?:^|[\s(])(?:True|False)(?:$|[\s)])'), "True/False", "use true/false"),
]
# t-attributes whose value Owl compiles as a JS expression.
EXPR_ATTR = re.compile(
    r'\bt-(?:if|elif|esc|out|foreach|att|attf-[\w-]+|att-[\w-]+|value|key|props)\s*=\s*"([^"]*)"')


@tagged('post_install', '-at_install')
class TestOwlTemplateSyntax(TransactionCase):

    def test_no_python_operators_in_owl_templates(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        root = os.path.join(here, 'static', 'src', 'xml')
        if not os.path.isdir(root):
            self.skipTest('no client-side templates in this module')

        problems = []
        for folder, _dirs, files in os.walk(root):
            for fname in files:
                if not fname.endswith('.xml'):
                    continue
                path = os.path.join(folder, fname)
                with open(path, encoding='utf-8') as fh:
                    for lineno, line in enumerate(fh, start=1):
                        for expr in EXPR_ATTR.findall(line):
                            for pattern, word, advice in FORBIDDEN:
                                if pattern.search(expr):
                                    problems.append(
                                        '%s:%d  %r uses Python `%s` — %s'
                                        % (os.path.relpath(path, here), lineno, expr,
                                           word, advice))
        self.assertFalse(problems, 'Python operators in Owl templates:\n  ' +
                         '\n  '.join(problems))
