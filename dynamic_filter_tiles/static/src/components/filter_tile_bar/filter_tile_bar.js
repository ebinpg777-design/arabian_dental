/** @odoo-module **/

import {
    Component,
    onWillDestroy,
    onWillStart,
    onWillUpdateProps,
    useRef,
    useState,
} from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { Domain } from "@web/core/domain";
import { tileDomain } from "@dynamic_filter_tiles/core/tile_domain";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { useHotkey } from "@web/core/hotkeys/hotkey_hook";
import { _t } from "@web/core/l10n/translation";
import { usePopover } from "@web/core/popover/popover_hook";
import { KeepLast } from "@web/core/utils/concurrency";
import { useBus, useService } from "@web/core/utils/hooks";
import { useSortable } from "@web/core/utils/sortable_owl";
import { useDebounced } from "@web/core/utils/timing";
import { FormViewDialog } from "@web/views/view_dialogs/form_view_dialog";
import { FilterTile } from "../filter_tile/filter_tile";
import { TileBreakdown } from "../tile_breakdown/tile_breakdown";
import { TileGeneratorDialog } from "../tile_generator/tile_generator_dialog";
import { TileImportDialog } from "../tile_io/tile_import_dialog";

// Alt+1..9 reach the first nine tiles; past that the mouse is faster anyway.
const MAX_SHORTCUTS = 9;
// Auto-refresh steps offered in the studio menu, in seconds.
const REFRESH_STEPS = [0, 30, 60, 300];
// Row height bounds, in pixels. 0 means "as tall as the tiles need to be".
const MIN_ROW_HEIGHT = 56;
const MAX_ROW_HEIGHT = 320;
// Keep in sync with MAX_TILE_ROWS in models/filter_tile.py.
const MAX_ROWS = 6;

/**
 * The ribbon of filter tiles shown above a list or kanban view.
 *
 * Responsibilities:
 *  - pull the tile definitions for the current model (cached service, no cost
 *    when the model has none),
 *  - recompute their values against the domain the user is currently looking
 *    at, every time the search bar changes,
 *  - turn a click into an ordinary, removable search facet,
 *  - host the studio affordances (capture / generate / reorder / edit).
 */
export class FilterTileBar extends Component {
    static template = "dynamic_filter_tiles.FilterTileBar";
    static components = { FilterTile, Dropdown, DropdownItem };
    static props = {
        list: { type: Object, optional: true },
        viewType: { type: String, optional: true },
    };
    static defaultProps = { viewType: "list" };

    setup() {
        this.orm = useService("orm");
        this.dialog = useService("dialog");
        this.action = useService("action");
        this.notification = useService("notification");
        this.tileService = useService("filter_tiles");
        this.keepLast = new KeepLast();
        this.rootRef = useRef("root");
        // Which domains the browser has already refused, keyed by the text.
        this.domainCheck = {};

        this.state = useState({
            defs: [],
            values: {},
            total: 0,
            canManage: false,
            canPersonalize: false,
            loading: true,
            collapsed: this.readSetting("collapsed", false),
            compact: this.readSetting("compact", false),
            hintDismissed: this.readSetting("hint-dismissed", false),
            refreshEvery: this.readNumberSetting("refresh", 0),
            // How tall each row is, keyed by row number. Unlike a tile's width -
            // which belongs to the tile, and so to everybody - this is how *you*
            // like to see the ribbon, and it stays in this browser.
            rowHeights: {},
            resizingRow: 0,
            // Rows a manager opened but has not filled yet: an empty row has no
            // tiles to remember it, so the bar has to.
            extraRows: 0,
            rowNames: {},
            multiSelect: this.readSetting("multi-select", false),
            // "All" is the more common intent for a deliberately-built selection
            // (late AND assigned to me); "any" is the one Ctrl-click already gave
            // you for free, so it stays available but is no longer the default.
            combineAll: this.readSetting("combine-all", true),
        });
        for (let row = 1; row <= MAX_ROWS; row++) {
            this.state.rowHeights[row] = this.readNumberSetting(`row-height-${row}`, 0);
        }
        this.stopRowResize = null;

        this.breakdownPopover = usePopover(TileBreakdown, {
            position: "bottom",
            popoverClass: "o_dft_breakdown_popover",
        });

        // The search model fires a lot during a single interaction; one trailing
        // recompute per burst is plenty.
        this.reload = useDebounced(() => this.computeValues(), 150);

        onWillStart(async () => {
            // Definitions are cached for the session, so this costs one small
            // round-trip on the first view and nothing afterwards. The values
            // are deliberately NOT awaited: no view ever waits on our counts.
            await this.loadDefinitions();
            this.computeValues();
        });
        onWillUpdateProps(() => this.reload());

        if (this.env.searchModel) {
            useBus(this.env.searchModel, "update", () => this.reload());
        }
        useBus(this.tileService.bus, "FILTER_TILES:UPDATE", async () => {
            await this.loadDefinitions();
            await this.computeValues();
        });

        // Rows are sortable groups: a tile can be dropped further along its own
        // row or onto another one. `applyChangeOnDrop` lets the DOM settle
        // first, so the new layout is simply read back from it.
        useSortable({
            enable: () => this.state.canManage && !this.state.collapsed,
            ref: this.rootRef,
            elements: ".o_dft_tile[data-tile-id]",
            groups: ".o_dft_strip",
            connectGroups: true,
            applyChangeOnDrop: true,
            handle: ".o_dft_grip",
            cursor: "grabbing",
            placeholderClasses: ["o_dft_tile_placeholder"],
            onDrop: () => this.onTileDropped(),
        });

        for (let index = 1; index <= MAX_SHORTCUTS; index++) {
            useHotkey(`alt+${index}`, () => {
                const def = this.orderedDefs[index - 1];
                if (def) {
                    this.onTileSelected(def, {});
                }
            });
        }
        useHotkey("alt+0", () => this.clearTileFacets());

        this.refreshTimer = null;
        this.armRefreshTimer();
        onWillDestroy(() => {
            this.clearRefreshTimer();
            this.stopRowResize?.();
        });
    }

