/** @odoo-module **/

import { registry } from "@web/core/registry";
import { EventBus } from "@odoo/owl";

/**
 * Session-wide cache of every tile definition the user may see.
 *
 * Tile definitions are few and change rarely, so they are fetched once and
 * shared by every view: opening a list view of a model without tiles then costs
 * exactly zero round-trips. Anything that edits tiles calls `reload()`, which
 * refetches and tells every mounted tile bar to redraw.
 */
export const filterTileRegistryService = {
    dependencies: ["orm"],
    start(env, { orm }) {
        const bus = new EventBus();
        const EMPTY = { tiles: {}, can_manage: false };
        let registryProm = null;

        function fetch() {
            if (!registryProm) {
                registryProm = orm
                    .silent
                    .call("filter.tile", "get_tile_registry", [])
                    .catch((error) => {
                        // A tile bar must never take a view down with it.
                        console.warn("Filter tiles: registry unavailable", error);
                        return EMPTY;
                    });
            }
            return registryProm;
        }

        return {
            bus,

            /**
             * The whole registry: tiles by model, row names, and what the user
             * is allowed to do. Which of those tiles belong on a given view is
             * the bar's business, not the cache's.
             */
            async getRegistry() {
                return (await fetch()) || EMPTY;
            },

            /** Drop the cache, refetch, and redraw every mounted tile bar. */
            async reload() {
                registryProm = null;
                await fetch();
                bus.trigger("FILTER_TILES:UPDATE");
            },
        };
    },
};

registry.category("services").add("filter_tiles", filterTileRegistryService);
