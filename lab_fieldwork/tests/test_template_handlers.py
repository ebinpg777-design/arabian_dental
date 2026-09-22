# -*- coding: utf-8 -*-
"""Every t-on- handler must name a method that exists.

A button whose handler points at nothing is not a syntax error and not an
install error: the XML is well-formed, the module loads, the screen renders, and
the failure arrives only when a person taps it -

    TypeError: v15.openVisits is not a function

That is what My Day's "Visits" button did on the floor the minute it shipped: the
button was added to the template and the method was never written.
(client, 2026-09-12)

This is the same shape of hole as the Owl operator guard in lab_delivery -
client-side templates are checked by nobody until somebody uses them.
"""
import os
import re

from odoo.tests import TransactionCase, tagged

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
XML_DIR = os.path.join(HERE, 'static', 'src', 'xml')
JS_DIR = os.path.join(HERE, 'static', 'src', 'js')

# t-on-click="() => this.foo(...)" and t-on-click="this.foo"
HANDLER = re.compile(r't-on-[\w.-]+\s*=\s*"([^"]*)"')
THIS_CALL = re.compile(r'\bthis\.([A-Za-z_]\w*)\s*\(')
THIS_REF = re.compile(r'^\s*this\.([A-Za-z_]\w*)\s*$')

# Defined as a class method `foo(` / `async foo(` or a property `foo = `.
def _defined_names(source):
    """Method and property names, one line at a time.

    The parameter list must not cross a newline. `[^)]*` does, and it made the
    match that starts at `patch(NavBar.prototype, {` run on until the `)` of the
    NEXT method's own signature - swallowing `fwGoBack()` and reporting a method
    that is plainly defined as missing. A guard that cries wolf gets switched
    off, which is worse than no guard. (client, 2026-09-12)
    """
    names = set()
    names |= set(re.findall(r'^[ \t]*(?:async\s+)?([A-Za-z_]\w*)\s*\([^()\n]*\)\s*\{',
                            source, re.M))
    names |= set(re.findall(r'^[ \t]*([A-Za-z_]\w*)\s*[:=]\s*', source, re.M))
    return names

# Things a component inherits or that are not component methods at all.
ALLOWED = {'state', 'props', 'env', 'render', 'el'}


@tagged('post_install', '-at_install')
class TestTemplateHandlers(TransactionCase):

    def test_every_handler_has_a_method(self):
        if not os.path.isdir(XML_DIR):
            self.skipTest('no client templates')
        defined = set()
        for root, _dirs, files in os.walk(JS_DIR):
            for name in files:
                if name.endswith('.js'):
                    with open(os.path.join(root, name), encoding='utf-8') as fh:
                        defined |= _defined_names(fh.read())

        missing = []
        for root, _dirs, files in os.walk(XML_DIR):
            for name in sorted(files):
                if not name.endswith('.xml'):
                    continue
                path = os.path.join(root, name)
                with open(path, encoding='utf-8') as fh:
                    for lineno, line in enumerate(fh, 1):
                        for expr in HANDLER.findall(line):
                            called = set(THIS_CALL.findall(expr))
                            ref = THIS_REF.match(expr)
                            if ref:
                                called.add(ref.group(1))
                            for method in called:
                                if method in ALLOWED or method in defined:
                                    continue
                                missing.append('%s:%s  this.%s(...)' % (name, lineno, method))

        self.assertFalse(missing, "handlers naming a method that does not exist:\n  "
                                  + "\n  ".join(missing))

    def test_refs_are_declared_and_read_on_both_sides(self):
        """A ref only works if BOTH halves exist, and neither half complains.

        `useRef` on a name no template declares returns a ref whose `el` is
        forever null, so the code guarding it with `?.` does nothing at all and
        says nothing about it. That is how the clinics panel's scroll-into-view
        dies if the t-ref is dropped: the button still opens the panel, the
        screen simply stops following it, and the tile looks dead again - which
        is the bug the scroll was added to fix. The other direction is quieter
        still but worth the same line of code: a t-ref nobody reads is markup
        pretending to be wiring. (client, 2026-09-12)

        Checked in both directions because the first version of this test only
        looked for orphaned t-refs, which is precisely NOT the half that breaks
        the scroll - removing the t-ref made it pass.
        """
        if not os.path.isdir(XML_DIR):
            self.skipTest('no client templates')
        declared = {}
        for root, _dirs, files in os.walk(XML_DIR):
            for name in sorted(files):
                if not name.endswith('.xml'):
                    continue
                with open(os.path.join(root, name), encoding='utf-8') as fh:
                    for lineno, line in enumerate(fh, 1):
                        for ref in re.findall(r't-ref\s*=\s*"([^"{}]+)"', line):
                            declared.setdefault(ref, '%s:%s' % (name, lineno))
        used = {}
        for root, _dirs, files in os.walk(JS_DIR):
            for name in sorted(files):
                if not name.endswith('.js'):
                    continue
                with open(os.path.join(root, name), encoding='utf-8') as fh:
                    for lineno, line in enumerate(fh, 1):
                        for ref in re.findall(r'useRef\(\s*["\']([^"\']+)["\']', line):
                            used.setdefault(ref, '%s:%s' % (name, lineno))
        broken = ['%s  useRef("%s") - no t-ref declares it' % (where, ref)
                  for ref, where in sorted(used.items()) if ref not in declared]
        broken += ['%s  t-ref="%s" - no useRef reads it' % (where, ref)
                   for ref, where in sorted(declared.items()) if ref not in used]
        self.assertFalse(broken, "refs wired on one side only:\n  " + "\n  ".join(broken))