    // ------------------------------------------------------------------
    // Rows
    // ------------------------------------------------------------------
    /**
     * The ribbon, grouped into rows.
     *
     * Always yields at least one row, and keeps empty rows in between rather
     * than closing the gaps: a manager who put tiles on rows 1 and 3 meant it,
     * and a row they just opened has no tiles to speak for it yet.
     */
    get rows() {
        const byRow = new Map();
        for (const def of this.state.defs) {
            const row = Math.max(1, Math.min(MAX_ROWS, def.row || 1));
            if (!byRow.has(row)) {
                byRow.set(row, []);
            }
            byRow.get(row).push(def);
        }
        const named = Object.keys(this.state.rowNames).map(Number).filter(Boolean);
        const highest = Math.max(1, ...byRow.keys(), ...named, this.state.extraRows);
        const rows = [];
        for (let row = 1; row <= highest; row++) {
            rows.push({
                row,
                defs: byRow.get(row) || [],
                name: this.state.rowNames[row] || "",
            });
        }
        return rows;
    }

    /**
     * Row captions only appear once somebody has a reason to see them: a name
     * exists, or a manager is looking at a ribbon that has more than one row.
     * A single unnamed row stays exactly as bare as it was.
     */
    get showRowNames() {
        return (
            Object.values(this.state.rowNames).some(Boolean) ||
            (this.state.canManage && this.rows.length > 1)
        );
    }

    /** Name a row, or clear the name by emptying the box. */
    async renameRow(row, name) {
        const value = (name || "").trim();
        if (value === (this.state.rowNames[row] || "")) {
            return;
        }
        const previous = this.state.rowNames[row] || "";
        this.state.rowNames = { ...this.state.rowNames, [row]: value };
        try {
            await this.orm.call("filter.tile.row", "set_row_name", [this.resModel, row, value]);
            await this.tileService.reload();
        } catch (error) {
            this.state.rowNames = { ...this.state.rowNames, [row]: previous };
            console.warn("Filter tiles: could not rename the row", error);
            this.notification.add(_t("This row could not be renamed."), { type: "warning" });
        }
    }

    onRowNameKeydown(ev) {
        if (ev.key === "Enter") {
            // A row name is one line of meaning, however many lines it wraps
            // onto: Enter commits it instead of adding a line break.
            ev.preventDefault();
            ev.target.blur();
        } else if (ev.key === "Escape") {
            ev.target.value = this.state.rowNames[Number(ev.target.dataset.row)] || "";
            ev.target.blur();
        }
    }

