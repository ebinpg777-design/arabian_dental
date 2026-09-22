/** @odoo-module **/

import { Component, onWillDestroy, onWillStart, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { tileDomain } from "@dynamic_filter_tiles/core/tile_domain";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { KeepLast } from "@web/core/utils/concurrency";
import { useService } from "@web/core/utils/hooks";
import { FilterTile } from "../filter_tile/filter_tile";

const REFRESH_MS = 60000;

/**
 * The Tile Wall: every ribbon in the database on one screen.
 *
 * Tiles were built to sit above a view, which makes them invisible until you go
 * looking. The wall turns the same records into a morning overview - grouped by
 * model, alerting tiles first - and every tile there is still a filter: click
 * one and you land in its list with the filter already applied, rather than in
 * a dashboard you cannot act on.
 *
 * Everything comes from a single `get_overview` call that batches one grouped
 * read per model, with the reader's own rights.
 */
export class TileOverview extends Component {
    static template = "dynamic_filter_tiles.TileOverview";
    static components = { FilterTile };
    static props = { action: { type: Object, optional: true }, "*": true };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.keepLast = new KeepLast();
        this.state = useState({
            sections: [],
            loading: true,
            onlyAlerts: false,
            live: false,
        });

        onWillStart(() => this.load());
        this.timer = null;
        onWillDestroy(() => this.stopLive());
    }

    async load() {
        this.state.loading = true;
        try {
            const result = await this.keepLast.add(
                this.orm.call("filter.tile", "get_overview", [this.state.onlyAlerts])
            );
            this.state.sections = result.sections || [];
        } catch {
            this.state.sections = [];
        }
        this.state.loading = false;
    }

    get alertCount() {
        return this.state.sections.reduce(
            (total, section) =>
                total + section.tiles.filter((tile) => tile.data && tile.data.alert).length,
            0
        );
    }

    get tileCount() {
        return this.state.sections.reduce((total, section) => total + section.tiles.length, 0);
    }

    toggleAlerts() {
        this.state.onlyAlerts = !this.state.onlyAlerts;
        this.load();
    }

    toggleLive() {
        this.state.live = !this.state.live;
        this.stopLive();
        if (this.state.live) {
            this.timer = browser.setInterval(() => this.load(), REFRESH_MS);
        }
    }

    stopLive() {
        if (this.timer) {
            browser.clearInterval(this.timer);
            this.timer = null;
        }
    }

    /** A tile on the wall is still a filter: open its records, filtered. */
    openTile(section, tile) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: `${section.label} · ${tile.name}`,
            res_model: section.model,
            views: [
                [false, "list"],
                [false, "form"],
            ],
            domain: tileDomain(tile),
        });
    }

    openSection(section) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: section.label,
            res_model: section.model,
            views: [
                [false, "list"],
                [false, "form"],
            ],
        });
    }

    get emptyMessage() {
        return this.state.onlyAlerts
            ? _t("Nothing is over its threshold. Good morning.")
            : _t("No tiles yet. Build one from the ribbon above any list view.");
    }
}

registry.category("actions").add("dynamic_filter_tiles.overview", TileOverview);
