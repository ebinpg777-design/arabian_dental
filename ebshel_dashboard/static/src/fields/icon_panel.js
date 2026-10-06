/** @odoo-module **/

import { Component, proxy, signal, t, useListener, useProps } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";

const RECENT_KEY = "ebshel_dashboard.recent_icons";
const RECENT_MAX = 12;

export const ICON_GROUPS = [
    { key: "analytics", label: _t("Analytics"), icons: [
        ["bar_chart", "bars chart bar"], ["show_chart", "line trend chart"],
        ["area_chart", "area chart"], ["pie_chart", "pie share chart"],
        ["speed", "gauge speed dashboard tachometer"], ["signal_cellular_4_bar", "signal"],
        ["trophy", "win award trophy"], ["radio_button_checked", "target goal bullseye"],
        ["my_location", "target aim crosshairs"], ["percent", "ratio percent"],
        ["tag", "number count hashtag"],
        ["format_list_numbered", "rank order sort numeric asc list ranked ol"],
        ["table", "table grid"], ["grid_view", "grid tiles th large"],
        ["checklist", "progress tasks"], ["filter_alt", "funnel filter"],
        ["wand_stars", "auto wand magic"], ["rocket_launch", "launch growth rocket"],
        ["lightbulb", "idea lightbulb"], ["whatshot", "hot urgent fire"],
        ["diamond", "premium diamond"], ["flash_on", "bolt fast flash"],
    ]},
    { key: "status", label: _t("Status"), icons: [
        ["check_circle", "done ok approved check circle"], ["check_box", "done check square"],
        ["cancel", "cancel failed times circle"], ["block", "blocked forbidden ban"],
        ["warning", "warning alert exclamation triangle"],
        ["error", "important exclamation circle"], ["help", "unknown question circle"],
        ["info", "info circle"], ["hourglass_top", "waiting pending hourglass half"],
        ["schedule", "time late clock"], ["history", "history past"], ["refresh", "refresh sync"],
        ["progress_activity", "in progress spinner"], ["flag", "flag"],
        ["sports_score", "finish flag checkered"], ["thumb_up", "good like thumbs up"],
        ["thumb_down", "bad dislike thumbs down"], ["star", "favourite star"],
        ["favorite", "love heart"], ["notifications", "notification alarm bell"],
        ["campaign", "announce bullhorn"], ["lock", "locked lock"], ["lock_open", "open unlock"],
        ["circle", "dot light circle"], ["pause_circle", "paused pause circle"],
        ["play_circle", "running play circle"], ["stop_circle", "stopped stop circle"],
    ]},
    { key: "money", label: _t("Money"), icons: [
        ["payments", "cash revenue money"], ["credit_card", "card payment credit"],
        ["account_balance", "bank"], ["calculate", "calc calculator"],
        ["attach_money", "dollar usd"], ["euro", "euro eur"], ["currency_pound", "pound gbp"],
        ["currency_rupee", "rupee inr"], ["currency_yen", "yen jpy"],
        ["currency_bitcoin", "bitcoin btc"], ["sell", "price tag"], ["label", "tags"],
        ["shopping_cart", "cart sales shopping"], ["shopping_basket", "basket shopping"],
        ["shopping_bag", "bag shopping"], ["card_giftcard", "gift"], ["balance", "balance scale"],
        ["show_chart", "revenue line chart"], ["pie_chart", "share pie chart"],
        ["description", "invoice file text"], ["handshake", "deal handshake"],
        ["work", "business briefcase"],
    ]},
    { key: "documents", label: _t("Documents"), icons: [
        ["description", "document file text"], ["article", "file"], ["content_copy", "files"],
        ["folder_open", "folder open"], ["assignment", "clipboard"], ["edit", "edit draft pencil"],
        ["edit_square", "edit pencil square"], ["attach_file", "attachment paperclip"],
        ["print", "print"], ["mail", "mail envelope"], ["send", "sent paper plane"],
        ["inbox", "inbox"], ["archive", "archive"], ["sticky_note_2", "note sticky"],
        ["book", "book"], ["newspaper", "news newspaper"], ["list_alt", "form list alt"],
        ["picture_as_pdf", "pdf file"], ["table_chart", "spreadsheet file excel"],
        ["format_quote", "quote left"], ["mode_comment", "comment commenting"],
    ]},
    { key: "logistics", label: _t("Logistics"), icons: [
        ["local_shipping", "delivery shipping truck"], ["deployed_code", "product unit cube"],
        ["inventory_2", "stock inventory cubes"], ["factory", "factory manufacturing industry"],
        ["build", "repair maintenance wrench"], ["settings", "settings machine cogs gear cog"],
        ["barcode", "barcode"], ["qr_code", "qr qrcode"], ["recycling", "recycle"],
        ["location_on", "location map marker"], ["map", "map"], ["public", "world country globe"],
        ["flight", "flight plane"], ["directions_boat", "sea ship"], ["train", "rail train"],
        ["directions_bike", "bike bicycle"], ["directions_car", "car"], ["road", "route road"],
        ["package_2", "box dropbox"], ["swap_horiz", "transfer exchange"],
        ["shuffle", "route random"], ["battery_5_bar", "battery three quarters"],
    ]},
    { key: "people", label: _t("People"), icons: [
        ["person", "user person"], ["group", "team group users"], ["person_add", "new user plus"],
        ["account_circle", "avatar user circle"], ["oi_user-secret", "anonymous user secret"],
        ["badge", "contact address card employee id badge"],
        ["contact_page", "contacts address book"], ["handshake", "partner handshake"],
        ["forum", "chat discussion comments"], ["chat_bubble", "message comment"],
        ["phone", "call phone"], ["headphones", "support headphones"], ["child_care", "child"],
        ["female", "woman female"], ["male", "man male"], ["sentiment_satisfied", "happy smile"],
        ["sentiment_dissatisfied", "unhappy frown"], ["sentiment_neutral", "neutral meh"],
        ["school", "training graduation cap"], ["cake", "birthday cake"],
    ]},
    { key: "time", label: _t("Time & Places"), icons: [
        ["calendar_today", "calendar"], ["event_available", "planned calendar check"],
        ["event_busy", "missed calendar times"], ["calendar_add_on", "schedule calendar plus"],
        ["hourglass_bottom", "deadline hourglass end"], ["schedule", "clock"],
        ["business", "company office building"], ["home", "home"], ["work", "work briefcase"],
        ["luggage", "travel suitcase"], ["security", "protected shield"],
        ["stethoscope", "health stethoscope"], ["medical_services", "medical medkit"],
        ["local_hospital", "hospital"], ["account_balance", "school university"],
        ["bed", "hotel bed"], ["coffee", "break coffee"], ["restaurant", "food cutlery"],
        ["light_mode", "day sun"], ["dark_mode", "night moon"], ["umbrella", "leave umbrella"],
    ]},
    { key: "tech", label: _t("Technology"), icons: [
        ["laptop", "laptop"], ["desktop_windows", "desktop"], ["smartphone", "phone mobile"],
        ["tablet", "tablet"], ["dns", "server"], ["database", "database records"],
        ["cloud", "cloud"], ["cloud_upload", "upload cloud"], ["cloud_download", "download cloud"],
        ["wifi", "network wifi"], ["power", "integration plug"], ["code", "code"],
        ["bug_report", "bug issue"], ["terminal", "shell terminal"], ["key", "key access"],
        ["memory", "chip microchip"], ["storage", "disk hdd"],
        ["account_tree", "structure sitemap"], ["link", "link"], ["share", "share alt"],
        ["rss_feed", "feed rss"], ["photo_camera", "photo camera"],
    ]},
];

