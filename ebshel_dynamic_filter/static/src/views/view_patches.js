/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { KanbanController } from "@web/views/kanban/kanban_controller";
import { ListController } from "@web/views/list/list_controller";
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