    /** Alt+1…9 walks the ribbon as it is read: row by row, left to right. */
    get orderedDefs() {
        return this.rows.flatMap((row) => row.defs);
    }

    get canAddRow() {
        return this.state.canManage && this.rows.length < MAX_ROWS;
    }

    addRow() {
        this.state.extraRows = Math.min(this.rows.length + 1, MAX_ROWS);
    }

    /** A row can go once there is more than one; the last one always stays. */
    get canDeleteRow() {
        return this.state.canManage && this.rows.length > 1;
    }

    /**
     * Delete a row. Its tiles are not deleted with it - a row is a layout
     * choice, its tiles are work somebody did - so they join the neighbouring
     * row and everything below moves up. The confirmation says exactly that.
     */
    deleteRow(rowInfo) {
        const count = rowInfo.defs.length;
        const target = rowInfo.row > 1 ? rowInfo.row - 1 : 1;
        const remove = async () => {
            if (this.state.extraRows >= rowInfo.row) {
                this.state.extraRows = Math.max(0, this.state.extraRows - 1);
            }
            try {
                await this.orm.call("filter.tile", "delete_row", [
                    this.resModel,
                    rowInfo.row,
                    this.actionId,
                ]);
            } catch (error) {
                console.warn("Filter tiles: could not delete the row", error);
                this.notification.add(_t("This row could not be deleted."), { type: "warning" });
                return;
            }
            await this.tileService.reload();
        };

        if (!count) {
            remove();
            return;
        }
        this.dialog.add(ConfirmationDialog, {
            title: _t("Delete row"),
            body: _t(
                "The %(count)s tiles on this row move to row %(target)s, and the rows below move " +
                    "up. No tile is deleted.",
                { count, target }
            ),
            confirmLabel: _t("Delete the row"),
            confirm: remove,
            cancel: () => {},
        });
    }

    rowStyle(row) {
        const height = this.state.rowHeights[row] || 0;
        return height ? `--dft-row-height: ${height}px;` : "";
    }

    /**
     * How much room the tiles on this row actually have.
     *
     * A short row must not clip its tiles: the stylesheet drops what it can
     * spare, in order - the sparkline first, then the sub-label, then the value
     * shrinks. The height is a number we chose, so no measuring is needed.
     */
    rowSizeClass(row) {
        const height = this.state.rowHeights[row] || 0;
        if (!height) {
            return "";
        }
        if (height < 84) {
            return "o_dft_row_xs";
        }
        if (height < 112) {
            return "o_dft_row_sm";
        }
        if (height >= 168) {
            return "o_dft_row_lg";
        }
        return "";
    }

    onRowResizeStart(row, ev) {
        if (ev.button !== 0) {
            return;
        }
        ev.preventDefault();
        const strip =
            this.rootRef.el && this.rootRef.el.querySelector(`.o_dft_strip[data-row="${row}"]`);
        const startY = ev.clientY;
        const startHeight = strip ? strip.getBoundingClientRect().height : MIN_ROW_HEIGHT;
        this.state.resizingRow = row;

        const onMove = (moveEv) => {
            const height = Math.round(startHeight + moveEv.clientY - startY);
            this.state.rowHeights[row] = Math.max(
                MIN_ROW_HEIGHT,
                Math.min(MAX_ROW_HEIGHT, height)
            );
        };
        const onUp = () => {
            this.stopRowResize?.();
            this.state.resizingRow = 0;
            this.writeNumberSetting(`row-height-${row}`, this.state.rowHeights[row]);
        };
        this.stopRowResize = () => {
            document.removeEventListener("pointermove", onMove);
            document.removeEventListener("pointerup", onUp);
            document.body.classList.remove("o_dft_resizing_row");
            this.stopRowResize = null;
        };
        document.addEventListener("pointermove", onMove);
        document.addEventListener("pointerup", onUp);
        document.body.classList.add("o_dft_resizing_row");
    }

    /** Double-click the edge: back to the natural height for that row. */
    resetRowHeight(row) {
        this.state.rowHeights[row] = 0;
        this.writeNumberSetting(`row-height-${row}`, 0);
    }

    get hasCustomHeights() {
        return Object.values(this.state.rowHeights).some(Boolean);
    }

    resetAllRowHeights() {
        for (const row of Object.keys(this.state.rowHeights)) {
            this.resetRowHeight(row);
        }
    }

