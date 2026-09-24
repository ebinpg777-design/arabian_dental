/** @odoo-module **/

import { Component, onMounted, onWillStart, onWillUnmount, useExternalListener, useRef, useState } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { useHotkey } from "@web/core/hotkeys/hotkey_hook";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { useDebounced } from "@web/core/utils/timing";
import { useSortable } from "@web/core/utils/sortable_owl";
import { formatFloat } from "@web/core/utils/numbers";
import { _t } from "@web/core/l10n/translation";

import { resolveCardColor } from "../core/board_colors";
import { IconPanel } from "../fields/icon_panel";
import { BoardCard, canvasToPng } from "./board_card";
import { markup } from "@odoo/owl";
import { escape } from "@web/core/utils/strings";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { QuickFindDialog } from "./quick_find_dialog";

const GRID_GAP = 12; // must match the stylesheet
const MIN_HEIGHT = 120;
const MAX_HEIGHT = 720;
const SIDEBAR_KEY = "ebshel_dashboard.sidebar";

/* The periods as a reader thinks of them: by the unit they span. Keys the
   server does not send are skipped, anything unexpected lands in "Other". */
const PERIOD_GROUPS = [
    { key: "days", label: _t("Days"), keys: ["today", "yesterday", "tomorrow"] },
    { key: "weeks", label: _t("Weeks"), keys: ["this_week", "last_week", "last_7", "next_7"] },
    { key: "months", label: _t("Months"), keys: ["mtd", "this_month", "last_month", "last_30", "next_30"] },
    { key: "quarters", label: _t("Quarters"), keys: ["qtd", "this_quarter", "last_quarter", "last_90", "next_90"] },
    { key: "years", label: _t("Years"), keys: ["ytd", "this_year", "last_year", "last_12_months", "last_365"] },
];

/** `YYYY-MM-DD` of a date, in the browser's own calendar. */
function isoDay(date) {
    return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}
const TOUR_KEY = "ebshel_dashboard.toured";
const SKELETONS = [3, 3, 3, 3, 8, 4, 6, 6];
// Cards per round trip once the layout is up. Small enough that the first
// numbers land quickly, big enough that a board is not a hundred requests.
const VALUE_CHUNK = 4;
// Number cards are cheap and share their counts and sparklines: a whole
// row of them is one request.
const NUMBER_CHUNK = 12;

function storageGet(key) {
    try {
        return window.localStorage.getItem(key);
    } catch {
        return null;
    }
}

function storageSet(key, value) {
    try {
        window.localStorage.setItem(key, value);
    } catch {
        // A remembered flag is a convenience, not a record.
    }
}

/**
 * The sidebar opens as an icon rail: the board gets the width, the tools stay
 * one click away. A reader who expands it is remembered in this browser.
 */
function readSidebar() {
    try {
        const stored = window.localStorage.getItem(SIDEBAR_KEY);
        return stored === null ? true : stored === "min";
    } catch {
        return true;
    }
}

function writeSidebar(min) {
    try {
        window.localStorage.setItem(SIDEBAR_KEY, min ? "min" : "");
    } catch {
        // A remembered sidebar is a convenience, not a record.
    }
}

/**
 * The dashboard client action.
 *
 * One RPC brings back the whole page - the board, its cards and their numbers,
 * the reader's watchlist and note - because a dashboard is read as a unit:
 * twenty cards asking for themselves would be twenty round trips and twenty
 * chances to render half a page.
 */
export class DashboardBoardAction extends Component {
    static template = "ebshel_dashboard.BoardAction";
    static components = { BoardCard, IconPanel };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.rootRef = useRef("root");
        this.moreRef = useRef("more");
        this.periodRef = useRef("period");
        this.state = useState({
            loading: true,
            board: null,
            items: [],
            boards: [],
            periods: [],
            period: null,
            focus: null,
            mine: false,
            editing: false,
            presenting: false,
            refreshedAt: null,
            now: Date.now(),
            resizeLabel: "",
            filter: "",
            boardFilter: "",
            sidebarMin: readSidebar(),
            menuOpen: false,
            watchOpen: false,
            watchlist: [],
            notesOpen: false,
            note: "",
            noteText: "",
            noteState: "",
            userName: "",
            flashId: null,
            views: [],
            savingView: false,
            viewName: "",
            fit: 1,
            tab: null,
            canBuild: false,
            storyHiddenFor: null,
            renaming: false,
            iconOpen: false,
            newName: "",
            addingTab: false,
            tabName: "",
            range: { start: "", end: "" },
            periodOpen: false,
            filters: {},
            tourStep: storageGet(TOUR_KEY) ? -1 : 0,
        });
        this.refreshTimer = null;
        this.slideTimer = null;
        this.clockTimer = null;
        this.resize = null;
        this.pendingFlash = null;
        this.saveNote = useDebounced(() => this.persistNote(), 700);
        this.onFullscreenChange = () => {
            if (!document.fullscreenElement && this.state.presenting) {
                this.state.presenting = false;
            }
        };

        onWillStart(async () => {
            // Chart.js ships with Odoo; the bundle is loaded on demand rather
            // than added to assets_backend, so a database that never opens a
            // dashboard never pays for it.
            await loadBundle("web.chartjs_lib");
            await this.loadBoard(this.initialBoardId);
        });
        onMounted(() => {
            document.addEventListener("fullscreenchange", this.onFullscreenChange);
            // The "updated 40s ago" label ticks on its own.
            this.clockTimer = setInterval(() => (this.state.now = Date.now()), 10000);
        });
        onWillUnmount(() => {
            this.stopAutoRefresh();
            this.stopSlideshow();
            clearInterval(this.clockTimer);
            document.removeEventListener("fullscreenchange", this.onFullscreenChange);
        });
        useExternalListener(window, "click", (ev) => {
            if (this.state.periodOpen && this.periodRef.el && !this.periodRef.el.contains(ev.target)) {
                this.state.periodOpen = false;
            }
            if (this.state.menuOpen && this.moreRef.el && !this.moreRef.el.contains(ev.target)) {
                this.state.menuOpen = false;
            }
        });

        useSortable({
            enable: () => this.state.editing && this.canEdit,
            ref: this.rootRef,
            elements: ".o_dbb_cell[data-item-id]",
            handle: ".o_dbc_handle",
            cursor: "grabbing",
            applyChangeOnDrop: true,
            placeholderClasses: ["o_dbb_placeholder"],
            onDrop: () => this.onCardDropped(),
        });