const ALL_ICONS = ICON_GROUPS.flatMap((group) =>
    group.icons.map(([icon, words]) => ({ icon, words: `${icon.replace(/^oi_/, "").replace(/[_-]/g, " ")} ${words}`, group: group.key }))
);

function readRecent() {
    try {
        const raw = window.localStorage.getItem(RECENT_KEY);
        const list = raw ? JSON.parse(raw) : [];
        // Names picked under 19.0 were FontAwesome classes, which Odoo 20 cannot draw.
        return Array.isArray(list)
            ? list.filter((icon) => typeof icon === "string" && !icon.startsWith("fa"))
            : [];
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
    props = useProps({
        current: t.string().optional(),
        onPick: t.function(),
        onClose: t.function().optional(),
        anchor: t.string().optional(),
    });
    rootRef = signal.ref();
    searchRef = signal.ref();

    setup() {
        this.state = proxy({ query: "", group: "all", recent: readRecent() });
        window.setTimeout(() => this.searchRef() && this.searchRef().focus(), 0);
        useListener(window, "click", (ev) => {
            // The click that opened the panel is still travelling: only a click
            // outside both the panel and its anchor closes it.
            const anchor = this.props.anchor && ev.target.closest && ev.target.closest(this.props.anchor);
            if (!anchor && this.rootRef() && !this.rootRef().contains(ev.target)) {
                this.close();
            }
        });
        useListener(window, "keydown", (ev) => {
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
