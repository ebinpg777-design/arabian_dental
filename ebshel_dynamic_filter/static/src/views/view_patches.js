/** @odoo-module **/

import { toRaw } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";
import { KanbanController } from "@web/views/kanban/kanban_controller";
import { ListController } from "@web/views/list/list_controller";
import { FilterTileBar, TILES_RECORDS_SAVED } from "../components/filter_tile_bar/filter_tile_bar";

/**
 * Tell the tile bar when records were saved in place.
 *
 * Tiles count records, and a record edited in the list, a card dragged to
 * another column or a multi-edit changes those counts without the search (or
 * the list itself) being reloaded - the bar used to keep the old numbers until
 * the next filter change. The model's own save hooks are the one place every
 * such save passes through, so they are wrapped, whatever the controller (or a
 * subclass of it) put there.
 */
function signalSavesToTiles(controller) {
    const model = toRaw(controller.model);
    const hooks = model && model.hooks;
    if (!hooks) {
        return;
    }
    for (const name of ["onRecordSaved", "onSavedMulti"]) {
        const original = hooks[name];
        hooks[name] = (...args) => {
            const result = original ? original(...args) : undefined;
            model.bus.trigger(TILES_RECORDS_SAVED);
            return result;
        };
    }
}

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
        signalSavesToTiles(this);
    },
});

patch(KanbanController.prototype, {
    setup() {
        super.setup(...arguments);
        this.FilterTileBar = FilterTileBar;
        signalSavesToTiles(this);
    },
});