    // ------------------------------------------------------------------
    // Tile width
    // ------------------------------------------------------------------
    /**
     * Store the width a tile was dragged to. The definition is patched in place
     * first so the ribbon does not jump while the write is in flight; the shared
     * cache is then refreshed for every other view.
     */
    async onTileResized(def, width) {
        const previous = def.width || 0;
        def.width = width;
        this.state.defs = [...this.state.defs];
        try {
            const stored = await this.orm.call("filter.tile", "resize_tile", [def.id, width]);
            if (stored === false) {
                throw new Error("not writable");
            }
            def.width = stored;
            this.state.defs = [...this.state.defs];
            await this.tileService.reload();
        } catch (error) {
            def.width = previous;
            this.state.defs = [...this.state.defs];
            console.warn("Filter tiles: could not save the tile width", error);
            this.notification.add(_t("This tile's width could not be saved."), {
                type: "warning",
            });
        }
    }

    // ------------------------------------------------------------------
    // Context
    // ------------------------------------------------------------------
    get resModel() {
        return (this.props.list && this.props.list.resModel) || "";
    }

    /**
     * The action this view was opened from, when there is one.
     *
     * A model is often reached through several menus - customer invoices,
     * vendor bills and journal entries are all `account.move` - and a tile that
     * belongs to one of them has no business on the others. Tiles with no
     * action stay model-wide, which is the common case.
     */
    get actionId() {
        return (this.env.config && this.env.config.actionId) || 0;
    }

    get isVisible() {
        if (this.env.inDialog || !this.resModel) {
            return false;
        }
        if (this.state.defs.length) {
            return true;
        }
        // Nothing configured yet: only somebody who could actually build a tile
        // gets the (dismissable) nudge.
        return (this.state.canManage || this.state.canPersonalize) && !this.state.hintDismissed;
    }

    /**
     * Alt+n is only advertised for the tiles it can actually reach, and it
     * counts across rows: the ribbon is numbered the way it is read.
     */
    shortcutFor(def) {
        const index = this.orderedDefs.indexOf(def);
        return index >= 0 && index < MAX_SHORTCUTS ? index + 1 : 0;
    }

    settingKey(name) {
        return `dynamic_filter_tiles.${name}.${this.resModel}`;
    }

    readSetting(name, fallback) {
        try {
            const raw = browser.localStorage.getItem(this.settingKey(name));
            return raw === null ? fallback : raw === "1";
        } catch {
            return fallback;
        }
    }

    writeSetting(name, value) {
        try {
            browser.localStorage.setItem(this.settingKey(name), value ? "1" : "0");
        } catch {
            // Private browsing / storage disabled: the preference is simply not kept.
        }
    }

    readNumberSetting(name, fallback) {
        try {
            const raw = browser.localStorage.getItem(this.settingKey(name));
            const value = Number(raw);
            return raw === null || !isFinite(value) ? fallback : value;
        } catch {
            return fallback;
        }
    }

    writeNumberSetting(name, value) {
        try {
            browser.localStorage.setItem(this.settingKey(name), String(value));
        } catch {
            // See above: a lost preference is not worth an error.
        }
    }

    // ------------------------------------------------------------------
    // Auto refresh
    // ------------------------------------------------------------------
    clearRefreshTimer() {
        if (this.refreshTimer) {
            browser.clearInterval(this.refreshTimer);
            this.refreshTimer = null;
        }
    }

    armRefreshTimer() {
        this.clearRefreshTimer();
        const seconds = this.state.refreshEvery;
        if (!seconds) {
            return;
        }
        this.refreshTimer = browser.setInterval(() => this.computeValues(), seconds * 1000);
    }

    cycleRefresh() {
        const current = REFRESH_STEPS.indexOf(this.state.refreshEvery);
        this.state.refreshEvery = REFRESH_STEPS[(current + 1) % REFRESH_STEPS.length];
        this.writeNumberSetting("refresh", this.state.refreshEvery);
        this.armRefreshTimer();
    }

    get refreshLabel() {
        const seconds = this.state.refreshEvery;
        if (!seconds) {
            return _t("Auto refresh: off");
        }
        return seconds < 60
            ? _t("Auto refresh: %ss", seconds)
            : _t("Auto refresh: %s min", seconds / 60);
    }

