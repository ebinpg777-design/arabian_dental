/** @odoo-module **/

import { Component, useExternalListener, useRef, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";

const RECENT_KEY = "ebshel_dashboard.recent_icons";
const RECENT_MAX = 12;

export const ICON_GROUPS = [
    { key: "analytics", label: _t("Analytics"), icons: [
        ["fa-bar-chart", "bars chart"], ["fa-line-chart", "line trend"], ["fa-area-chart", "area"],
        ["fa-pie-chart", "pie share"], ["fa-tachometer", "gauge speed dashboard"], ["fa-signal", "signal"],
        ["fa-dashboard", "dashboard"], ["fa-trophy", "win award"], ["fa-bullseye", "target goal"],
        ["fa-crosshairs", "target aim"], ["fa-percent", "ratio percent"], ["fa-hashtag", "number count"],
        ["fa-sort-numeric-asc", "rank order"], ["fa-table", "table grid"], ["fa-th-large", "grid tiles"],
        ["fa-list-ol", "list ranked"], ["fa-tasks", "progress tasks"], ["fa-filter", "funnel filter"],
        ["fa-magic", "auto wand"], ["fa-rocket", "launch growth"], ["fa-lightbulb-o", "idea"],
        ["fa-fire", "hot urgent"], ["fa-diamond", "premium"], ["fa-flash", "bolt fast"],
    ]},
    { key: "status", label: _t("Status"), icons: [
        ["fa-check-circle", "done ok approved"], ["fa-check-square-o", "done check"], ["fa-times-circle", "cancel failed"],
        ["fa-ban", "blocked forbidden"], ["fa-exclamation-triangle", "warning alert"], ["fa-exclamation-circle", "important"],
        ["fa-question-circle", "unknown question"], ["fa-info-circle", "info"], ["fa-hourglass-half", "waiting pending"],
        ["fa-clock-o", "time late"], ["fa-history", "history past"], ["fa-refresh", "refresh sync"],
        ["fa-spinner", "in progress"], ["fa-flag", "flag"], ["fa-flag-checkered", "finish"],
        ["fa-thumbs-o-up", "good like"], ["fa-thumbs-o-down", "bad dislike"], ["fa-star", "favourite star"],
        ["fa-heart", "love"], ["fa-bell", "notification alarm"], ["fa-bullhorn", "announce"],
        ["fa-lock", "locked"], ["fa-unlock", "open"], ["fa-circle", "dot light"],
        ["fa-pause-circle", "paused"], ["fa-play-circle", "running"], ["fa-stop-circle", "stopped"],
    ]},
    { key: "money", label: _t("Money"), icons: [
        ["fa-money", "cash revenue"], ["fa-credit-card", "card payment"], ["fa-bank", "bank"],
        ["fa-calculator", "calc"], ["fa-usd", "dollar"], ["fa-eur", "euro"], ["fa-gbp", "pound"],
        ["fa-inr", "rupee"], ["fa-jpy", "yen"], ["fa-btc", "bitcoin"], ["fa-tag", "price tag"],
        ["fa-tags", "tags"], ["fa-shopping-cart", "cart sales"], ["fa-shopping-basket", "basket"],
        ["fa-shopping-bag", "bag"], ["fa-gift", "gift"], ["fa-balance-scale", "balance"],
        ["fa-line-chart", "revenue"], ["fa-pie-chart", "share"], ["fa-file-text-o", "invoice"],
        ["fa-handshake-o", "deal"], ["fa-briefcase", "business"],
    ]},
    { key: "documents", label: _t("Documents"), icons: [
        ["fa-file-text-o", "document"], ["fa-file-o", "file"], ["fa-files-o", "files"],
        ["fa-folder-open-o", "folder"], ["fa-clipboard", "clipboard"], ["fa-pencil", "edit draft"],
        ["fa-pencil-square-o", "edit"], ["fa-paperclip", "attachment"], ["fa-print", "print"],
        ["fa-envelope-o", "mail"], ["fa-paper-plane", "sent"], ["fa-inbox", "inbox"],
        ["fa-archive", "archive"], ["fa-sticky-note-o", "note"], ["fa-book", "book"],
        ["fa-newspaper-o", "news"], ["fa-list-alt", "form"], ["fa-file-pdf-o", "pdf"],
        ["fa-file-excel-o", "spreadsheet"], ["fa-quote-left", "quote"], ["fa-commenting-o", "comment"],
    ]},
    { key: "logistics", label: _t("Logistics"), icons: [
        ["fa-truck", "delivery shipping"], ["fa-cube", "product unit"], ["fa-cubes", "stock inventory"],
        ["fa-industry", "factory manufacturing"], ["fa-wrench", "repair maintenance"], ["fa-cogs", "settings machine"],
        ["fa-cog", "gear"], ["fa-barcode", "barcode"], ["fa-qrcode", "qr"], ["fa-recycle", "recycle"],
        ["fa-map-marker", "location"], ["fa-map-o", "map"], ["fa-globe", "world country"],
        ["fa-plane", "flight"], ["fa-ship", "sea"], ["fa-train", "rail"], ["fa-bicycle", "bike"],
        ["fa-car", "car"], ["fa-road", "route"], ["fa-dropbox", "box"],
        ["fa-exchange", "transfer"], ["fa-random", "route"], ["fa-battery-three-quarters", "battery"],
    ]},
    { key: "people", label: _t("People"), icons: [
        ["fa-user", "user person"], ["fa-user-o", "person"], ["fa-users", "team group"],
        ["fa-user-plus", "new user"], ["fa-user-circle-o", "avatar"], ["fa-user-secret", "anonymous"],
        ["fa-address-card-o", "contact"], ["fa-address-book", "contacts"], ["fa-handshake-o", "partner"],
        ["fa-comments-o", "chat discussion"], ["fa-comment-o", "message"], ["fa-phone", "call"],
        ["fa-headphones", "support"], ["fa-child", "child"], ["fa-female", "woman"], ["fa-male", "man"],
        ["fa-smile-o", "happy"], ["fa-frown-o", "unhappy"], ["fa-meh-o", "neutral"],
        ["fa-id-badge", "employee"], ["fa-graduation-cap", "training"], ["fa-birthday-cake", "birthday"],
    ]},
    { key: "time", label: _t("Time & Places"), icons: [
        ["fa-calendar", "calendar"], ["fa-calendar-check-o", "planned"], ["fa-calendar-times-o", "missed"],
        ["fa-calendar-plus-o", "schedule"], ["fa-hourglass-end", "deadline"], ["fa-clock-o", "clock"],
        ["fa-building-o", "company office"], ["fa-building", "company"], ["fa-home", "home"],
        ["fa-briefcase", "work"], ["fa-suitcase", "travel"], ["fa-shield", "protected"],
        ["fa-stethoscope", "health"], ["fa-medkit", "medical"], ["fa-hospital-o", "hospital"],
        ["fa-university", "school"], ["fa-bed", "hotel"], ["fa-coffee", "break"],
        ["fa-cutlery", "food"], ["fa-sun-o", "day"], ["fa-moon-o", "night"], ["fa-umbrella", "leave"],
    ]},
    { key: "tech", label: _t("Technology"), icons: [
        ["fa-laptop", "laptop"], ["fa-desktop", "desktop"], ["fa-mobile", "phone"], ["fa-tablet", "tablet"],
        ["fa-server", "server"], ["fa-database", "database records"], ["fa-cloud", "cloud"],
        ["fa-cloud-upload", "upload"], ["fa-cloud-download", "download"], ["fa-wifi", "network"],
        ["fa-plug", "integration"], ["fa-code", "code"], ["fa-bug", "bug issue"], ["fa-terminal", "shell"],
        ["fa-key", "key access"], ["fa-microchip", "chip"], ["fa-hdd-o", "disk"], ["fa-sitemap", "structure"],
        ["fa-link", "link"], ["fa-share-alt", "share"], ["fa-rss", "feed"], ["fa-camera", "photo"],
    ]},
];

const ALL_ICONS = ICON_GROUPS.flatMap((group) =>
    group.icons.map(([icon, words]) => ({ icon, words: `${icon.replace("fa-", "").replace(/-/g, " ")} ${words}`, group: group.key }))
);

function readRecent() {
    try {
        const raw = window.localStorage.getItem(RECENT_KEY);
        const list = raw ? JSON.parse(raw) : [];
        return Array.isArray(list) ? list.filter((icon) => typeof icon === "string") : [];
    } catch {
        return [];
    }
}

function writeRecent(list) {
    try {
        window.localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, RECENT_MAX)));
    } catch {
        // Storage may be unavailable; the recent row is a convenience, not a record.
    }
}

