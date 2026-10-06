# -*- coding: utf-8 -*-
"""The dashboard in a real browser.

Odoo 20 runs on OWL 3, whose failures only show at runtime: a template that
reads a member without ``this.``, a prop the schema refuses or a ref that is
not a signal all build into a perfectly valid asset bundle and then break - or
silently blank - the page. These tests mount the client action with one card
of every kind and the card form with every widget the module ships, click
through them, and fail on any JavaScript error the page logs.
"""
from odoo.tests import HttpCase, tagged

from .test_dashboard import TestDashboardCommon

# Mirrors CHART_KINDS / SVG_KINDS / MATRIX_KINDS of static/src/dashboard/board_card.js.
CHART_KINDS = ['bar', 'hbar', 'lollipop', 'stacked', 'combo', 'waterfall', 'pareto', 'bubble',
               'line', 'area', 'pie', 'donut', 'polar', 'radar', 'funnel', 'scatter']

JS_HELPERS = """
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    async function waitFor(check, what, timeout = 30000) {
        const start = Date.now();
        while (Date.now() - start < timeout) {
            let result;
            try {
                result = check();
            } catch {
                result = false;
            }
            if (result) {
                return result;
            }
            await sleep(100);
        }
        throw new Error(`timed out waiting for ${what}`);
    }
    function $(selector, root = document) {
        return root.querySelector(selector);
    }
    function $$(selector, root = document) {
        return [...root.querySelectorAll(selector)];
    }
    function press(key) {
        const target = document.activeElement || document.body;
        target.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
        target.dispatchEvent(new KeyboardEvent("keyup", { key, bubbles: true, cancelable: true }));
    }
    function assert(condition, message) {
        if (!condition) {
            throw new Error(message);
        }
    }
    async function webClientEnv() {
        return waitFor(() => odoo.__WOWL_DEBUG__ && odoo.__WOWL_DEBUG__.root && odoo.__WOWL_DEBUG__.root.env,
                       "the web client");
    }
"""


