/** @odoo-module **/

import { onWillRender, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { Domain } from "@web/core/domain";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { SearchPanel } from "@web/search/search_panel/search_panel";
import { FormViewDialog } from "@web/views/view_dialogs/form_view_dialog";
import { PanelTilesDialog } from "./panel_tiles_dialog";

// Keep in sync with MAX_PANEL_TILES in models/filter_tile.py.
export const MAX_PANEL_TILES = 12;
// Below this many values the "find in panel" box is noise, not help.
const FIND_THRESHOLD = 8;
// Saved selections are a shortlist, not an archive.
const MAX_PRESETS = 12;

const DEFAULT_OPTIONS = {
    enhanced: true, // off = Odoo's own panel, untouched
    sort: "default", // "default" | "count" | "name"
    hideEmpty: false,
    bars: true,
    compact: false,
};

/** Case- and accent-insensitive form of a label, for matching. */
export function foldText(text) {
    return String(text ?? "")
        .normalize("NFD")
        .replace(/[̀-ͯ]/g, "")
        .toLowerCase();
}

/**
 * Split `text` into the parts that match `query` and the parts that do not,
 * accent- and case-insensitively, so the template can highlight the matches
 * without ever handing raw HTML to the DOM.
 *
 * @returns {{ text: string, hit: boolean }[]}
 */
export function splitMatches(text, query) {
    const original = String(text ?? "");
    const needle = foldText(query).trim();
    if (!needle) {
        return [{ text: original, hit: false }];
    }
    // Folded string, plus where each folded character came from: folding can
    // change the length ("é" is two code points once decomposed), so matches
    // are found in the folded text and cut out of the original one.
    let folded = "";
    const origin = [];
    let position = 0;
    for (const char of original) {
        for (const foldedChar of foldText(char)) {
            folded += foldedChar;
            origin.push(position);
        }
        position += char.length;
    }
    origin.push(position);

    const parts = [];
    let cursor = 0;
    let from = 0;
    let index = folded.indexOf(needle, from);
    while (index !== -1) {
        const start = origin[index];
        const end = origin[index + needle.length];
        if (start > cursor) {
            parts.push({ text: original.slice(cursor, start), hit: false });
        }
        parts.push({ text: original.slice(start, end), hit: true });
        cursor = end;
        from = index + needle.length;
        index = folded.indexOf(needle, from);
    }
    if (cursor < original.length) {
        parts.push({ text: original.slice(cursor), hit: false });
    }
    return parts.length ? parts : [{ text: original, hit: false }];
}

/**
 * The smart search panel.
 *
 * Odoo's search panel (the column of categories and filters on the left of
 * Contacts, Employees, Products, ...) is patched here once, for every view of
 * every app. Everything added is presentation or a shortcut to something the
 * panel could already do - the search model, its domains and its counters are
 * untouched - and "Classic look" in the panel menu switches it all off.
 *
 *  - find in panel: one box filtering every section at once, accent- and
 *    case-insensitive, with the match highlighted and trees opened on it,
 *  - collapsible sections, a count of what is selected in each and a clear
 *    button per section,
 *  - the current selection as removable chips at the top,
 *  - count bars: each value's share of its section, drawn behind the value,
 *  - sort by most records or by name, hide the empty values, compact density,
 *  - saved selections: name a combination of panel values, get it back later,
 *  - the bridge to filter tiles: pin a value as a tile, or turn a whole
 *    section into a tile set, in one click.
 *
 * Preferences are kept per model in the browser, like the tile bar's own.
 */
patch(SearchPanel.prototype, {
    setup() {
        super.setup(...arguments);
        // Instance properties, not `static components`: subclasses of the
        // panel copy `static components` when they are defined, and would not
        // see anything added to the base afterwards.
        this.DftDropdown = Dropdown;
        this.DftDropdownItem = DropdownItem;

        this.dft = useState({
            query: "",
            options: this.dftReadOptions(),
            collapsed: this.dftReadJSON(this.dftKey("collapsed"), {}),
            presets: this.dftReadJSON(this.dftPresetKey(), []),
            // Starred values, by section: { "filter:category_ids": [3, 7] }.
            stars: this.dftReadJSON(this.dftKey("stars"), {}),
            presetName: "",
        });
        // Anything derived from the sections is computed at most once per
        // render: the templates ask for the same answers once per value.
        this.dftMemo = new Map();
        onWillRender(() => {
            this.dftMemo = new Map();
        });
    },

    // ------------------------------------------------------------------
    // Preferences
    // ------------------------------------------------------------------
    get dftResModel() {
        return (this.env.searchModel && this.env.searchModel.resModel) || "";
    },

    dftKey(name) {
        return `ebshel_dynamic_filter.sp.${name}.${this.dftResModel}`;
    },

    /** Saved selections belong to the menu they were made in, not the model. */
    dftPresetKey() {
        const actionId = (this.env.config && this.env.config.actionId) || 0;
        return `${this.dftKey("presets")}.${actionId}`;
    },

    dftReadJSON(key, fallback) {
        try {
            const raw = browser.localStorage.getItem(key);
            if (raw === null) {
                return fallback;
            }
            const value = JSON.parse(raw);
            return value && typeof value === "object" ? value : fallback;
        } catch {
            return fallback;
        }
    },

    dftWriteJSON(key, value) {
        try {
            browser.localStorage.setItem(key, JSON.stringify(value));
        } catch {
            // Private browsing / storage disabled: the preference is simply not kept.
        }
    },

    dftReadOptions() {
        const stored = this.dftReadJSON(this.dftKey("options"), {});
        const options = { ...DEFAULT_OPTIONS };
        for (const key of Object.keys(DEFAULT_OPTIONS)) {
            if (typeof stored[key] === typeof DEFAULT_OPTIONS[key]) {
                options[key] = stored[key];
            }
        }
        return options;
    },

    dftSetOption(name, value) {
        this.dft.options[name] = value;
        this.dftWriteJSON(this.dftKey("options"), this.dft.options);
        if (name === "enhanced" && !value) {
            this.dft.query = "";
        }
    },

    get dftOn() {
        return this.dft.options.enhanced;
    },

    get dftQuery() {
        return this.dftOn ? foldText(this.dft.query).trim() : "";
    },

    get dftSortChoices() {
        return [
            { key: "default", label: _t("Default order") },
            { key: "count", label: _t("Most records first") },
            { key: "name", label: _t("Alphabetical") },
        ];
    },

    // ------------------------------------------------------------------
    // Sections
    // ------------------------------------------------------------------
    dftSectionKey(section) {
        return `${section.type}:${section.fieldName}`;
    },

    dftMemoize(key, compute) {
        if (!this.dftMemo.has(key)) {
            this.dftMemo.set(key, compute());
        }
        return this.dftMemo.get(key);
    },

    /** The sections to draw: while searching, only those with a match. */
    dftVisibleSections() {
        const sections = this.sections;
        if (!this.dftQuery) {
            return sections;
        }
        return sections.filter((section) => !section.errorMsg && this.dftMatchCount(section) > 0);
    },

    get dftShowFind() {
        return this.dftMemoize("showFind", () => {
            let total = 0;
            for (const section of this.sections) {
                total += section.values ? section.values.size : 0;
            }
            return total >= FIND_THRESHOLD || Boolean(this.dft.query);
        });
    },

    dftIsCollapsed(section) {
        return this.dftOn && !this.dftQuery && Boolean(this.dft.collapsed[this.dftSectionKey(section)]);
    },

    dftToggleSection(section) {
        if (!this.dftOn || this.dftQuery) {
            return;
        }
        const key = this.dftSectionKey(section);
        if (this.dft.collapsed[key]) {
            delete this.dft.collapsed[key];
        } else {
            this.dft.collapsed[key] = true;
        }
        this.dftWriteJSON(this.dftKey("collapsed"), this.dft.collapsed);
    },

    dftSetAllCollapsed(collapsed) {
        const next = {};
        if (collapsed) {
            for (const section of this.sections) {
                next[this.dftSectionKey(section)] = true;
            }
        }
        this.dft.collapsed = next;
        this.dftWriteJSON(this.dftKey("collapsed"), next);
    },

    /** How many values are selected in a section (a category has 0 or 1). */
    dftSelectedCount(section) {
        const active = this.state.active[section.id];
        if (section.type === "category") {
            return active ? 1 : 0;
        }
        return active ? Object.values(active).filter(Boolean).length : 0;
    },

    dftClearSection(section) {
        this.clearSelection(section.id);
    },

    /** Values that answer the query, for the section header while searching. */
    dftMatchCount(section) {
        return this.dftMemoize(`matches:${section.id}`, () => {
            const query = this.dftQuery;
            if (!query || !section.values) {
                return 0;
            }
            let count = 0;
            for (const [id, value] of section.values) {
                if (id !== false && foldText(value.display_name).includes(query)) {
                    count++;
                }
            }
            return count;
        });
    },

    // ------------------------------------------------------------------
    // Values: which, in what order
    // ------------------------------------------------------------------
    dftHidesEmpty(section) {
        return this.dftOn && this.dft.options.hideEmpty && section.enableCounters;
    },

    /**
     * Category values that survive the query and "hide empty", ancestors
     * included - a match three levels deep is shown with the path to it.
     * `null` when nothing is filtered out.
     *
     * @returns {Set|null}
     */
    dftCategoryVisible(section) {
        return this.dftMemoize(`catVisible:${section.id}`, () => {
            const query = this.dftQuery;
            const hideEmpty = this.dftHidesEmpty(section);
            if (!query && !hideEmpty) {
                return null;
            }
            const visible = new Set();
            for (const [id, value] of section.values) {
                if (id === false) {
                    continue;
                }
                const matches = !query || foldText(value.display_name).includes(query);
                const kept = !hideEmpty || value.__count > 0 || id === section.activeValueId;
                if (!matches || !kept) {
                    continue;
                }
                let current = value;
                while (current && !visible.has(current.id)) {
                    visible.add(current.id);
                    current = current.parentId ? section.values.get(current.parentId) : null;
                }
            }
            return visible;
        });
    },

    dftSortIds(section, ids) {
        const sort = this.dftOn ? this.dft.options.sort : "default";
        const starred = this.dftOn ? this.dftStarredIds(section) : [];
        if (sort === "default" && !starred.length) {
            return ids;
        }
        const valueOf = (id) => section.values.get(id) || {};
        const sorted = ids.filter((id) => id !== false);
        if (sort === "default") {
            // nothing to reorder but the stars, below
        } else if (sort === "count") {
            sorted.sort((a, b) => (valueOf(b).__count || 0) - (valueOf(a).__count || 0));
        } else {
            sorted.sort((a, b) =>
                String(valueOf(a).display_name ?? "").localeCompare(
                    String(valueOf(b).display_name ?? ""),
                    undefined,
                    { sensitivity: "base", numeric: true }
                )
            );
        }
        // Starred values lead, in the order the sort gave them.
        const ordered = starred.length
            ? [...sorted.filter((id) => starred.includes(id)), ...sorted.filter((id) => !starred.includes(id))]
            : sorted;
        // "All" stays on top: it is a reset, not a value.
        return ids.includes(false) ? [false, ...ordered] : ordered;
    },

    // ------------------------------------------------------------------
    // Starred values: the handful you use every day, first in their section
    // ------------------------------------------------------------------
    dftStarredIds(section) {
        return this.dft.stars[this.dftSectionKey(section)] || [];
    },

    dftIsStarred(section, valueId) {
        return this.dftStarredIds(section).includes(valueId);
    },

    dftToggleStar(section, value) {
        const key = this.dftSectionKey(section);
        const current = this.dft.stars[key] || [];
        const next = current.includes(value.id)
            ? current.filter((id) => id !== value.id)
            : [...current, value.id];
        if (next.length) {
            this.dft.stars[key] = next;
        } else {
            delete this.dft.stars[key];
        }
        this.dftWriteJSON(this.dftKey("stars"), this.dft.stars);
    },

    // ------------------------------------------------------------------
    // Filter sections: select every value, or turn the selection around
    // ------------------------------------------------------------------
    /** The values the section is showing right now - a search narrows them. */
    dftShownFilterIds(section) {
        return this.dftFilterValueIds(section, section.values);
    },

    dftSelectAll(section) {
        const ids = this.dftShownFilterIds(section);
        for (const id of ids) {
            this.state.active[section.id][id] = true;
        }
        this.env.searchModel.toggleFilterValues(section.id, ids, true);
    },

    /** Everything shown that was off comes on, and the other way round. */
    dftInvert(section) {
        const ids = this.dftShownFilterIds(section);
        for (const id of ids) {
            this.state.active[section.id][id] = !this.state.active[section.id][id];
        }
        this.env.searchModel.toggleFilterValues(section.id, ids);
    },

    /** The ids a category level draws, filtered and sorted. */
    dftCategoryIds(section, ids) {
        if (!this.dftOn) {
            return ids;
        }
        const visible = this.dftCategoryVisible(section);
        let list = ids;
        if (visible) {
            // While searching, "All" would only get in the way of the results.
            list = ids.filter((id) => (id === false ? !this.dftQuery : visible.has(id)));
        }
        return this.dftSortIds(section, list);
    },

    /** While searching, every branch that holds a match is opened. */
    dftForceOpen() {
        return Boolean(this.dftQuery);
    },

    /** The ids a filter section (or one of its groups) draws. */
    dftFilterValueIds(section, values) {
        const ids = [...values.keys()];
        if (!this.dftOn) {
            return ids;
        }
        const query = this.dftQuery;
        const hideEmpty = this.dftHidesEmpty(section);
        let list = ids;
        if (query || hideEmpty) {
            list = ids.filter((id) => {
                const value = values.get(id);
                if (query && !foldText(value.display_name).includes(query)) {
                    return false;
                }
                return !hideEmpty || value.__count > 0 || Boolean(value.checked);
            });
        }
        return this.dftSortIds(section, list);
    },

    /** Groups of a grouped filter section that still have something to show. */
    dftGroupIds(section) {
        const groupIds = section.sortedGroupIds || [];
        if (!this.dftOn || (!this.dftQuery && !this.dftHidesEmpty(section))) {
            return groupIds;
        }
        return groupIds.filter(
            (groupId) => this.dftFilterValueIds(section, section.groups.get(groupId).values).length
        );
    },

    dftParts(text) {
        return splitMatches(text, this.dftOn ? this.dft.query : "");
    },

    // ------------------------------------------------------------------
    // Count bars
    // ------------------------------------------------------------------
    /**
     * A value's share of the largest one beside it, as a CSS variable the
     * stylesheet turns into a bar behind the row. Siblings only: in a tree the
     * parents' counters include their children's, and would flatten every bar
     * below them.
     */
    dftBarStyle(section, value) {
        if (!this.dftOn || !this.dft.options.bars || !section.enableCounters || value.id === false) {
            return "";
        }
        const max = this.dftMemoize(`max:${section.id}:${value.parentId || 0}`, () => {
            let highest = 0;
            for (const [id, sibling] of section.values) {
                if (id !== false && (sibling.parentId || 0) === (value.parentId || 0)) {
                    highest = Math.max(highest, sibling.__count || 0);
                }
            }
            return highest;
        });
        const share = max ? Math.round(((value.__count || 0) * 100) / max) : 0;
        const color = section.color ? `--dft-sp-color: ${section.color};` : "";
        return `--dft-sp-share: ${share}%; ${color}`;
    },

    // ------------------------------------------------------------------
    // Current selection, as chips
    // ------------------------------------------------------------------
    get dftChips() {
        return this.dftMemoize("chips", () => {
            const chips = [];
            for (const section of this.sections) {
                if (section.type === "category") {
                    if (!section.activeValueId || !section.values.has(section.activeValueId)) {
                        continue;
                    }
                    const path = [
                        ...this.getAncestorValueIds(section, section.activeValueId),
                        section.activeValueId,
                    ].map((id) => section.values.get(id).display_name);
                    chips.push({
                        key: `c${section.id}`,
                        label: path.join(" › "),
                        section,
                        valueId: section.activeValueId,
                    });
                } else if (section.values) {
                    for (const [id, value] of section.values) {
                        if (value.checked) {
                            chips.push({
                                key: `f${section.id}_${id}`,
                                label: value.display_name,
                                section,
                                valueId: id,
                            });
                        }
                    }
                }
            }
            return chips;
        });
    },

    dftRemoveChip(chip) {
        const { section, valueId } = chip;
        if (section.type === "category") {
            this.clearSelection(section.id);
            return;
        }
        if (this.state.active[section.id]) {
            this.state.active[section.id][valueId] = false;
        }
        this.env.searchModel.toggleFilterValues(section.id, [valueId], false);
    },

    // ------------------------------------------------------------------
    // Saved selections
    // ------------------------------------------------------------------
    /** The panel's current selection, keyed by field so it outlives a reload. */
    dftCaptureSelection() {
        const categories = {};
        const filters = {};
        for (const section of this.sections) {
            const key = this.dftSectionKey(section);
            if (section.type === "category") {
                if (section.activeValueId) {
                    categories[key] = section.activeValueId;
                }
            } else if (section.values) {
                const ids = [...section.values.values()].filter((v) => v.checked).map((v) => v.id);
                if (ids.length) {
                    filters[key] = ids;
                }
            }
        }
        return { categories, filters };
    },

    dftSavePreset() {
        const name = (this.dft.presetName || "").trim();
        const selection = this.dftCaptureSelection();
        if (!name || !this.dftChips.length) {
            return;
        }
        const presets = this.dft.presets.filter((preset) => preset.name !== name);
        presets.unshift({ name, ...selection });
        this.dft.presets = presets.slice(0, MAX_PRESETS);
        this.dft.presetName = "";
        this.dftWriteJSON(this.dftPresetKey(), this.dft.presets);
        this.env.services.notification?.add(_t('Selection "%s" saved.', name), { type: "success" });
    },

    dftDeletePreset(preset) {
        this.dft.presets = this.dft.presets.filter((item) => item !== preset);
        this.dftWriteJSON(this.dftPresetKey(), this.dft.presets);
    },

    /**
     * Put a saved selection back. Values that no longer exist are skipped
     * quietly; the search model is notified once, for the whole selection.
     */
    dftApplyPreset(preset) {
        const searchModel = this.env.searchModel;
        for (const section of this.sections) {
            const real = searchModel.sections.get(section.id);
            if (!real) {
                continue;
            }
            const key = this.dftSectionKey(section);
            if (real.type === "category") {
                const wanted = preset.categories && preset.categories[key];
                real.activeValueId = wanted && real.values.has(wanted) ? wanted : false;
            } else if (real.values) {
                const wanted = new Set((preset.filters && preset.filters[key]) || []);
                for (const [id, value] of real.values) {
                    value.checked = wanted.has(id);
                }
            }
        }
        // Clearing no section at all is the public way to say "re-read the
        // panel": it notifies the search model, which reloads the records.
        searchModel.clearSections([]);
    },

    dftPresetSize(preset) {
        let count = Object.keys(preset.categories || {}).length;
        for (const ids of Object.values(preset.filters || {})) {
            count += ids.length;
        }
        return count;
    },

    onDftPresetKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.dftSavePreset();
        }
    },

    dftClearQuery() {
        this.dft.query = "";
    },

    onDftFindKeydown(ev) {
        // The field is cleared by hand as well as through the state: `t-model`
        // does not write an emptied value back into an input that still has
        // the focus, so the box would keep showing what was typed.
        if (ev.key === "Escape" && this.dft.query) {
            ev.stopPropagation();
            this.dft.query = "";
            ev.target.value = "";
        } else if (ev.key === "Enter" && this.dftQuery) {
            ev.preventDefault();
            if (this.dftApplyFirstMatch()) {
                ev.target.value = "";
            }
        }
    },

    /**
     * Enter in the find box: apply the first value that matches, as a click
     * on it would - select the category, or tick the filter - and clear the
     * search so the result is in view. Type, Enter, done.
     */
    dftApplyFirstMatch() {
        const query = this.dftQuery;
        for (const section of this.dftVisibleSections()) {
            if (section.type === "category") {
                const visible = this.dftCategoryVisible(section);
                for (const [id, value] of section.values) {
                    if (id !== false && (!visible || visible.has(id))
                        && foldText(value.display_name).includes(query)) {
                        this.dft.query = "";
                        this.toggleCategory(section, value);
                        return true;
                    }
                }
            } else if (section.values) {
                const [id] = this.dftShownFilterIds(section);
                if (id !== undefined) {
                    this.dft.query = "";
                    if (!this.state.active[section.id][id]) {
                        this.state.active[section.id][id] = true;
                        this.env.searchModel.toggleFilterValues(section.id, [id], true);
                    }
                    return true;
                }
            }
        }
        return false;
    },

    // ------------------------------------------------------------------
    // Bridge to filter tiles
    // ------------------------------------------------------------------
    get dftTilesAvailable() {
        return this.dftOn && !this.env.isSmall && Boolean(this.env.services.filter_tiles);
    },

    /** The same condition the panel itself applies for this value. */
    dftValueCondition(section, value) {
        if (section.type === "category") {
            const field = this.env.searchModel.searchViewFields[section.fieldName];
            const operator =
                field && field.type === "many2one" && section.parentField ? "child_of" : "=";
            return { operator, domain: [[section.fieldName, operator, value.id]] };
        }
        return { operator: "in", domain: [[section.fieldName, "in", [value.id]]] };
    },

    dftTileIcon(section) {
        const icon = (section.icon || "").trim();
        return icon.startsWith("fa-") ? icon : "fa-filter";
    },

    /** Pin one value as a tile: the editor opens, already filled in. */
    async dftPinValue(section, value) {
        const { orm, dialog } = this.env.services;
        const tiles = this.env.services.filter_tiles;
        const domain = new Domain(this.dftValueCondition(section, value).domain).toString();
        const context = await orm.call("filter.tile", "prepare_tile_defaults", [
            this.dftResModel,
            domain,
            false,
            1,
            (this.env.config && this.env.config.actionId) || false,
        ]);
        Object.assign(context, {
            default_name: String(value.display_name ?? ""),
            default_icon: this.dftTileIcon(section),
            default_tooltip: `${section.description}: ${value.display_name}`,
        });
        dialog.add(FormViewDialog, {
            resModel: "filter.tile",
            context,
            title: _t("Pin as a filter tile"),
            size: "lg",
            onRecordSaved: () => tiles.reload(),
        });
    },

    /** One tile per value of the section, picked in a small dialog. */
    async dftSectionToTiles(section) {
        const { orm, dialog, notification } = this.env.services;
        const tiles = this.env.services.filter_tiles;
        const registry = await tiles.getRegistry();
        let values = [...section.values.values()].filter((value) => value.id !== false);
        if (section.enableCounters) {
            values = values
                .filter((value) => value.__count > 0)
                .sort((a, b) => (b.__count || 0) - (a.__count || 0));
        }
        if (!values.length) {
            notification.add(_t("This section has no value to turn into a tile."), {
                type: "warning",
            });
            return;
        }
        const operator = this.dftValueCondition(section, values[0]).operator;
        dialog.add(PanelTilesDialog, {
            sectionName: section.description || section.fieldName,
            values: values.map((value) => ({
                id: value.id,
                label: String(value.display_name ?? ""),
                count: section.enableCounters ? value.__count || 0 : null,
            })),
            maxTiles: MAX_PANEL_TILES,
            canManage: Boolean(registry.can_manage),
            actionName: (this.env.config && this.env.config.actionName) || "",
            onConfirm: async (picked, personal, thisActionOnly) => {
                const ids = await orm.call("filter.tile", "create_tiles_from_panel", [
                    this.dftResModel,
                    section.fieldName,
                    operator,
                    picked.map((value) => ({ id: value.id, label: value.label })),
                ], {
                    action_id: thisActionOnly ? this.env.config.actionId : false,
                    personal,
                    icon: this.dftTileIcon(section),
                    section_name: section.description || "",
                });
                await tiles.reload();
                notification.add(_t("%s tiles created.", ids.length), { type: "success" });
            },
        });
    },

    // ------------------------------------------------------------------
    // Group the view by a section's field
    // ------------------------------------------------------------------
    dftCanGroupBy(section) {
        const searchModel = this.env.searchModel;
        if (!this.dftOn || !searchModel.searchMenuTypes || !searchModel.searchMenuTypes.has("groupBy")) {
            return false;
        }
        if (!searchModel.searchViewFields[section.fieldName]) {
            return false;
        }
        return !searchModel.groupBy.some((spec) => spec.split(":")[0] === section.fieldName);
    },

    dftGroupBy(section) {
        this.env.searchModel.createNewGroupBy(section.fieldName);
    },
});
