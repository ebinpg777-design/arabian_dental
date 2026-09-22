/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { KanbanController } from "@web/views/kanban/kanban_controller";
import { ListController } from "@web/views/list/list_controller";
import { SearchBar } from "@web/search/search_bar/search_bar";
import { FilterTileBar } from "../components/filter_tile_bar/filter_tile_bar";

/**
 * Make the tile bar available to *every* list and kanban view - including the
 * ones other modules subclass (dashboard lists, file-upload lists, and every
 * other controller built on top of the standard ones).
 *
 * The component is exposed as an instance property rather than through
 * `static components`, because subclasses snapshot `static components` when
 * their class is defined: anything we add to the base afterwards would be
 * invisible to them. An instance property set in `setup()` is inherited by
 * every subclass that calls `super.setup()`, which is all of them.
 *
 * The matching template patches then render it with `t-component`.
 */
patch(ListController.prototype, {
    setup() {
        super.setup(...arguments);
        this.FilterTileBar = FilterTileBar;
    },
});

patch(KanbanController.prototype, {
    setup() {
        super.setup(...arguments);
        this.FilterTileBar = FilterTileBar;
    },
});

/**
 * Backspace in the search bar must not take the screen down.
 *
 * Core reads the facet to drop as `facets[navigator.activeItemIndex]` - an index
 * into the NAVIGATOR's items, which also holds the input, and which goes stale
 * the moment the facet list changes underneath it. The read then returns
 * `undefined` and `facet.groupId` throws "Cannot read properties of undefined",
 * with the Oops dialog over a list the user was only trying to type in.
 *
 * This bar changes facets more than most - selecting a tile rebuilds them - so
 * the guard belongs here: no facet, nothing to remove, and the keystroke is
 * simply ignored. (client, 2026-09-09, seen on the Work Orders list)
 */
patch(SearchBar.prototype, {
    removeFacet(facet) {
        if (!facet) {
            return;
        }
        return super.removeFacet(...arguments);
    },
});