@tagged('post_install', '-at_install')
class TestDashboardUi(HttpCase, TestDashboardCommon):
    """The client action and the card form, mounted and clicked."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        field = cls.env['ir.model.fields']._get
        split = field('res.partner', 'is_company').id
        stack = field('res.partner', 'type').id
        date = field('res.partner', 'create_date').id
        latitude = field('res.partner', 'partner_latitude').id
        longitude = field('res.partner', 'partner_longitude').id
        cls.partners.write({'partner_longitude': 10.0})
        cls.board.name = 'Every Kind'
        specs = {
            'kpi': {'compare': True, 'date_field_id': date},
            'gauge': {'target_value': 10},
            'status': {},
            'bullet': {'target_value': 10},
            'formula': {'formula': 'a / b * 100', 'variable_ids': [
                (0, 0, {'name': 'a', 'label': 'Companies', 'domain': "[('is_company', '=', True)]"}),
                (0, 0, {'name': 'b', 'label': 'All', 'domain': '[]'}),
            ]},
            'bar': {'group_by_field_id': split},
            'hbar': {'group_by_field_id': split},
            'lollipop': {'group_by_field_id': split},
            'stacked': {'group_by_field_id': split, 'stack_field_id': stack},
            'combo': {'group_by_field_id': split, 'aggregate': 'sum',
                      'measure_field_id': latitude, 'measure2_field_id': longitude},
            'waterfall': {'group_by_field_id': split},
            'pareto': {'group_by_field_id': split},
            'bubble': {'group_by_field_id': split, 'aggregate': 'sum',
                       'measure_field_id': latitude, 'measure2_field_id': longitude},
            'treemap': {'group_by_field_id': split},
            'calendar': {'date_field_id': date},
            'heatmap': {'group_by_field_id': split, 'stack_field_id': stack},
            'pivot': {'group_by_field_id': split, 'stack_field_id': stack},
            'line': {'date_field_id': date},
            'area': {'date_field_id': date},
            'pie': {'group_by_field_id': split},
            'donut': {'group_by_field_id': split},
            'polar': {'group_by_field_id': split},
            'radar': {'group_by_field_id': split},
            'funnel': {'group_by_field_id': split},
            'scatter': {'aggregate': 'sum', 'measure_field_id': latitude,
                        'measure2_field_id': longitude},
            'progress': {'group_by_field_id': split},
            'table': {'group_by_field_id': split},
            'list': {'aggregate': 'sum', 'measure_field_id': latitude},
        }
        Item = cls.env['dashboard.item']
        cls.cards = Item
        for sequence, (kind, values) in enumerate(specs.items()):
            cls.cards |= Item.create({
                'name': 'Kind %s' % kind, 'board_id': cls.board.id, 'sequence': sequence,
                'model_id': cls.partner_model.id, 'domain': cls.base_domain,
                'kind': kind, 'aggregate': 'count', 'width': 4, **values,
            })
        cls.cards |= Item.create({
            'name': 'Kind text', 'board_id': cls.board.id, 'sequence': 99, 'kind': 'text',
            'note': '<p>Every kind of card, drawn.</p>',
        })
        cls.kinds = sorted(set(cls.cards.mapped('kind')))

    def test_every_kind_of_card_mounts_on_the_board(self):
        all_kinds = [key for key, _label in self.env['dashboard.item']._fields['kind'].selection]
        self.assertEqual(self.kinds, sorted(all_kinds), "the board must carry one card of every kind")
        code = JS_HELPERS + """
        (async () => {
            const env = await webClientEnv();
            await env.services.action.doAction({
                type: "ir.actions.client", tag: "ebshel_dashboard.board",
                params: { board_id: %(board)s },
            });
            await waitFor(() => $$(".o_dbb_grid .o_dbc_card").length === %(count)s, "every card");
            await waitFor(() => !$(".o_dbc_waiting"), "every card's numbers");
            const tour = $(".o_dbb_tour");
            if (tour) {
                $(".o_dbb_tour .btn-secondary").click();
                await waitFor(() => !$(".o_dbb_tour"), "the tour to close");
            }
            assert(!$(".o_dbc_error"), "a card shows an error: " + ($(".o_dbc_error") || {}).textContent);
            for (const kind of %(kinds)s) {
                assert($(".o_dbc_card.o_dbc_" + kind), "no card of kind " + kind);
            }
            await waitFor(() => %(charts)s.every((kind) => {
                const canvas = $(".o_dbc_" + kind + " canvas");
                return canvas && window.Chart && window.Chart.getChart(canvas);
            }), "every Chart.js card to draw");
            assert($$(".o_dbc_treemap svg").length && $$(".o_dbc_calendar svg").length, "the SVG cards are blank");
            assert($(".o_dbc_heatmap table, .o_dbc_heatmap .o_dbc_matrix, .o_dbc_heatmap [class*=cell]"),
                   "the heat map is blank");
            assert($(".o_dbc_pivot table"), "the pivot table is blank");
            await waitFor(() => /(^|\\D)3(\\D|$)/.test($(".o_dbc_kpi .o_dbc_body").textContent), "the number card to count 3")
                .catch((error) => {
                    throw new Error(`${error.message}: "${$(".o_dbc_kpi .o_dbc_body").textContent.trim()}"`);
                });
            assert(/Every kind of card/.test($(".o_dbc_text").textContent), "the note card is blank");
            // Odoo 20 has no FontAwesome: every icon is a Material Symbols name.
            const icons = $$(".o_dbb_action i.oi");
            assert(icons.length > 20, "icons are missing");
            const bad = icons.filter((icon) => !icon.dataset.icon || icon.dataset.icon.startsWith("fa"));
            assert(!bad.length, "icons without a drawable name: " + bad.map((i) => i.outerHTML).join(" "));
            assert(!$$(".o_dbb_action .fa").length, "a FontAwesome class is left");

            // The more menu of a card, and its table view.
            const bar = $(".o_dbc_bar");
            $(".o_dbc_menu_wrap .o_dbc_tool", bar).click();
            await waitFor(() => $(".o_dbc_menu", bar), "the card menu");
            $(".o_dbc_menu .dropdown-item", bar).click();
            await waitFor(() => !$(".o_dbc_bar canvas") && $(".o_dbc_bar table"), "the bars as a table");

            // Quick find (Q): a dialog with the boards and the cards.
            press("q");
            const input = await waitFor(() => $(".o_dbq_input"), "the quick find dialog");
            input.value = "Kind pie";
            input.dispatchEvent(new Event("input", { bubbles: true }));
            await waitFor(() => $$(".o_dbq_hit").some((hit) => /Kind pie/.test(hit.textContent)), "a card hit");
            press("Escape");
            await waitFor(() => !$(".o_dbq_input"), "the quick find dialog to close");

            // The title icon opens the icon panel; a pick is saved at once.
            $(".o_dbb_title_icon").click();
            await waitFor(() => $(".o_dbf_icon_panel .o_dbf_icon_cell"), "the icon panel");
            const search = $(".o_dbf_icon_panel input");
            search.value = "rocket";
            search.dispatchEvent(new Event("input", { bubbles: true }));
            await waitFor(() => $$(".o_dbf_icon_panel .o_dbf_icon_cell").length === 1, "the search to narrow");
            $(".o_dbf_icon_panel .o_dbf_icon_cell").click();
            await waitFor(() => $(".o_dbb_title_icon i").dataset.icon === "rocket_launch", "the new icon");

            // Arrange mode (E), then back.
            press("e");
            await waitFor(() => $(".o_dbb_grid.o_dbb_editing .o_dbc_handle"), "arrange mode");
            press("e");
            await waitFor(() => !$(".o_dbb_grid.o_dbb_editing"), "arrange mode to end");

            // The sidebar collapses and expands.
            const wasMin = !!$(".o_dbb_side_min");
            $(".o_dbb_side_toggle").click();
            await waitFor(() => !!$(".o_dbb_side_min") !== wasMin, "the sidebar to toggle");
            $(".o_dbb_side_toggle").click();
            await waitFor(() => !!$(".o_dbb_side_min") === wasMin, "the sidebar to toggle back");

            // Clicking a row of the table narrows the board to it.
            const row = $(".o_dbc_card.o_dbc_table tbody tr[role=button]");
            assert(row, "the table card has no row");
            row.click();
            await waitFor(() => $(".o_dbb_chips .o_dbb_chip"), "the focus chip");
            await waitFor(() => $(".o_dbc_card.o_dbc_table .o_dbc_row_focused"), "the focused row");
            await waitFor(() => $(".o_dbc_card.o_dbc_kpi .o_dbc_badge_focus"), "the other cards to narrow");
            press("Escape");
            await waitFor(() => !$(".o_dbb_chips .o_dbb_chip") && !$(".o_dbc_badge_focus"), "the focus to clear");
            console.log("test successful");
        })().catch((error) => console.error("dashboard UI: " + (error && error.stack || error)));
        """ % {
            'board': self.board.id,
            'count': len(self.cards),
            'kinds': repr(self.kinds).replace("'", '"'),
            'charts': repr(CHART_KINDS).replace("'", '"'),
        }
        self.browser_js('/odoo', code, login='admin', timeout=180)
        self.assertEqual(self.board.icon, 'rocket_launch')

    def test_the_card_form_and_its_widgets_mount(self):
        card = self.cards.filtered(lambda item: item.kind == 'bar')
        code = JS_HELPERS + """
        (async () => {
            const env = await webClientEnv();
            await env.services.action.doAction({
                type: "ir.actions.act_window", res_model: "dashboard.item", res_id: %(card)s,
                views: [[false, "form"]],
            });
            // The live preview is the board's own card, computed on the server.
            await waitFor(() => $(".o_dbf_preview .o_dbc_card.o_dbc_bar canvas"), "the live preview");
            await waitFor(() => window.Chart.getChart($(".o_dbf_preview canvas")), "the preview chart");
            // The kind picker: one tile per kind, the current one lit.
            assert($$(".o_dbf_kind").length >= 27, "the kind picker lacks kinds: " + $$(".o_dbf_kind").length);
            assert($(".o_dbf_kind.o_dbf_kind_on"), "the current kind is not lit");
            // The setup chips say what is set.
            await waitFor(() => $$(".o_dbf_setup_chip").length, "the setup chips");
            // Every tab of the card's settings mounts its widgets.
            for (const tab of $$(".o_dbf_tabs .nav-link")) {
                tab.click();
                await sleep(150);
            }
            await waitFor(() => $(".o_dbf_tabs .nav-link[name=numbers]"), "the numbers tab");
            $(".o_dbf_tabs .nav-link[name=numbers]").click();
            await waitFor(() => $$(".o_dbf_sample_pair").length === 3, "the number samples");
            $(".o_dbf_tabs .nav-link[name=chart]").click();
            await waitFor(() => $$(".o_dbf_pal").length, "the palette picker");
            $(".o_dbf_tabs .nav-link[name=shape]").click();
            await waitFor(() => $(".o_dbf_suggest .o_dbf_suggest_chip"), "the field suggestions");
            // A suggestion fills the many2one as if it had been picked in it.
            const chip = $$(".o_dbf_suggest .o_dbf_suggest_chip").find((node) => !node.classList.contains("o_dbf_suggest_on"));
            const chipLabel = chip.querySelector("span").textContent;
            chip.click();
            await waitFor(() => $(".o_dbf_suggest .o_dbf_suggest_on") && $$(".o_dbf_setup_chip").some((node) => node.textContent.includes(chipLabel)),
                          "the suggested field to be set");
            // Icon, colour and width.
            await waitFor(() => $(".o_dbf_icon_face i.oi"), "the icon picker");
            assert($(".o_dbf_icon_face i.oi").dataset.icon === "bar_chart", "the icon face is wrong");
            $(".o_dbf_icon_open").click();
            await waitFor(() => $(".o_dbf_icon_panel .o_dbf_icon_cell"), "the icon panel");
            const cell = $(".o_dbf_icon_panel .o_dbf_icon_cell[title=trophy]");
            assert(cell, "no trophy in the icon panel");
            cell.click();
            await waitFor(() => $(".o_dbf_icon_input").value === "trophy", "the picked icon");
            await waitFor(() => $(".o_dbf_preview .o_dbc_icon").dataset.icon === "trophy", "the preview icon");
            assert($$(".o_dbf_swatch").length > 4, "the colour picker is blank");
            $$(".o_dbf_swatch")[2].click();
            await waitFor(() => $(".o_dbf_swatch.o_dbf_swatch_on"), "the picked colour");
            const segments = $$(".o_dbf_width_seg");
            assert(segments.length === 12, "the width picker has not twelve columns");
            segments[5].click();
            await waitFor(() => /6\\/12/.test($(".o_dbf_width_label").textContent), "the picked width");
            // Another kind redraws the preview.
            $$(".o_dbf_kind").find((tile) => /Pie/.test(tile.textContent)).click();
            await waitFor(() => $(".o_dbf_preview .o_dbc_pie canvas"), "the preview to redraw as a pie");
            $(".o_form_button_save").click();
            await waitFor(() => !$(".o_form_button_save") || !$(".o_form_dirty, .o_form_status_indicator_buttons:not(.invisible) .o_form_button_save"),
                          "the card to be saved");
            console.log("test successful");
        })().catch((error) => console.error("dashboard form UI: " + (error && error.stack || error)));
        """ % {'card': card.id}
        self.browser_js('/odoo', code, login='admin', timeout=180)
        self.assertEqual((card.kind, card.icon, card.width), ('pie', 'trophy', 6))

    def test_the_readers_controls_work(self):
        """Period, mine, favourite, tabs, watchlist, notes, saved views and search."""
        self.board.allow_mine = True
        code = JS_HELPERS + """
        (async () => {
            const env = await webClientEnv();
            await env.services.action.doAction({
                type: "ir.actions.client", tag: "ebshel_dashboard.board",
                params: { board_id: %(board)s },
            });
            await waitFor(() => $$(".o_dbb_grid .o_dbc_card").length === %(count)s && !$(".o_dbc_waiting"),
                          "the board");
            if ($(".o_dbb_tour")) {
                $(".o_dbb_tour .btn-secondary").click();
            }

            // The period menu: a pick re-reads the board and names itself.
            $(".o_dbb_period_btn").click();
            await waitFor(() => $(".o_dbb_period_menu"), "the period menu");
            const before = $(".o_dbb_period_label").textContent;
            const item = $$(".o_dbb_period_item").find((button) => !button.classList.contains("o_dbb_period_on"));
            item.click();
            await waitFor(() => !$(".o_dbb_period_menu") && $(".o_dbb_period_label").textContent !== before,
                          "the new period");
            await waitFor(() => !$(".o_dbc_waiting"), "the cards over the new period");

            // Only mine (M), as a chip.
            $$(".o_dbb_pill").find((button) => /Mine/.test(button.textContent)).click();
            await waitFor(() => $(".o_dbb_chip_mine"), "the mine chip");
            $(".o_dbb_chip_mine .o_dbb_chip_btn").click();
            await waitFor(() => !$(".o_dbb_chip_mine"), "the mine chip to go");

            // Favourite.
            $(".o_dbb_star").click();
            await waitFor(() => $(".o_dbb_star.o_dbb_star_on i.oi-filled"), "the star to fill");

            // Search the tiles.
            const search = $(".o_dbb_search input");
            search.value = "Kind pie";
            search.dispatchEvent(new Event("input", { bubbles: true }));
            await waitFor(() => $$(".o_dbb_grid .o_dbc_card").length === 1, "the search to narrow the tiles");
            $(".o_dbb_search_clear").click();
            await waitFor(() => $$(".o_dbb_grid .o_dbc_card").length === %(count)s, "every tile back");

            // A tab, added in arrange mode, then picked.
            $$(".o_dbb_btn").find((button) => /Arrange/.test(button.textContent)).click();
            await waitFor(() => $(".o_dbb_tab_add"), "the add-tab button");
            $(".o_dbb_tab_add").click();
            const tabInput = await waitFor(() => $(".o_dbb_tab_input"), "the tab name box");
            tabInput.value = "Second page";
            tabInput.dispatchEvent(new Event("input", { bubbles: true }));
            tabInput.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
            // Adding a tab reloads the board, then opens the new (empty) page.
            await waitFor(() => /Second page/.test(($(".o_dbb_tab.o_dbb_tab_on") || {}).textContent)
                                && !$$(".o_dbb_grid .o_dbc_card").length && !$(".o_dbb_loading"), "the new tab");
            $$(".o_dbb_btn").find((button) => /Done/.test(button.textContent)).click();
            await waitFor(() => !$(".o_dbb_grid.o_dbb_editing"), "arrange mode to end");
            $$(".o_dbb_tab")[0].click();
            await waitFor(() => $$(".o_dbb_grid .o_dbc_card").length === %(count)s && !$(".o_dbc_waiting"),
                          "the overview again");

            // Pin a number to the watchlist and see it there.
            $(".o_dbc_card.o_dbc_kpi .o_dbc_pin").click();
            await waitFor(() => $(".o_dbc_card.o_dbc_kpi .o_dbc_pin_on"), "the pin").catch((error) => {
                throw new Error(`${error.message}: pin=${$(".o_dbc_card.o_dbc_kpi .o_dbc_pin").outerHTML} `
                    + `count=${($(".o_dbb_side_count") || {}).textContent} watch=${!!$(".o_dbb_watch")} `
                    + `items=${$$(".o_dbb_watch_item").length}`);
            });
            // Pinning opens the watchlist.
            await waitFor(() => $$(".o_dbb_watch_item").some((watch) => /Kind kpi/.test(watch.textContent)),
                          "the pinned card in the watchlist");

            // My notes, saved as they are typed.
            $$(".o_dbb_side_item").find((link) => /notes/.test(link.title)).click();
            const note = await waitFor(() => $(".o_dbb_notes_text"), "the notes drawer");
            note.value = "Check the pie on Monday";
            note.dispatchEvent(new Event("input", { bubbles: true }));
            await waitFor(() => /saved/.test($(".o_dbb_notes_state").textContent), "the note to be saved");

            // A saved view, from the more menu.
            $(".o_dbb_more .o_dbb_btn_icon").click();
            await waitFor(() => $(".o_dbb_menu"), "the more menu");
            $$(".o_dbb_menu .dropdown-item").find((link) => /Save this view/.test(link.textContent)).click();
            const viewInput = await waitFor(() => $(".o_dbb_view_input"), "the view name box");
            viewInput.value = "Monday";
            viewInput.dispatchEvent(new Event("input", { bubbles: true }));
            viewInput.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
            await waitFor(() => $$(".o_dbb_view").some((view) => /Monday/.test(view.textContent)), "the saved view");

            // The board's settings form: icon and colour pickers.
            $(".o_dbb_more .o_dbb_btn_icon").click();
            await waitFor(() => $(".o_dbb_menu"), "the more menu");
            $$(".o_dbb_menu .dropdown-item").find((link) => /Settings/.test(link.textContent)).click();
            await waitFor(() => $(".o_form_view .o_dbf_icon_face i.oi"), "the board form");
            assert($(".o_dbf_icon_face i.oi").dataset.icon === "speed", "the board form shows the wrong icon");
            assert($$(".o_form_view .o_dbf_swatch").length > 4, "the board colour picker is blank");
            console.log("test successful");
        })().catch((error) => console.error("dashboard reader UI: " + (error && error.stack || error)));
        """ % {'board': self.board.id, 'count': len(self.cards)}
        self.browser_js('/odoo', code, login='admin', timeout=180)
        self.assertEqual(self.board.tab_ids.mapped('name'), ['Second page'])
        note = self.env['dashboard.note'].search([('board_id', '=', self.board.id)])
        self.assertIn('Check the pie', note.body or '')
        self.assertTrue(self.env['dashboard.view'].search([('board_id', '=', self.board.id), ('name', '=', 'Monday')]))

    def test_the_builders_actions_work(self):
        """Arrange: widen, duplicate, remove (with its dialog); edit in a dialog;
        drill through and come back; present."""
        code = JS_HELPERS + """
        (async () => {
            const env = await webClientEnv();
            const count = %(count)s;
            await env.services.action.doAction({
                type: "ir.actions.client", tag: "ebshel_dashboard.board",
                params: { board_id: %(board)s },
            });
            const ready = (n) => $$(".o_dbb_grid .o_dbc_card").length === n && !$(".o_dbc_waiting")
                && !$(".o_dbb_loading");
            await waitFor(() => ready(count), "the board");
            if ($(".o_dbb_tour")) {
                $(".o_dbb_tour .btn-secondary").click();
            }

            // Arrange mode: the first card's tools.
            press("e");
            await waitFor(() => $(".o_dbb_cell .o_dbb_resize"), "the arrange tools");
            const firstId = $(".o_dbb_grid .o_dbb_cell").dataset.itemId;
            const cell = () => $(`.o_dbb_grid .o_dbb_cell[data-item-id="${firstId}"]`);
            assert($(".o_dbb_resize_label", cell()).textContent === "4/12", "the first card is not 4/12");
            $(".o_dbb_mini[title=Wider]", cell()).click();
            await waitFor(() => $(".o_dbb_resize_label", cell()).textContent === "5/12", "the card to widen");
            $(".o_dbb_mini[title='Duplicate this card']", cell()).click();
            await waitFor(() => ready(count + 1), "the duplicated card");
            const copy = $$(".o_dbb_grid .o_dbb_cell").find((node) => /\\(copy\\)/.test(node.textContent));
            assert(copy, "the copy is not on the board");
            $(".o_dbb_mini_danger", copy).click();
            const confirm = await waitFor(() => $(".modal .modal-footer .btn-danger"), "the remove dialog");
            assert(/will be removed/.test($(".modal .modal-body").textContent), "the dialog does not name the card");
            confirm.click();
            await waitFor(() => !$(".modal") && ready(count), "the card to be removed");

            // Drag the first card onto the third one (the sortable of arrange mode).
            const order = () => $$(".o_dbb_grid .o_dbb_cell[data-item-id]").map((node) => node.dataset.itemId);
            const before = order();
            const handle = $(".o_dbc_handle", cell());
            const target = $$(".o_dbb_grid .o_dbb_cell[data-item-id]")[2];
            const at = (el) => {
                const rect = el.getBoundingClientRect();
                return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
            };
            const pointer = (type, el, { x, y }) => el.dispatchEvent(new PointerEvent(type, {
                bubbles: type !== "pointerenter", cancelable: true, composed: true, clientX: x, clientY: y,
                button: 0, buttons: 1, pointerId: 1, isPrimary: true, pointerType: "mouse",
            }));
            pointer("pointerdown", handle, at(handle));
            await sleep(50);
            pointer("pointermove", handle, { x: at(handle).x + 40, y: at(handle).y + 40 });
            await sleep(50);
            pointer("pointermove", target, at(target));
            pointer("pointerenter", target, at(target));
            await sleep(50);
            pointer("pointermove", target, at(target));
            await sleep(50);
            pointer("pointerup", target, at(target));
            await waitFor(() => order()[0] !== before[0] && ready(count), "the dragged card to move");

            // Drag the corner of a card: taller, saved.
            const grip = $(".o_dbb_grip", cell());
            const corner = at(grip);
            pointer("pointerdown", grip, corner);
            pointer("pointermove", document.body, { x: corner.x, y: corner.y + 120 });
            await waitFor(() => /px/.test(($(".o_dbb_resize_live") || {}).textContent), "the live size");
            pointer("pointerup", document.body, { x: corner.x, y: corner.y + 120 });
            await waitFor(() => !$(".o_dbb_resize_live") && ready(count), "the new height to be saved");
            press("e");
            await waitFor(() => !$(".o_dbb_grid.o_dbb_editing"), "arrange mode to end");

            // Edit a card in a dialog: the form and its live preview mount there too.
            $(".o_dbb_grid .o_dbc_card.o_dbc_donut .o_dbc_tool[title='Edit this card']").click();
            await waitFor(() => $(".modal .o_form_view .o_dbf_preview .o_dbc_card.o_dbc_donut canvas"),
                          "the card form in a dialog");
            $(".modal .btn-close").click();
            await waitFor(() => !$(".modal") && ready(count), "the dialog to close and the board to reload");

            // Drill through a number, then come back by the breadcrumb.
            $(".o_dbb_grid .o_dbc_card.o_dbc_kpi .o_dbc_kpi_body").click();
            await waitFor(() => $(".o_list_view .o_data_row"), "the records behind the number");
            assert($$(".o_list_view .o_data_row").length === 3, "the number did not open its 3 records");
            $(".o_breadcrumb .breadcrumb-item a, .breadcrumb .breadcrumb-item a").click();
            await waitFor(() => ready(count), "the board again");

            // Present (F) and leave (Esc).
            press("f");
            await waitFor(() => $(".o_dbb_presenting") && !$(".o_dbb_side"), "presentation mode");
            press("Escape");
            await waitFor(() => !$(".o_dbb_presenting") && $(".o_dbb_side"), "presentation mode to end");
            console.log("test successful");
        })().catch((error) => console.error("dashboard builder UI: " + (error && error.stack || error)));
        """ % {'board': self.board.id, 'count': len(self.cards)}
        self.browser_js('/odoo', code, login='admin', timeout=180)
        first = self.cards.filtered(lambda card: card.width == 5)
        self.assertEqual(first.name, 'Kind kpi')
        self.assertGreater(first.height, 0, "the corner drag was not saved")
        self.assertNotEqual(self.board.item_ids.sorted('sequence')[0], first, "the drag was not saved")
        self.assertEqual(len(self.board.item_ids), len(self.cards))