        // Keyboard: what a reader standing at a wall display reaches for.
        useHotkey("r", () => this.refresh());
        useHotkey("f", () => this.togglePresenting());
        useHotkey("e", () => this.canEdit && this.toggleEditing());
        useHotkey("m", () => this.state.board && this.state.board.allow_mine && this.toggleMine());
        useHotkey("p", () => this.print());
        useHotkey("q", () => this.openQuickFind());
        useHotkey("escape", () => this.onEscape());
        // Brackets are not on the hotkey service's whitelist; shifted arrows are.
        useHotkey("shift+arrowleft", () => this.stepBoard(-1));
        useHotkey("shift+arrowright", () => this.stepBoard(1));
    }

    // ------------------------------------------------------------------
    // Derived state
    // ------------------------------------------------------------------
    get initialBoardId() {
        const params = this.props.action.params || {};
        const context = this.props.action.context || {};
        return params.board_id || context.dashboard_board_id || null;
    }

    get canEdit() {
        return Boolean(this.state.board && this.state.board.can_edit);
    }

    /**
     * Rename the board from its own title.
     *
     * The title is the obvious place to change a name, and a dashboard that
     * carries a menu is renamed there too - the entry and the board should
     * never disagree about what this page is called.
     */
    startRenaming() {
        if (!this.canEdit || this.state.presenting) {
            return;
        }
        this.state.renaming = true;
        this.state.newName = this.state.board.name;
        window.setTimeout(() => {
            const input = this.rootRef.el && this.rootRef.el.querySelector(".o_dbb_title_input");
            if (input) {
                input.focus();
                input.select();
            }
        }, 30);
    }

    /** The title icon opens the same picker the card editor uses. */
    toggleIconPicker() {
        if (!this.canEdit || this.state.presenting) {
            return;
        }
        this.state.iconOpen = !this.state.iconOpen;
    }

    async applyIcon(icon) {
        this.state.iconOpen = false;
        if (!this.state.board || icon === this.state.board.icon) {
            return;
        }
        const result = await this.orm.call("dashboard.board", "set_icon", [[this.state.board.id], icon]);
        this.state.board = { ...this.state.board, icon: result.icon };
        this.state.boards = this.state.boards.map((board) =>
            board.id === this.state.board.id ? { ...board, icon: result.icon } : board);
    }

    async applyRename() {
        const name = (this.state.newName || "").trim();
        this.state.renaming = false;
        if (!name || !this.state.board || name === this.state.board.name) {
            return;
        }
        const result = await this.orm.call("dashboard.board", "rename", [[this.state.board.id], name]);
        this.state.board = { ...this.state.board, name: result.name };
        this.state.boards = this.state.boards.map((board) =>
            board.id === this.state.board.id ? { ...board, name: result.name } : board);
        if (this.env.config.setDisplayName) {
            this.env.config.setDisplayName(result.name);
        }
        this.notification.add(
            result.menu ? _t("Renamed. Its menu entry followed - reload to see it.") : _t("Renamed."),
            { type: "success" });
    }

    onRenameKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.applyRename();
        } else if (ev.key === "Escape") {
            ev.stopPropagation();
            // Put the old name back before closing: leaving the input fires
            // blur, and blur applies what is in it.
            this.state.newName = this.state.board.name;
            this.state.renaming = false;
        }
    }

    /** Put this dashboard in the menu, or move the entry it has. */
    assignMenu() {
        this.state.menuOpen = false;
        if (!this.state.board) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            name: _t("Add to the Menu"),
            res_model: "dashboard.menu.wizard",
            view_mode: "form",
            views: [[false, "form"]],
            target: "new",
            context: { default_board_id: this.state.board.id },
        });
    }

    /** Whether this reader may make dashboards at all - the role, not a board. */
    get canBuild() {
        return Boolean(this.state.canBuild);
    }

    get periodLabel() {
        if (this.state.period && typeof this.state.period === "object") {
            return `${this.state.period.start} – ${this.state.period.end}`;
        }
        const period = this.state.periods.find((entry) => entry.key === this.state.period);
        return period ? period.label : "";
    }

    get periodKey() {
        return this.state.period && typeof this.state.period === "object" ? "custom" : this.state.period;
    }

    /** "All time" is a row of its own above the columns - it spans everything. */
    get allTimePeriod() {
        return this.state.periods.find((entry) => entry.key === "all") || null;
    }

    get periodGroups() {
        const byKey = new Map(this.state.periods.map((entry) => [entry.key, entry]));
        const used = new Set(["all"]);
        const groups = [];
        for (const group of PERIOD_GROUPS) {
            const periods = group.keys.filter((key) => byKey.has(key)).map((key) => {
                used.add(key);
                return byKey.get(key);
            });
            if (periods.length) {
                groups.push({ key: group.key, label: group.label, periods });
            }
        }
        const rest = this.state.periods.filter((entry) => !used.has(entry.key));
        if (rest.length) {
            groups.push({ key: "other", label: _t("Other"), periods: rest });
        }
        return groups;
    }

    togglePeriod() {
        this.state.periodOpen = !this.state.periodOpen;
        if (!this.state.periodOpen) {
            return;
        }
        this.state.menuOpen = false;
        // Open the range on what is on screen, so "Apply" is one click away.
        if (!this.state.range.start || !this.state.range.end) {
            const period = this.state.period;
            const today = new Date();
            this.state.range = typeof period === "object"
                ? { start: period.start, end: period.end }
                : { start: isoDay(new Date(today.getTime() - 29 * 86400000)), end: isoDay(today) };
        }
    }

    pickPeriod(key) {
        this.state.periodOpen = false;
        if (key !== this.periodKey) {
            this.loadBoard(this.state.board.id, key);
        }
    }

    /** The filter bar as the server wants it: only the ones actually set. */
    get filterValues() {
        const board = this.state.board;
        if (!board || !board.filters) {
            return [];
        }
        return board.filters
            .filter((filter) => this.state.filters[filter.id] !== undefined
                && this.state.filters[filter.id] !== "")
            .map((filter) => ({
                model: filter.model,
                field: filter.field,
                value: this.state.filters[filter.id],
            }));
    }

    /** What the chips under the header say: "Country: Belgium". */
    get filterChips() {
        const board = this.state.board;
        if (!board || !board.filters) {
            return [];
        }
        return board.filters
            .filter((filter) => this.state.filters[filter.id] !== undefined
                && this.state.filters[filter.id] !== "")
            .map((filter) => {
                const raw = this.state.filters[filter.id];
                const option = (filter.options || []).find((entry) => String(entry.key) === String(raw));
                return { id: filter.id, label: filter.label, value: option ? option.label : raw };
            });
    }

    /** Is this the option the reader picked? (QWeb has no `String`.) */
    isFilterValue(filter, option) {
        const current = this.state.filters[filter.id];
        return current !== undefined && `${current}` === `${option.key}`;
    }

    onFilterPicked(filter, ev) {
        const value = ev.target.value;
        // Let go of the dropdown: while it holds the focus the hotkey service
        // swallows Escape, and Escape is how a reader clears the bar.
        ev.target.blur();
        this.state.filters = { ...this.state.filters, [filter.id]: value };
        this.reload();
    }

    clearFilter(id) {
        const filters = { ...this.state.filters };
        delete filters[id];
        this.state.filters = filters;
        this.reload();
    }

    clearFilters() {
        this.state.filters = {};
        this.reload();
    }

    /** Read the board again on the same period - a filter changed. */
    reload() {
        if (this.state.board) {
            this.loadBoard(this.state.board.id, this.state.period);
        }
    }

    /**
     * What stands out on this board, as separate insights.
     *
     * Written from the numbers the page already holds - nothing is asked of
     * the server - and only when there is something worth saying: the
     * biggest mover against the previous period, thresholds crossed, numbers
     * out of line with their own past, and a number pacing behind its goal.
     * Each insight names the card it is about, so a click can take the
     * reader to it.
     */
    get narrative() {
        const items = this.state.items.filter((item) => !item.pending && !item.error && !item.hidden);
        if (!items.length) {
            return [];
        }
        const insights = [];
        const movers = items
            .filter((item) => item.delta_percent !== undefined && item.delta_percent !== null
                && Math.abs(item.delta_percent) >= 10)
            .sort((a, b) => Math.abs(b.delta_percent) - Math.abs(a.delta_percent));
        if (movers.length) {
            const top = movers[0];
            const up = top.delta_percent > 0;
            const against = top.compare_mode === "year" ? _t("last year") : _t("the previous period");
            insights.push({
                tone: up ? "up" : "down",
                icon: up ? "fa-arrow-up" : "fa-arrow-down",
                itemId: top.id,
                text: _t("%s is %s%% %s %s", `**${top.name}**`, Math.abs(top.delta_percent),
                         up ? _t("up on") : _t("down on"), against),
            });
            if (movers.length > 1) {
                insights.push({
                    tone: "info", icon: "fa-exchange", itemId: movers[1].id,
                    text: movers.length === 2
                        ? _t("%s moved by 10%% or more too", `**${movers[1].name}**`)
                        : _t("%s other cards moved by 10%% or more", movers.length - 1),
                });
            }
        }
        for (const item of items.filter((entry) => entry.alert)) {
            insights.push({
                tone: item.alert === "danger" ? "danger" : "warning",
                icon: "fa-exclamation-triangle", itemId: item.id,
                text: _t("%s has crossed its threshold", `**${item.name}**`),
            });
        }
        for (const item of items.filter((entry) => entry.anomaly)) {
            insights.push({
                tone: item.anomaly.direction === "above" ? "danger" : "info",
                icon: "fa-bolt", itemId: item.id,
                text: _t("%s is unusual today: %sσ %s its own last %s days", `**${item.name}**`,
                         Math.abs(item.anomaly.z),
                         item.anomaly.direction === "above" ? _t("above") : _t("below"),
                         item.anomaly.days),
            });
        }
        for (const item of items.filter((entry) => entry.pace && entry.pace.status === "behind")) {
            insights.push({
                tone: "warning", icon: "fa-flag-o", itemId: item.id,
                text: _t("%s is behind its goal for the period", `**${item.name}**`),
            });
        }
        return insights.slice(0, 6);
    }

    /** The insights with their text as safe HTML: escaped, then the name in bold. */
    get story() {
        if (this.state.storyHiddenFor === (this.state.board && this.state.board.id)) {
            return [];
        }
        return this.narrative.map((insight) => ({
            ...insight,
            html: markup(escape(insight.text).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")),
        }));
    }

    /** Take the reader to the card an insight is about. */
    jumpTo(insight) {
        if (!insight.itemId || !this.rootRef.el) {
            return;
        }
        const cell = this.rootRef.el.querySelector(`.o_dbb_cell[data-item-id="${insight.itemId}"]`);
        if (cell) {
            cell.scrollIntoView({ behavior: "smooth", block: "center" });
        }
        this.flashCard(insight.itemId);
    }

    hideStory() {
        this.state.storyHiddenFor = this.state.board ? this.state.board.id : null;
    }

    /** The pages of the board: the untabbed first page, then each tab. */
    get tabs() {
        const board = this.state.board;
        if (!board || !board.tabs || !board.tabs.length) {
            return [];
        }
        const untabbed = this.state.items.filter((item) => !item.tab_id).length;
        const pages = [];
        if (untabbed || this.state.editing) {
            pages.push({ id: null, name: _t("Overview"), icon: "fa-home", count: untabbed });
        }
        for (const tab of board.tabs) {
            pages.push({ id: tab.id, name: tab.name, icon: tab.icon,
                         count: this.state.items.filter((item) => item.tab_id === tab.id).length });
        }
        return pages;
    }

    get skeletons() {
        return SKELETONS;
    }

    get showSkeletons() {
        return this.state.loading && !this.state.items.length && Boolean(this.state.board);
    }

    get gridColumns() {
        return (this.state.board && this.state.board.columns) || 12;
    }

    get gridStyle() {
        return `grid-template-columns: repeat(${this.gridColumns}, minmax(0, 1fr));`;
    }

    get compact() {
        return Boolean(this.state.board && this.state.board.density === "compact");
    }

    /** The board's colour drives the accents of the whole page. */
    get accentStyle() {
        const board = this.state.board;
        return `--dbb-accent: ${resolveCardColor({ color: board ? board.color : "indigo" })};`;
    }

    boardColor(board) {
        return resolveCardColor({ color: board.color });
    }

    cardColor(item) {
        return resolveCardColor(item);
    }

    get greeting() {
        const hour = new Date().getHours();
        const part = hour < 12 ? _t("Good morning") : hour < 18 ? _t("Good afternoon") : _t("Good evening");
        return this.state.userName ? `${part}, ${this.state.userName}` : part;
    }

    /** Changes with every refresh, so the countdown ring starts over. */
    get refreshCycle() {
        return this.state.refreshedAt ? this.state.refreshedAt.getTime() : 0;
    }

    /** "Updated 40s ago", ticking. */
    get agoLabel() {
        const at = this.state.refreshedAt;
        if (!at) {
            return _t("Refresh");
        }
        const seconds = Math.max(0, Math.round((this.state.now - at.getTime()) / 1000));
        if (seconds < 10) {
            return _t("Updated just now");
        }
        if (seconds < 60) {
            return _t("Updated %ss ago", seconds);
        }
        const minutes = Math.round(seconds / 60);
        if (minutes < 60) {
            return _t("Updated %sm ago", minutes);
        }
        return _t("Updated at %s", at.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }));
    }

    get focusLabel() {
        const focus = this.state.focus;
        if (!focus) {
            return "";
        }
        return `${focus.field_label || focus.field}: ${focus.label}`;
    }

    get sideBoards() {
        const query = this.state.boardFilter.trim().toLowerCase();
        if (!query) {
            return this.state.boards;
        }
        return this.state.boards.filter((board) => board.name.toLowerCase().includes(query));
    }

    /**
     * The cards on screen: every card except those hiding under their own
     * rule (shown while arranging, or they could never be edited), narrowed
     * by the tile search.
     */
    get visibleItems() {
        const query = this.state.filter.trim().toLowerCase();
        let shown = this.state.items.filter((item) => !item.hidden || this.state.editing);
        // A search looks across every tab; otherwise the current page only.
        if (!query && this.tabs.length) {
            shown = shown.filter((item) => (item.tab_id || null) === (this.state.tab || null));
        }
        if (!query) {
            return shown;
        }
        return shown.filter((item) =>
            String(item.name || "").toLowerCase().includes(query) ||
            String(item.model || "").toLowerCase().includes(query)
        );
    }

    get hiddenCount() {
        return this.state.items.filter((item) => item.hidden).length;
    }

    get palette() {
        return (this.state.board && this.state.board.palette) || "default";
    }

    get contentStyle() {
        return this.state.presenting && this.state.fit < 1 ? `zoom: ${this.state.fit};` : "";
    }

    isPinned(item) {
        return this.state.watchlist.some((watch) => watch.id === item.id);
    }

    /** A watched number, shown the way its card shows it - a ratio as a percentage. */
    watchValue(watch, value) {
        if (value === undefined && watch.as_ratio && watch.ratio !== undefined) {
            return `${watch.ratio}%`;
        }
        const digits = watch.aggregate === "count" ? 0 : watch.digits || 0;
        const number = Number((value === undefined ? watch.value : value) || 0);
        return formatFloat(number, { digits: [16, digits], humanReadable: Math.abs(number) >= 100000, decimals: 1 });
    }

    // ------------------------------------------------------------------
    // Loading
    // ------------------------------------------------------------------
    async loadBoard(boardId, period) {
        this.state.loading = true;
        try {
            // Layout first: the grid, the tabs and the card frames come back
            // in one quick call, then the numbers arrive a few cards at a time.
            const data = await this.orm.call("dashboard.board", "read_board", [], {
                board_id: boardId || false,
                period: period || false,
                focus: this.state.focus || false,
                mine: Boolean(this.state.mine),
                with_values: false,
                filters: this.filterValues,
            });
            this.state.board = data.board;
            this.state.items = data.items || [];
            this.state.boards = data.boards || [];
            if (this.state.tab && !(data.board.tabs || []).some((tab) => tab.id === this.state.tab)) {
                this.state.tab = null;
            }
            if (this.state.tab === null && (data.board.tabs || []).length &&
                    !this.state.items.some((item) => !item.tab_id)) {
                this.state.tab = data.board.tabs[0].id;
            }
            this.state.periods = data.periods || [];
            this.state.period = data.board ? data.board.period : null;
            this.state.mine = Boolean(data.mine);
            this.state.watchlist = data.watchlist || [];
            this.state.views = data.views || [];
            this.state.note = data.note || "";
            this.state.noteText = this.noteToText(data.note || "");
            this.state.userName = data.user_name || "";
            this.state.canBuild = Boolean(data.can_build);
            this.state.refreshedAt = new Date();
            this.state.now = Date.now();
            // The breadcrumb is told the board's name, not the action's: the
            // board switcher changes what is on screen without starting a new
            // action, and a stale name there is worse than none.
            if (this.env.config.setDisplayName) {
                this.env.config.setDisplayName(data.board ? data.board.name : _t("Dashboards"));
            }
            this.restartAutoRefresh();
            this.restartSlideshow();
            this.fitToScreen();
            if (this.pendingFlash) {
                const id = this.pendingFlash;
                this.pendingFlash = null;
                this.flashCard(id);
            }
            await this.fillValues(data.board ? data.board.id : boardId);
        } finally {
            this.state.loading = false;
        }
    }

    /**
     * Ask for the numbers, a few cards at a time, and put them on the board
     * as they arrive.
     *
     * The visible tab goes first - what the reader is looking at fills in
     * before what they would have to click to see. A load that has been
     * overtaken (another board, another period) drops its results: `token`
     * is the generation this fill belongs to.
     */
    async fillValues(boardId) {
        const token = (this.loadToken = (this.loadToken || 0) + 1);
        const pending = this.state.items.filter((item) => item.pending);
        if (!pending.length) {
            return;
        }
        const current = this.state.tab || null;
        // Visible tab first; then cards that ask the same questions side by
        // side - numbers together, then charts by what they split on - so
        // the server's per-request memo can answer each question once.
        const numbers = ["kpi", "gauge", "status", "bullet", "formula"];
        const rank = (item) => [
            (item.tab_id || null) === current ? 0 : 1,
            numbers.includes(item.kind) ? 0 : 1,
            item.model || "",
            item.group_by || item.date_field || "",
        ].join("|");
        pending.sort((a, b) => rank(a).localeCompare(rank(b)));
        const chunks = [];
        let bucket = [];
        for (const item of pending) {
            const limit = numbers.includes(item.kind) ? NUMBER_CHUNK : VALUE_CHUNK;
            if (bucket.length && (bucket.length >= limit ||
                    numbers.includes(bucket[0].kind) !== numbers.includes(item.kind))) {
                chunks.push(bucket);
                bucket = [];
            }
            bucket.push(item);
        }
        if (bucket.length) {
            chunks.push(bucket);
        }
        for (const group of chunks) {
            const chunk = group.map((item) => item.id);
            let values;
            try {
                values = await this.orm.silent.call("dashboard.board", "board_values", [], {
                    board_id: boardId,
                    item_ids: chunk,
                    period: this.state.period,
                    focus: this.state.focus || false,
                    mine: Boolean(this.state.mine),
                    filters: this.filterValues,
                });
            } catch {
                // A card that cannot be computed keeps its frame rather than
                // taking the page down with it.
                values = [];
            }
            if (token !== this.loadToken) {
                return;
            }
            const byId = new Map(values.map((value) => [value.id, value]));
            this.state.items = this.state.items.map((item) => byId.get(item.id) || item);
            for (const id of chunk) {
                if (!byId.has(id)) {
                    const index = this.state.items.findIndex((item) => item.id === id);
                    if (index >= 0) {
                        this.state.items[index] = { ...this.state.items[index], pending: false, error: true };
                    }
                }
            }
        }
    }

    async refresh() {
        if (!this.state.board) {
            return;
        }
        await this.loadBoard(this.state.board.id, this.state.period);
    }

    async refreshItem(item) {
        const payload = await this.orm.call("dashboard.item", "refresh_item", [], {
            item_id: item.id,
            period: this.state.period,
            focus: this.state.focus || false,
            mine: Boolean(this.state.mine),
            filters: this.filterValues,
        });
        if (!payload) {
            return;
        }
        const index = this.state.items.findIndex((entry) => entry.id === item.id);
        if (index >= 0) {
            this.state.items[index] = payload;
        }
    }

    restartAutoRefresh() {
        this.stopAutoRefresh();
        const seconds = this.state.board && this.state.board.auto_refresh;
        if (seconds) {
            this.refreshTimer = setInterval(() => this.refresh(), seconds * 1000);
        }
    }

    stopAutoRefresh() {
        if (this.refreshTimer) {
            clearInterval(this.refreshTimer);
            this.refreshTimer = null;
        }
    }

    /** In presentation mode, a board that says so hands over to the next one. */
    restartSlideshow() {
        this.stopSlideshow();
        const seconds = this.state.board && this.state.board.slide_seconds;
        if (this.state.presenting && seconds > 0 && this.state.boards.length > 1) {
            this.slideTimer = setTimeout(() => this.stepBoard(1), seconds * 1000);
        }
    }

    stopSlideshow() {
        if (this.slideTimer) {
            clearTimeout(this.slideTimer);
            this.slideTimer = null;
        }
    }

    /** Presenting: shrink the page just enough for the whole board to fit. */
    fitToScreen() {
        if (!this.state.presenting) {
            this.state.fit = 1;
            return;
        }
        window.setTimeout(() => {
            const root = this.rootRef.el;
            const content = root && root.querySelector(".o_dbb_content");
            const hero = root && root.querySelector(".o_dbb_hero");
            if (!content || !hero) {
                return;
            }
            const available = window.innerHeight - hero.getBoundingClientRect().height - 24;
            const needed = content.scrollHeight / (this.state.fit || 1);
            this.state.fit = needed > available ? Math.max(0.5, available / needed) : 1;
        }, 80);
    }

    // ------------------------------------------------------------------
    // Shell: sidebar, menu, quick find, watchlist, notes
    // ------------------------------------------------------------------
    toggleSidebar() {
        this.state.sidebarMin = !this.state.sidebarMin;
        writeSidebar(this.state.sidebarMin);
    }

    toggleMenu() {
        this.state.menuOpen = !this.state.menuOpen;
    }

    menuAction(name) {
        this.state.menuOpen = false;
        this[name]();
    }

    openQuickFind() {
        const actions = [
            { key: "refresh", label: _t("Refresh"), icon: "fa-refresh", run: () => this.refresh() },
            { key: "present", label: _t("Present full screen"), icon: "fa-expand", run: () => this.togglePresenting() },
            { key: "print", label: _t("Print"), icon: "fa-print", run: () => this.print() },
            { key: "pdf", label: _t("Download as PDF"), icon: "fa-file-pdf-o", run: () => this.downloadPdf() },
            { key: "xlsx", label: _t("Download as a spreadsheet"), icon: "fa-file-excel-o", run: () => this.downloadXlsx() },
            { key: "export", label: _t("Export this dashboard"), icon: "fa-download", run: () => this.exportBoard() },
            { key: "tour", label: _t("Show the tour"), icon: "fa-question-circle", run: () => this.startTour() },
        ];
        if (this.canBuild) {
            actions.push({ key: "build", label: _t("Build a dashboard from a model"),
                           icon: "fa-magic", run: () => this.buildBoard() });
        }
        if (this.canEdit) {
            actions.unshift(
                { key: "arrange", label: _t("Arrange the cards"), icon: "fa-arrows", run: () => this.toggleEditing() },
                { key: "card", label: _t("Add a card"), icon: "fa-plus", run: () => this.addCard() },
                { key: "view", label: _t("Save this view"), icon: "fa-bookmark-o", run: () => this.startSavingView() },
            );
        }
        this.dialog.add(QuickFindDialog, {
            boards: this.state.boards,
            actions,
            onPick: (hit) => {
                if (hit.type === "board") {
                    this.switchBoard(hit.board.id);
                } else if (hit.type === "action") {
                    hit.action.run();
                } else {
                    this.goToCard(hit.card.board_id, hit.card.id);
                }
            },
        });
    }

    /** Open a board and light the card up once it is on screen. */
    goToCard(boardId, itemId) {
        if (this.state.board && this.state.board.id === boardId) {
            this.flashCard(itemId);
            return;
        }
        this.pendingFlash = itemId;
        this.switchBoard(boardId);
    }

    flashCard(itemId) {
        this.state.filter = "";
        this.state.flashId = itemId;
        window.setTimeout(() => {
            const cell = this.rootRef.el && this.rootRef.el.querySelector(`.o_dbb_cell[data-item-id="${itemId}"]`);
            if (cell && cell.scrollIntoView) {
                cell.scrollIntoView({ behavior: "smooth", block: "center" });
            }
        }, 50);
        window.setTimeout(() => {
            if (this.state.flashId === itemId) {
                this.state.flashId = null;
            }
        }, 2200);
    }

    toggleWatchlist() {
        this.state.watchOpen = !this.state.watchOpen;
    }

    async togglePin(item) {
        const pinned = await this.orm.call("dashboard.preference", "toggle_watch", [item.id]);
        this.state.watchlist = await this.orm.call("dashboard.preference", "watchlist_payload", []);
        if (pinned) {
            this.state.watchOpen = true;
            this.notification.add(_t("Pinned to your watchlist."), { type: "success" });
        }
    }

    toggleNotes() {
        this.state.notesOpen = !this.state.notesOpen;
    }

    /** The note is stored as HTML; the drawer edits plain text lines. */
    noteToText(html) {
        if (!html) {
            return "";
        }
        const box = document.createElement("div");
        box.innerHTML = String(html).replace(/<\/p>|<br\s*\/?>/gi, "\n");
        return box.textContent.replace(/\n+$/, "");
    }

    textToNote(text) {
        const escape = (line) => line.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        return text.split("\n").map((line) => `<p>${escape(line) || "<br/>"}</p>`).join("");
    }

    onNoteInput(ev) {
        this.state.noteText = ev.target.value;
        this.state.noteState = _t("saving…");
        this.saveNote();
    }

    async persistNote() {
        if (!this.state.board) {
            return;
        }
        const html = this.state.noteText.trim() ? this.textToNote(this.state.noteText) : "";
        const saved = await this.orm.call("dashboard.note", "save_note", [this.state.board.id, html]);
        this.state.note = saved.body || "";
        this.state.noteState = _t("saved");
        window.setTimeout(() => (this.state.noteState = ""), 1500);
    }

    // ------------------------------------------------------------------
    // Control panel
    // ------------------------------------------------------------------
    switchBoard(boardId) {
        this.loadToken = (this.loadToken || 0) + 1;
        this.state.focus = null;
        this.state.editing = false;
        this.state.filter = "";
        this.state.tab = null;
        this.state.items = []; // skeletons while the next board arrives
        this.loadBoard(boardId);
    }

    // ------------------------------------------------------------------
    // PDF
    // ------------------------------------------------------------------
    /**
     * Every chart on screen, keyed by card, as a PNG.
     *
     * The file shows the charts the reader is looking at - the server draws
     * the rest from the numbers, which it recomputes itself.
     */
    boardImages(onlyId = null) {
        const images = {};
        const cells = this.rootRef.el ? this.rootRef.el.querySelectorAll(".o_dbb_cell[data-item-id]") : [];
        for (const cell of cells) {
            const id = Number(cell.dataset.itemId);
            if (onlyId && id !== onlyId) {
                continue;
            }
            const png = canvasToPng(cell.querySelector("canvas"));
            if (png) {
                images[id] = png;
            }
        }
        return images;
    }

    async downloadPdf(itemIds = null, images = null) {
        this.state.menuOpen = false;
        if (!this.state.board) {
            return;
        }
        this.notification.add(_t("Building the PDF…"), { type: "info" });
        const action = await this.orm.call("dashboard.board", "download_pdf", [[this.state.board.id]], {
            images: images || this.boardImages(),
            period: this.state.period,
            focus: this.state.focus || false,
            mine: Boolean(this.state.mine),
            item_ids: itemIds || false,
        });
        this.action.doAction(action);
    }

    /** One card, from its own menu: its chart, and a page of its own. */
    onCardPdf(item, image) {
        this.downloadPdf([item.id], image ? { [item.id]: image } : {});
    }

    /** The numbers behind the pictures, as a spreadsheet. */
    async downloadXlsx(itemIds = null) {
        this.state.menuOpen = false;
        if (!this.state.board) {
            return;
        }
        this.notification.add(_t("Building the spreadsheet…"), { type: "info" });
        const action = await this.orm.call("dashboard.board", "download_xlsx", [[this.state.board.id]], {
            period: this.state.period,
            focus: this.state.focus || false,
            mine: Boolean(this.state.mine),
            filters: this.filterValues,
            item_ids: itemIds || false,
        });
        this.action.doAction(action);
    }

    onCardXlsx(item) {
        this.downloadXlsx([item.id]);
    }

    // ------------------------------------------------------------------
    // Tabs
    // ------------------------------------------------------------------
    pickTab(id) {
        this.state.tab = id;
        this.state.filter = "";
    }

    startAddingTab() {
        this.state.addingTab = true;
        this.state.tabName = "";
        window.setTimeout(() => {
            const input = this.rootRef.el && this.rootRef.el.querySelector(".o_dbb_tab_input");
            if (input) {
                input.focus();
            }
        }, 30);
    }

    async addTab() {
        const name = this.state.tabName.trim();
        this.state.addingTab = false;
        if (!name) {
            return;
        }
        const tab = await this.orm.call("dashboard.board", "add_tab", [[this.state.board.id], name]);
        await this.refresh();
        this.state.tab = tab.id;
    }

    onTabKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.addTab();
        } else if (ev.key === "Escape") {
            this.state.addingTab = false;
        }
    }

    async moveToTab(item, ev) {
        const raw = ev.target.value;
        ev.target.value = "";
        if (raw === "") {
            return;
        }
        const tabId = raw === "none" ? false : Number(raw);
        await this.orm.call("dashboard.board", "move_to_tab", [[this.state.board.id], item.id, tabId]);
        await this.refresh();
    }

    // ------------------------------------------------------------------
    // Custom date range
    // ------------------------------------------------------------------
    onRangeInput(which, ev) {
        this.state.range = { ...this.state.range, [which]: ev.target.value };
    }

    applyRange() {
        const { start, end } = this.state.range;
        if (!start || !end || end < start) {
            this.notification.add(_t("Pick a start and an end, in that order."), { type: "warning" });
            return;
        }
        this.state.periodOpen = false;
        this.loadBoard(this.state.board.id, { start, end });
    }

    // ------------------------------------------------------------------
    // The tour
    // ------------------------------------------------------------------
    get tourSteps() {
        return [
            { title: _t("Welcome to your dashboards"), text: _t("Every card here is a live question asked of your data. Click a number to open the records it counted."), icon: "fa-tachometer" },
            { title: _t("Narrow the whole page"), text: _t("Click a bar, a slice or a table row and every card on the same model follows. Esc clears it."), icon: "fa-filter" },
            { title: _t("The rail on the left"), text: _t("Your dashboards and the tools: quick find (Q), a watchlist for pinned numbers, private notes, presentation mode."), icon: "fa-bars" },
            { title: _t("Make it yours"), text: _t("Arrange (E) drags and resizes cards, + Card adds one with a live preview, and the more menu builds, exports and saves views."), icon: "fa-magic" },
        ];
    }

    tourNext() {
        if (this.state.tourStep + 1 >= this.tourSteps.length) {
            this.tourDone();
        } else {
            this.state.tourStep += 1;
        }
    }

    tourDone() {
        this.state.tourStep = -1;
        storageSet(TOUR_KEY, "1");
    }

    startTour() {
        this.state.menuOpen = false;
        this.state.tourStep = 0;
    }

    stepBoard(step) {
        const boards = this.state.boards;
        if (!this.state.board || boards.length < 2) {
            return;
        }
        const index = boards.findIndex((board) => board.id === this.state.board.id);
        const next = boards[(index + step + boards.length) % boards.length];
        this.switchBoard(next.id);
    }

    toggleEditing() {
        if (!this.state.editing) {
            // Remember the arrangement being left behind: dragging writes as
            // it happens, so discarding means putting this back.
            this.layoutBefore = this.state.items.map((item) => ({
                id: item.id,
                sequence: item.sequence,
                width: item.width,
                height: item.height,
                tab_id: item.tab_id || false,
            }));
        }
        this.state.editing = !this.state.editing;
    }

    /** Leave arrange mode, keeping what was moved. */
    doneEditing() {
        this.state.editing = false;
        this.layoutBefore = null;
    }

    /** Leave arrange mode and put the arrangement back as it was found. */
    async discardEditing() {
        const before = this.layoutBefore;
        this.state.editing = false;
        this.layoutBefore = null;
        if (!before || !this.state.board) {
            return;
        }
        await this.orm.call("dashboard.board", "restore_layout", [[this.state.board.id], before]);
        this.notification.add(_t("The arrangement is back as it was."), { type: "info" });
        await this.refresh();
    }

    toggleMine() {
        this.state.mine = !this.state.mine;
        this.refresh();
    }

    async toggleDefault() {
        const board = this.state.board;
        await this.orm.call("dashboard.board", "set_default", [[board.id], !board.is_default]);
        await this.refresh();
        this.notification.add(
            this.state.board.is_default
                ? _t("This dashboard now opens first.")
                : _t("This dashboard is no longer your favourite."),
            { type: "success" }
        );
    }

    togglePresenting() {
        this.state.presenting = !this.state.presenting;
        this.state.editing = false;
        this.state.menuOpen = false;
        const el = this.rootRef.el;
        if (this.state.presenting && el && el.requestFullscreen) {
            el.requestFullscreen().catch(() => {});
        } else if (!this.state.presenting && document.fullscreenElement && document.exitFullscreen) {
            document.exitFullscreen().catch(() => {});
        }
        this.restartSlideshow();
        this.fitToScreen();
    }

    // ------------------------------------------------------------------
    // Saved views
    // ------------------------------------------------------------------
    startSavingView() {
        this.state.menuOpen = false;
        this.state.savingView = true;
        this.state.viewName = "";
        window.setTimeout(() => {
            const input = this.rootRef.el && this.rootRef.el.querySelector(".o_dbb_view_input");
            if (input) {
                input.focus();
            }
        }, 30);
    }

    async saveView() {
        const name = this.state.viewName.trim();
        if (!name) {
            this.state.savingView = false;
            return;
        }
        const period = typeof this.state.period === "object"
            ? `custom:${this.state.period.start}:${this.state.period.end}` : this.state.period;
        await this.orm.call("dashboard.view", "save_view", [
            this.state.board.id, name, period, this.state.focus || false, Boolean(this.state.mine),
        ]);
        this.state.views = await this.orm.call("dashboard.view", "list_views", [this.state.board.id]);
        this.state.savingView = false;
        this.notification.add(_t("View \"%s\" saved.", name), { type: "success" });
    }

    onViewKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.saveView();
        } else if (ev.key === "Escape") {
            this.state.savingView = false;
        }
    }

    applyView(view) {
        this.state.focus = view.focus || null;
        this.state.mine = Boolean(view.mine);
        let period = view.period || this.state.period;
        if (typeof period === "string" && period.startsWith("custom:")) {
            const [, start, end] = period.split(":");
            period = { start, end };
        }
        this.loadBoard(this.state.board.id, period);
    }

    async removeView(view) {
        await this.orm.call("dashboard.view", "remove_view", [view.id]);
        this.state.views = this.state.views.filter((entry) => entry.id !== view.id);
    }

    onEscape() {
        if (this.state.tourStep >= 0) {
            this.tourDone();
        } else if (this.state.iconOpen) {
            // The hotkey service takes Escape in the capture phase, so the
            // picker's own listener never sees it on this page.
            this.state.iconOpen = false;
        } else if (this.state.menuOpen) {
            this.state.menuOpen = false;
        } else if (this.state.periodOpen) {
            this.state.periodOpen = false;
        } else if (this.state.addingTab) {
            this.state.addingTab = false;
        } else if (this.state.savingView) {
            this.state.savingView = false;
        } else if (this.state.filter) {
            this.state.filter = "";
        } else if (this.state.focus) {
            this.clearFocus();
        } else if (this.filterChips.length) {
            this.clearFilters();
        } else if (this.state.editing) {
            this.doneEditing();
        } else if (this.state.presenting) {
            this.togglePresenting();
        }
    }

    print() {
        window.print();
    }

    /** Card geometry: a width out of twelve, scaled to the board's grid. */
    cardSpan(item) {
        const columns = this.gridColumns;
        return Math.max(1, Math.min(Math.round(((item.width || 3) * columns) / 12), columns));
    }

    cardStyle(item) {
        return `grid-column: span ${this.cardSpan(item)};`;
    }

    spanToWidth(span) {
        return Math.max(1, Math.min(Math.round((span * 12) / this.gridColumns), 12));
    }

    // ------------------------------------------------------------------
    // Editing
    // ------------------------------------------------------------------
    async onCardDropped() {
        // Read the order back from the DOM: the sortable has already moved the
        // node, and the DOM is what the user actually sees.
        const nodes = this.rootRef.el.querySelectorAll(".o_dbb_cell[data-item-id]");
        const layout = [...nodes].map((node) => Number(node.dataset.itemId));
        await this.orm.call("dashboard.board", "arrange_items", [[this.state.board.id], layout]);
        await this.refresh();
    }

    async widen(item, step) {
        const columns = this.gridColumns;
        const span = Math.max(1, Math.min(this.cardSpan(item) + step, columns));
        const width = this.spanToWidth(span);
        if (width === item.width) {
            return;
        }
        await this.orm.call("dashboard.board", "resize_item", [[this.state.board.id], item.id, width]);
        await this.refresh();
    }

    /** Drag the corner of a card: width snaps to columns, height is free. */
    startResize(ev, item) {
        ev.preventDefault();
        ev.stopPropagation();
        const cell = ev.target.closest(".o_dbb_cell");
        const grid = this.rootRef.el.querySelector(".o_dbb_grid");
        if (!cell || !grid) {
            return;
        }
        const columns = this.gridColumns;
        const style = window.getComputedStyle(grid);
        const padding = parseFloat(style.paddingLeft || 0) + parseFloat(style.paddingRight || 0);
        const gridWidth = grid.getBoundingClientRect().width - padding;
        const colPx = (gridWidth - GRID_GAP * (columns - 1)) / columns;
        const card = cell.querySelector(".o_dbc_card");
        const rect = card.getBoundingClientRect();
        this.resize = {
            item, cell, card, columns, colPx,
            startX: ev.clientX, startY: ev.clientY,
            startW: rect.width, startH: rect.height,
            span: this.cardSpan(item), height: Math.round(rect.height),
        };
        this.onResizeMove = (event) => this.moveResize(event);
        this.onResizeEnd = () => this.endResize();
        document.addEventListener("pointermove", this.onResizeMove);
        document.addEventListener("pointerup", this.onResizeEnd, { once: true });
        document.body.classList.add("o_dbb_resizing");
    }

    moveResize(ev) {
        const r = this.resize;
        if (!r) {
            return;
        }
        const width = r.startW + (ev.clientX - r.startX);
        r.span = Math.max(1, Math.min(Math.round((width + GRID_GAP) / (r.colPx + GRID_GAP)), r.columns));
        r.height = Math.max(MIN_HEIGHT, Math.min(Math.round(r.startH + (ev.clientY - r.startY)), MAX_HEIGHT));
        r.cell.style.gridColumn = `span ${r.span}`;
        r.card.style.minHeight = `${r.height}px`;
        this.state.resizeLabel = `${this.spanToWidth(r.span)}/12 · ${r.height}px`;
    }

    async endResize() {
        document.removeEventListener("pointermove", this.onResizeMove);
        document.body.classList.remove("o_dbb_resizing");
        const r = this.resize;
        this.resize = null;
        this.state.resizeLabel = "";
        if (!r) {
            return;
        }
        const width = this.spanToWidth(r.span);
        const height = r.height;
        if (width === r.item.width && height === r.item.height) {
            return;
        }
        await this.orm.call("dashboard.board", "resize_item",
                            [[this.state.board.id], r.item.id, width, height]);
        await this.refresh();
    }

    /**
     * Remove a card from the board, once the reader has said so out loud.
     *
     * The dialog names the card: on a board of twenty tiles "are you sure?"
     * is not a question anybody can answer.
     */
    removeCard(item) {
        this.dialog.add(ConfirmationDialog, {
            title: _t("Remove this card"),
            body: _t('"%s" will be removed from this dashboard. Its data is untouched.', item.name),
            confirmLabel: _t("Remove"),
            confirmClass: "btn-danger",
            confirm: async () => {
                await this.orm.call("dashboard.item", "delete_card", [], { item_id: item.id });
                this.state.items = this.state.items.filter((entry) => entry.id !== item.id);
                this.notification.add(_t("%s removed.", item.name), { type: "success" });
                await this.refresh();
            },
            cancel: () => {},
        });
    }

    /** Delete the whole dashboard, and open whichever one comes next. */
    deleteBoard() {
        const board = this.state.board;
        if (!board) {
            return;
        }
        this.state.menuOpen = false;
        this.dialog.add(ConfirmationDialog, {
            title: _t("Delete this dashboard"),
            body: _t('"%s" and its cards will be deleted. The records behind them are untouched, and this cannot be undone.', board.name),
            confirmLabel: _t("Delete"),
            confirmClass: "btn-danger",
            confirm: async () => {
                const result = await this.orm.call("dashboard.board", "delete_board", [], { board_id: board.id });
                this.notification.add(_t("%s deleted.", result.name || board.name), { type: "success" });
                this.state.board = null;
                this.state.items = [];
                this.switchBoard(result.next || false);
            },
            cancel: () => {},
        });
    }

    async duplicateCard(item) {
        await this.orm.call("dashboard.item", "action_duplicate", [[item.id]]);
        await this.refresh();
        this.notification.add(_t("Card duplicated."), { type: "success" });
    }

    async moveCard(item, ev) {
        const boardId = Number(ev.target.value);
        ev.target.value = "";
        if (!boardId) {
            return;
        }
        await this.orm.write("dashboard.item", [item.id], { board_id: boardId });
        await this.refresh();
        const board = this.state.boards.find((entry) => entry.id === boardId);
        this.notification.add(_t("Card moved to %s.", board ? board.name : boardId), { type: "success" });
    }

    /** Tidy: reorder cards so rows fill up - widest first inside each row width. */
    async fillGaps() {
        const items = [...this.state.items].sort((a, b) => (b.width || 3) - (a.width || 3));
        await this.orm.call("dashboard.board", "arrange_items", [[this.state.board.id], items.map((item) => item.id)]);
        await this.refresh();
        this.notification.add(_t("Cards rearranged to fill the rows."), { type: "success" });
    }

    async duplicateBoard() {
        const copies = await this.orm.call("dashboard.board", "copy", [[this.state.board.id]]);
        const copyId = Array.isArray(copies) ? copies[0] : copies;
        this.notification.add(_t("Dashboard duplicated."), { type: "success" });
        this.switchBoard(copyId);
    }

    openCard(item) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: item.name,
            res_model: "dashboard.item",
            res_id: item.id,
            views: [[false, "form"]],
            target: "new",
        }, {
            onClose: () => this.refresh(),
        });
    }

    addCard() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: _t("New card"),
            res_model: "dashboard.item",
            views: [[false, "form"]],
            target: "new",
            context: { default_board_id: this.state.board.id },
        }, {
            onClose: () => this.refresh(),
        });
    }

    async exportBoard() {
        const action = await this.orm.call("dashboard.board", "action_export_boards",
                                           [[this.state.board.id]]);
        this.action.doAction(action);
    }

    importBoard() {
        this.action.doAction("ebshel_dashboard.action_dashboard_import", {
            onClose: () => this.refresh(),
        });
    }

    buildBoard() {
        this.action.doAction("ebshel_dashboard.action_dashboard_build", {
            onClose: () => this.refresh(),
        });
    }

    editBoard() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: this.state.board.name,
            res_model: "dashboard.board",
            res_id: this.state.board.id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    newBoard() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: _t("New dashboard"),
            res_model: "dashboard.board",
            views: [[false, "form"]],
            target: "current",
        });
    }

    // ------------------------------------------------------------------
    // Focus and drill-through
    // ------------------------------------------------------------------
    /** One clicked part becomes the board's focus; clicking it again clears it. */
    onFocus(item, point, onDate) {
        const next = {
            item_id: item.id,
            model: item.model,
            field: onDate ? item.date_field : item.group_by,
            field_label: onDate ? item.date_label : item.group_label,
            key: point.key === undefined ? null : point.key,
            interval: item.interval,
            on_date: Boolean(onDate),
            label: point.label,
        };
        const current = this.state.focus;
        const same = current && current.item_id === next.item_id &&
            current.field === next.field && current.key === next.key;
        this.state.focus = same ? null : next;
        this.loadBoard(this.state.board.id, this.state.period);
    }

    clearFocus() {
        if (!this.state.focus) {
            return;
        }
        this.state.focus = null;
        this.loadBoard(this.state.board.id, this.state.period);
    }

    async openFocusRecords() {
        const focus = this.state.focus;
        if (!focus) {
            return;
        }
        const action = await this.orm.call("dashboard.item", "drill_action", [], {
            item_id: focus.item_id,
            period: this.state.period,
            part: { key: focus.key, on_date: focus.on_date },
            mine: Boolean(this.state.mine),
            filters: this.filterValues,
        });
        if (action) {
            this.action.doAction(action);
        }
    }

    async onDrill(item, options) {
        if (options && options.resId) {
            this.action.doAction({
                type: "ir.actions.act_window",
                res_model: item.model,
                res_id: options.resId,
                views: [[false, "form"]],
                target: "current",
            });
            return;
        }
        // `part` is sent only when a *piece* of the card was clicked: a null
        // key is the "Undefined" bucket, which is not the same question as the
        // card as a whole.
        let part = null;
        if (options && options.subvalue) {
            part = { subvalue: options.subvalue };
        } else if (options && "key" in options) {
            part = { key: options.key === undefined ? null : options.key,
                     on_date: Boolean(options.onDate) };
        }
        const action = await this.orm.call("dashboard.item", "drill_action", [], {
            item_id: item.id,
            period: this.state.period,
            part,
            focus: this.state.focus || false,
            mine: Boolean(this.state.mine),
            filters: this.filterValues,
        });
        if (!action) {
            this.notification.add(_t("This card has nothing to open."), { type: "warning" });
            return;
        }
        this.action.doAction(action);
    }
}

registry.category("actions").add("ebshel_dashboard.board", DashboardBoardAction);