    // ------------------------------------------------------------------
    // Domains
    // ------------------------------------------------------------------
    /**
     * Domain of the records currently shown, minus this bar's own facets - so
     * selecting a tile filters the view without collapsing its neighbours to 0.
     *
     * @param {boolean} includeGlobal keep the action's own domain (for values)
     *      or drop it (when capturing a tile from the current filter).
     */
    currentDomain(includeGlobal = true) {
        const searchModel = this.env.searchModel;
        if (!searchModel) {
            return (this.props.list && this.props.list.domain) || [];
        }
        try {
            const domains = includeGlobal ? [searchModel.globalDomain] : [];
            for (const group of searchModel._getGroups()) {
                const groupDomains = [];
                for (const activeItem of group.activeItems) {
                    const item = searchModel.searchItems[activeItem.searchItemId];
                    if (item && item.isFilterTile) {
                        continue; // our own facets
                    }
                    const domain = searchModel._getSearchItemDomain(activeItem);
                    if (domain) {
                        groupDomains.push(domain);
                    }
                }
                if (groupDomains.length) {
                    domains.push(Domain.or(groupDomains));
                }
            }
            return Domain.and(domains).toList(searchModel.domainEvalContext);
        } catch (error) {
            console.warn("Filter tiles: could not read the current domain", error);
            return (this.props.list && this.props.list.domain) || [];
        }
    }

    /**
     * The screen's own domain, without the filters the reader has applied.
     * A headline tile - "Finished", on a screen whose default filter is "to
     * do" - counts against this, or it reads 0 for ever. (client, 2026-09-10)
     */
    unfilteredDomain() {
        const searchModel = this.env.searchModel;
        if (!searchModel) {
            return this.currentDomain(true);
        }
        try {
            return new Domain(searchModel.globalDomain).toList(
                searchModel.domainEvalContext
            );
        } catch (error) {
            console.warn("Filter tiles: could not read the screen's own domain", error);
            return this.currentDomain(true);
        }
    }

    /** What the "+" tile pre-fills: the user's filters, not the action's. */
    get capturedDomain() {
        const domain = this.currentDomain(false);
        return domain.length ? new Domain(domain).toString() : "[]";
    }

    // ------------------------------------------------------------------
    // Data
    // ------------------------------------------------------------------
    /**
     * The tiles that belong on *this* view: the model's tiles, minus the ones
     * meant for another view type or pinned to another menu, in the order the
     * manager arranged them.
     */
    async loadDefinitions() {
        const registry = await this.tileService.getRegistry();
        const viewType = this.props.viewType;
        const actionId = this.actionId;
        this.state.defs = ((registry.tiles && registry.tiles[this.resModel]) || [])
            .filter(
                (def) =>
                    (def.view_types === "both" || def.view_types === viewType) &&
                    (!def.action_id || def.action_id === actionId)
            )
            .sort((a, b) => a.sequence - b.sequence || a.id - b.id);
        this.state.canManage = Boolean(registry.can_manage);
        this.state.canPersonalize = Boolean(registry.can_personalize);
        this.state.rowNames = (registry.rows && registry.rows[this.resModel]) || {};
    }

    async computeValues() {
        if (!this.state.defs.length) {
            this.state.values = {};
            this.state.loading = false;
            return;
        }
        this.state.loading = true;
        const payload = this.state.defs.map((def) => ({
            id: def.id,
            key: def.key,
            domain: def.domain,
            mine_field: def.mine_field,
            measure: def.measure,
            aggregate: def.aggregate,
            show_trend: def.show_trend,
            trend_source: def.trend_source,
            trend_field: def.trend_field,
            alert_operator: def.alert_operator,
            alert_value: def.alert_value,
            ignore_filters: def.ignore_filters,
        }));
        try {
            const result = await this.keepLast.add(
                this.orm.silent.call("filter.tile", "compute_tiles", [
                    this.resModel,
                    payload,
                    this.currentDomain(true),
                    6,
                    // What a headline tile counts against: this screen without
                    // the reader's own filters. (client, 2026-09-10)
                    this.unfilteredDomain(),
                ])
            );
            this.state.values = result.tiles || {};
            this.state.total = result.total || 0;
        } catch (error) {
            console.warn("Filter tiles: values unavailable", error);
        }
        this.state.loading = false;
    }