/**
 * The searchable, grouped grid of icons.
 *
 * Search matches the icon's name and a few words about what it usually stands
 * for ("late", "revenue", "team"), so a user types the meaning and gets the
 * glyph. Recently picked icons come first, across every place that picks one:
 * the card editor, the dashboard's own title, the board form.
 */
export class IconPanel extends Component {
    static template = "ebshel_dashboard.IconPanel";
    static props = {
        current: { type: String, optional: true },
        onPick: Function,
        onClose: { type: Function, optional: true },
        anchor: { type: String, optional: true },
    };

    setup() {
        this.rootRef = useRef("root");
        this.searchRef = useRef("search");
        this.state = useState({ query: "", group: "all", recent: readRecent() });
        window.setTimeout(() => this.searchRef.el && this.searchRef.el.focus(), 0);
        useExternalListener(window, "click", (ev) => {
            // The click that opened the panel is still travelling: only a click
            // outside both the panel and its anchor closes it.
            const anchor = this.props.anchor && ev.target.closest && ev.target.closest(this.props.anchor);
            if (!anchor && this.rootRef.el && !this.rootRef.el.contains(ev.target)) {
                this.close();
            }
        });
        useExternalListener(window, "keydown", (ev) => {
            if (ev.key === "Escape") {
                ev.stopPropagation();
                this.close();
            }
        });
    }

    get groups() {
        return [{ key: "all", label: _t("All") },
                ...ICON_GROUPS.map((group) => ({ key: group.key, label: group.label }))];
    }

    get matches() {
        const query = this.state.query.trim().toLowerCase();
        const group = this.state.group;
        const seen = new Set();
        const result = [];
        for (const entry of ALL_ICONS) {
            if (group !== "all" && entry.group !== group) {
                continue;
            }
            if (query && !entry.words.includes(query)) {
                continue;
            }
            if (seen.has(entry.icon)) {
                continue;
            }
            seen.add(entry.icon);
            result.push(entry.icon);
        }
        return result;
    }

    get recent() {
        if (this.state.query.trim() || this.state.group !== "all") {
            return [];
        }
        return this.state.recent;
    }

    close() {
        if (this.props.onClose) {
            this.props.onClose();
        }
    }

    onQueryInput(ev) {
        this.state.query = ev.target.value;
    }

    onQueryKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            const first = this.matches[0];
            if (first) {
                this.select(first);
            }
        }
    }

    pickGroup(key) {
        this.state.group = key;
    }

    select(icon) {
        const recent = [icon, ...this.state.recent.filter((entry) => entry !== icon)].slice(0, RECENT_MAX);
        this.state.recent = recent;
        writeRecent(recent);
        this.props.onPick(icon);
        this.close();
    }
}
