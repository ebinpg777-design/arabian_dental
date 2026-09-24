/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";

/**
 * Tiles that watch themselves.
 *
 * A tile given a threshold is checked hourly on the server, so the warning
 * reaches its audience wherever they happen to be in Odoo - not only on the
 * view the tile lives above. The notification carries the tile's own filter, so
 * "why?" is one click away.
 *
 * The message arrives on the user's own partner channel, which the web client
 * is already subscribed to: no extra channel, nothing guessable from outside.
 */
export const filterTileAlertService = {
    dependencies: ["bus_service", "notification", "action"],
    start(env, { bus_service: busService, notification, action }) {
        busService.subscribe("filter_tiles.alert", (payload) => {
            if (!payload || !payload.title) {
                return;
            }
            const buttons = [];
            if (payload.model) {
                buttons.push({
                    name: _t("Show me"),
                    onClick: () =>
                        action.doAction({
                            type: "ir.actions.act_window",
                            name: payload.title,
                            res_model: payload.model,
                            views: [
                                [false, "list"],
                                [false, "form"],
                            ],
                            domain: payload.domain || "[]",
                        }),
                });
            }
            notification.add(payload.message, {
                title: _t("Filter tile alert: %s", payload.title),
                type: "warning",
                sticky: true,
                buttons,
            });
        });
        // Nothing else in a plain `web` database opens the websocket, so the
        // alert service is the one that asks for it.
        busService.start();
    },
};

registry.category("services").add("filter_tiles_alert", filterTileAlertService);