    /**
     * Can the browser evaluate this tile's filter at all?
     *
     * The server says so for tiles it has checked (`broken`); this asks the
     * browser itself, which is the only authority that matters, and caches
     * the answer per domain string.
     */
    tileIsBroken(def) {
        if (def.broken) {
            return true;
        }
        const text = def.domain || "[]";
        if (!(text in this.domainCheck)) {
            try {
                new Domain(text).toList(
                    (this.env.searchModel && this.env.searchModel.domainEvalContext) || {}
                );
                this.domainCheck[text] = false;
            } catch (error) {
                console.warn("Filter tiles: unusable domain", def.name, error);
                this.domainCheck[text] = true;
            }
        }
        return this.domainCheck[text];
    }

    dataFor(def) {
        // undefined, never null: `data` is an optional Object prop, and OWL's prop
        // validation rejects null with "Invalid props for component 'FilterTile':
        // 'data' is not a object" — which takes the whole ribbon down before the
        // counts have arrived. (regressed and re-fixed 2026-08-21)
        return this.state.values[def.key] || undefined;
    }

    // ------------------------------------------------------------------
    // Selection -> search facets
    // ------------------------------------------------------------------
    get activeKeys() {
        const searchModel = this.env.searchModel;
        if (!searchModel) {
            return [];
        }
        return searchModel.query
            .map((queryElem) => searchModel.searchItems[queryElem.searchItemId])
            .filter((item) => item && item.isFilterTile)
            .map((item) => item.tileKey);
    }

    isActive(def) {
        return this.activeKeys.includes(def.key);
    }

    clearTileFacets() {
        const searchModel = this.env.searchModel;
        if (!searchModel) {
            return;
        }
        const groupIds = new Set();
        for (const queryElem of searchModel.query) {
            const item = searchModel.searchItems[queryElem.searchItemId];
            if (item && item.isFilterTile) {
                groupIds.add(item.groupId);
            }
        }
        groupIds.forEach((groupId) => searchModel.deactivateGroup(groupId));
    }

    /**
     * Rebuild the facets from scratch, one prefilter per selected tile.
     *
     * How they combine is a property of the *call*, not of the domains: Odoo
     * OR-s the filters that share a group and AND-s the groups together. So one
     * call with every prefilter means "any of these", and one call per
     * prefilter means "all of these at once".
     */
    applySelection(keys) {
        const searchModel = this.env.searchModel;
        if (!searchModel) {
            return;
        }
        // A headline tile means "these records, whatever else is filtered", so
        // applying one clears the reader's other filters: otherwise the list it
        // opens is empty while the tile reads thousands. (client, 2026-09-10)
        const chosen = keys
            .map((key) => this.state.defs.find((def) => def.key === key))
            .filter(Boolean);
        if (chosen.some((def) => def.ignore_filters) && searchModel.clearQuery) {
            searchModel.clearQuery();
        }
        this.clearTileFacets();
        const prefilters = keys
            .map((key) => this.state.defs.find((def) => def.key === key))
            .filter(Boolean)
            .filter((def) => !this.tileIsBroken(def))
            .map((def) => ({
                description: def.name,
                domain: tileDomain(def),
                // Keep it out of the Filters dropdown; it stays a removable facet.
                invisible: "True",
                isFilterTile: true,
                tileKey: def.key,
            }));
        if (!prefilters.length) {
            return;
        }
        if (this.state.combineAll && prefilters.length > 1) {
            prefilters.forEach((prefilter) => searchModel.createNewFilters([prefilter]));
        } else {
            searchModel.createNewFilters(prefilters);
        }
    }

    /**
     * Plain click = only this tile (toggle). Ctrl/Cmd click - or multi-select
     * mode, which is the same thing without a keyboard - adds to the selection.
     */
    onTileSelected(def, ev) {
        // A tile whose domain the browser cannot evaluate must not be applied:
        // the failure happens inside the search model's own rendering, where
        // nothing catches it, and the whole view dies until somebody removes
        // the facet - which they cannot do, because the screen is gone. Say so
        // and do nothing. (client, 2026-09-10)
        if (this.tileIsBroken(def)) {
            this.notification.add(
                _t('"%s" cannot be used: its filter contains something the browser '
                   + "cannot work out. Edit the tile and simplify its filter.", def.name),
                { type: "warning" }
            );
            return;
        }
        const selection = new Set(this.activeKeys);
        const additive = this.state.multiSelect || (ev && (ev.ctrlKey || ev.metaKey));
        if (additive) {
            selection.has(def.key) ? selection.delete(def.key) : selection.add(def.key);
        } else if (selection.has(def.key) && selection.size === 1) {
            selection.clear();
        } else {
            selection.clear();
            selection.add(def.key);
        }
        this.applySelection([...selection]);
    }

