# -*- coding: utf-8 -*-
from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestSearchBarArrow(HttpCase):
    """The search bar shows the arrow of its Filters menu on a desktop too (Odoo 20
    draws it only on a touch screen)."""

    def test_the_search_bar_shows_its_arrow(self):
        code = """
            const wait = (sel) => new Promise((resolve, reject) => {
                let n = 0;
                const t = setInterval(() => {
                    const el = document.querySelector(sel);
                    if (el) { clearInterval(t); resolve(el); }
                    else if (++n > 150) { clearInterval(t); reject(new Error('not drawn: ' + sel)); }
                }, 100);
            });
            (async () => {
                const button = await wait('.o_control_panel .o_searchview_dropdown_toggler');
                if (button.classList.contains('o-dropdown-no-caret')) {
                    throw new Error('the arrow is hidden');
                }
                const arrow = getComputedStyle(button, '::after').content;
                if (!arrow.includes('arrow_drop_down')) {
                    throw new Error('no arrow drawn: ' + arrow);
                }
                console.log('test successful');
            })().catch((e) => console.error(e.message));
        """
        self.browser_js('/odoo/action-base.action_partner_form', code, login='admin', timeout=60)