    toggleMultiSelect() {
        this.state.multiSelect = !this.state.multiSelect;
        this.writeSetting("multi-select", this.state.multiSelect);
    }

    /** Switch between "any of the selected tiles" and "all of them at once". */
    toggleCombineMode() {
        this.state.combineAll = !this.state.combineAll;
        this.writeSetting("combine-all", this.state.combineAll);
        // Re-apply what is already selected, so the change is visible at once.
        const keys = this.activeKeys;
        if (keys.length > 1) {
            this.applySelection(keys);
        }
    }

    get combineLabel() {
        return this.state.combineAll
            ? _t("Selected tiles: match all")
            : _t("Selected tiles: match any");
    }

    // ------------------------------------------------------------------
    // Studio
    // ------------------------------------------------------------------
    toggleCollapsed() {
        this.state.collapsed = !this.state.collapsed;
        this.writeSetting("collapsed", this.state.collapsed);
    }

    toggleCompact() {
        this.state.compact = !this.state.compact;
        this.writeSetting("compact", this.state.compact);
    }

    dismissHint() {
        this.state.hintDismissed = true;
        this.writeSetting("hint-dismissed", true);
    }

    /**
     * Open the tile editor; a new tile inherits the filter on screen.
     *
     * `personal` builds a private tile - the one every user is allowed to make,
     * visible to nobody else. Managers get the choice; everybody else always
     * lands here, and the server stamps their tile as personal anyway.
     */
    async openEditor(tileId = false, personal = false, row = 1) {
        let context = {};
        if (!tileId) {
            context = await this.orm.call("filter.tile", "prepare_tile_defaults", [
                this.resModel,
                this.capturedDomain,
                personal,
                row,
                this.actionId,
            ]);
        }
        this.dialog.add(FormViewDialog, {
            resModel: "filter.tile",
            resId: tileId || false,
            context,
            title: tileId
                ? _t("Edit filter tile")
                : personal
                ? _t("New private tile")
                : _t("New filter tile"),
            size: "lg",
            onRecordSaved: () => this.tileService.reload(),
        });
    }

    /**
     * Remove a tile from the ribbon, for good.
     *
     * Always behind a confirmation, and the wording says who is affected: a
     * private tile is yours to drop, a shared one disappears from everybody's
     * view of this model. A tile that is currently applied is unselected first,
     * so the search bar is not left holding a facet that no longer exists.
     */
    deleteTile(def) {
        const shared = !def.personal;
        this.dialog.add(ConfirmationDialog, {
            title: _t("Remove tile"),
            body: shared
                ? _t(
                      '"%s" is a shared tile: removing it takes it off this view for everybody. ' +
                          "This cannot be undone - archive it instead if you may want it back.",
                      def.name
                  )
                : _t('Remove your private tile "%s"? This cannot be undone.', def.name),
            confirmLabel: _t("Remove"),
            confirmClass: "btn-danger",
            confirm: async () => {
                if (this.isActive(def)) {
                    this.applySelection(this.activeKeys.filter((key) => key !== def.key));
                }
                try {
                    await this.orm.unlink("filter.tile", [def.id]);
                } catch (error) {
                    console.warn("Filter tiles: could not remove the tile", error);
                    this.notification.add(_t("This tile could not be removed."), {
                        type: "danger",
                    });
                    return;
                }
                await this.tileService.reload();
                this.notification.add(_t('"%s" was removed.', def.name), { type: "success" });
            },
            cancel: () => {},
        });
    }

    // ------------------------------------------------------------------
    // Breakdown
    // ------------------------------------------------------------------
    /** Split a tile open on the spot: what is in there, and can I filter it? */
    openBreakdown(def, target) {
        this.breakdownPopover.open(target, {
            def,
            baseDomain: this.currentDomain(true),
            onPick: (row) => this.applyBreakdownRow(def, row),
        });
    }

    /**
     * A breakdown row is a filter too: selecting one applies the tile *and* the
     * slice, as a single facet the user can drop like any other.
     */
    applyBreakdownRow(def, row) {
        const searchModel = this.env.searchModel;
        if (!searchModel) {
            return;
        }
        this.clearTileFacets();
        const domain = Domain.and([new Domain(tileDomain(def)), new Domain(row.domain)]);
        searchModel.createNewFilters([
            {
                description: `${def.name} · ${row.label}`,
                domain: domain.toString(),
                invisible: "True",
                isFilterTile: true,
                tileKey: def.key,
            },
        ]);
    }

    // ------------------------------------------------------------------
    // Portability
    // ------------------------------------------------------------------
    /** Download this model's ribbon as JSON - a tile set is worth moving. */
    async exportTiles() {
        const payload = await this.orm.call("filter.tile", "export_tiles", [this.resModel]);
        const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = `filter-tiles-${this.resModel.replace(/\./g, "-")}.json`;
        link.click();
        URL.revokeObjectURL(url);
        this.notification.add(
            _t("%s tiles exported.", (payload.tiles || []).length),
            { type: "success" }
        );
    }

    openImport() {
        this.dialog.add(TileImportDialog, {
            resModel: this.resModel,
            onImported: async (result) => {
                await this.tileService.reload();
                const created = (result.created || []).length;
                const skipped = (result.skipped || []).length;
                this.notification.add(
                    skipped
                        ? _t("%(created)s tiles imported, %(skipped)s skipped.", { created, skipped })
                        : _t("%s tiles imported.", created),
                    { type: skipped ? "warning" : "success" }
                );
            },
        });
    }

    async openGenerator() {
        const fields = await this.orm.call("filter.tile", "get_status_fields", [this.resModel]);
        if (!fields.length) {
            this.notification.add(
                _t("This model has no status-like field to build a tile set from. Add a tile by hand instead."),
                { type: "warning" }
            );
            return;
        }
        this.dialog.add(TileGeneratorDialog, {
            resModel: this.resModel,
            fields,
            hasTiles: this.state.defs.length > 0,
            actionName: (this.env.config && this.env.config.actionName) || "",
            rowCount: this.rows.length,
            maxRows: MAX_ROWS,
            onGenerate: async (fieldName, replace, thisActionOnly, row) => {
                const ids = await this.orm.call("filter.tile", "autogenerate_tiles", [
                    this.resModel,
                    fieldName,
                    replace,
                    thisActionOnly ? this.actionId : false,
                    row,
                ]);
                await this.tileService.reload();
                this.notification.add(_t("%s tiles created.", ids.length), { type: "success" });
            },
        });
    }

    openTileList() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: _t("Filter Tiles"),
            res_model: "filter.tile",
            views: [
                [false, "list"],
                [false, "form"],
            ],
            domain: [["model_name", "=", this.resModel]],
            context: { search_default_group_by_model: 1 },
        });
    }

    /**
     * Persist a drag-and-drop for everybody - within a row or across rows.
     *
     * The sortable applies the move to the DOM first, so the new arrangement is
     * simply read back from it: no guessing from the drop's neighbours, and no
     * second source of truth to keep in step.
     */
    async onTileDropped() {
        const root = this.rootRef.el;
        if (!root) {
            return;
        }
        // The sortable's placeholder is a *shallow clone* of the dragged tile,
        // so it carries the same `data-tile-id`. Left in, it would count twice
        // and shift every sequence after it by one. It sits right next to the
        // real element once the drop is applied, so dropping duplicates keeps
        // the intended position either way.
        const seen = new Set();
        const layout = [...root.querySelectorAll(".o_dft_strip[data-row]")].map((strip) => ({
            row: Number(strip.dataset.row),
            tiles: [...strip.querySelectorAll(".o_dft_tile[data-tile-id]")]
                .filter((tile) => !tile.classList.contains("o_dft_tile_placeholder"))
                .map((tile) => Number(tile.dataset.tileId))
                .filter((id) => {
                    if (!id || seen.has(id)) {
                        return false;
                    }
                    seen.add(id);
                    return true;
                }),
        }));

        // Reflect it locally straight away, so the ribbon does not flicker back
        // to the old order while the write is in flight.
        const byId = new Map(this.state.defs.map((def) => [def.id, def]));
        const reordered = [];
        for (const { row, tiles } of layout) {
            for (const id of tiles) {
                const def = byId.get(id);
                if (def) {
                    def.row = row;
                    reordered.push(def);
                }
            }
        }
        this.state.defs = reordered;

        await this.orm.call("filter.tile", "arrange_tiles", [layout]);
        await this.tileService.reload();
    }
}
