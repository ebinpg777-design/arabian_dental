/** @odoo-module **/

import { Component, onWillStart, proxy, t, useProps } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";

// Odoo's twelve kanban colours, by the route's colour index (0 = none).
export const ROUTE_COLOURS = [
    "#6b7280", "#ef4444", "#f59e0b", "#eab308", "#06b6d4", "#8b5cf6",
    "#ec4899", "#14b8a6", "#3b82f6", "#f97316", "#22c55e", "#a855f7",
];

export function routeColour(index) {
    return ROUTE_COLOURS[(Number(index) || 0) % ROUTE_COLOURS.length];
}

export function isoToday() {
    return luxon.DateTime.local().toISODate();
}

export function isoPlus(iso, days) {
    return luxon.DateTime.fromISO(iso).plus({ days }).toISODate();
}

/** "Fri 7 Nov" or "Fri 7 – Sat 8 Nov", the way the server says it. */
export function spanWord(from, to) {
    const { DateTime } = luxon;
    const a = DateTime.fromISO(from);
    const b = DateTime.fromISO(to || from);
    if (!a.isValid) {
        return "";
    }
    if (!b.isValid || b.equals(a)) {
        return a.toFormat("ccc d LLL");
    }
    return `${a.toFormat("ccc d")} – ${b.toFormat("ccc d LLL")}`;
}

// The executive's words on My Day; the manager's on the roster.
const KIND_LINES = {
    handover: {
        cover: { title: _t("Cover for me"), sub: _t("I'm away - take my round") },
        assist: { title: _t("Come along"), sub: _t("I need a hand on it") },
    },
    assign: {
        regular: { title: _t("Assign"), sub: _t("put them on the route") },
        cover: { title: _t("Cover"), sub: _t("stand in for somebody away") },
        assist: { title: _t("Assist"), sub: _t("send them along with somebody") },
    },
};

/**
 * One dialog, two moods.
 *
 * On My Day an executive hands a round over or asks a colleague along
 * (`mode: "handover"`): their own routes, one colleague, some days, a word on
 * why; it goes as a request the colleague answers. On the roster a manager
 * assigns (`mode: "assign"`): many people on many routes for a spell, confirmed
 * at once, or a cover / an assist arranged on somebody's behalf.
 *
 * The people are offered in the order most likely to be right: whoever already
 * knows the chosen routes first, then the lightest day, then the name.
 */
export class RouteDutyDialog extends Component {
    static template = "lab_fieldwork.RouteDutyDialog";
    static components = { Dialog };
    props = useProps({
        close: t.function(),
        mode: t.selection(["handover", "assign"]).optional("handover"),
        presets: t.object().optional(),
        onDone: t.function().optional(),
    });

    setup() {
        this.orm = useService("orm");
        const p = this.props.presets || {};
        const from = p.dateFrom || isoToday();
        this.state = proxy({
            loading: true,
            busy: false,
            options: null,
            kind: p.kind || (this.props.mode === "assign" ? "regular" : "cover"),
            routeIds: [...(p.routes || [])],
            userIds: [...(p.users || [])],
            originId: p.originUserId || null,
            dateFrom: from,
            dateTo: p.dateTo || from,
            note: p.note || "",
            query: "",
        });
        onWillStart(() => this.load());
    }

    get isAssign() {
        return this.props.mode === "assign";
    }

    get title() {
        if (this.isAssign) {
            return _t("Put people on routes");
        }
        return this.state.kind === "assist" ? _t("Ask a colleague along") : _t("Hand my round over");
    }

    get kinds() {
        const lines = KIND_LINES[this.isAssign ? "assign" : "handover"];
        return Object.keys(lines).map((id) => ({ id, ...lines[id] }));
    }

    async load() {
        this.state.loading = true;
        const data = await this.orm.call("lab.route.duty", "dialog_options", [
            this.props.mode, this.state.dateFrom,
        ]);
        this.state.options = data;
        // Handing over: every route of theirs, unless the caller chose.
        if (!this.isAssign && !this.state.routeIds.length) {
            this.state.routeIds = data.routes.map((r) => r.id);
        }
        this.state.loading = false;
    }

    // ---------------------------------------------------------------- choices
    routeColour(index) {
        return routeColour(index);
    }

    setKind(kind) {
        this.state.kind = kind;
        if (kind === "regular") {
            this.state.originId = null;
        }
    }

    hasRoute(id) {
        return this.state.routeIds.includes(id);
    }

    toggleRoute(id) {
        const ids = this.state.routeIds;
        this.state.routeIds = ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id];
    }

    allRoutes() {
        const every = (this.state.options?.routes || []).map((r) => r.id);
        this.state.routeIds = this.state.routeIds.length === every.length ? [] : every;
    }

    hasPerson(id) {
        return this.state.userIds.includes(id);
    }

    togglePerson(id) {
        const ids = this.state.userIds;
        if (!this.isAssign) {
            this.state.userIds = ids.includes(id) ? [] : [id];    // one colleague
            return;
        }
        this.state.userIds = ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id];
    }

    setOrigin(ev) {
        const value = parseInt(ev.target.value, 10);
        this.state.originId = Number.isFinite(value) ? value : null;
    }

    onQuery(ev) {
        this.state.query = ev.target.value;
    }

    onNote(ev) {
        this.state.note = ev.target.value;
    }

    // ----------------------------------------------------------------- people
    get people() {
        const o = this.state.options;
        if (!o) {
            return [];
        }
        const q = this.state.query.trim().toLowerCase();
        const chosen = new Set(this.state.routeIds);
        const rows = o.people
            .filter((p) => !q || p.name.toLowerCase().includes(q))
            .filter((p) => p.id !== this.state.originId)
            .map((p) => {
                const knows = p.knows.filter((id) => chosen.has(id)).length;
                const standing = p.standing.filter((id) => chosen.has(id)).length;
                return {
                    ...p,
                    knowsChosen: knows,
                    standingChosen: standing,
                    loadWord: p.load
                        ? _t("%s route(s) that day", p.load)
                        : _t("free that day"),
                };
            });
        rows.sort((a, b) =>
            (b.knowsChosen - a.knowsChosen) || (a.load - b.load) || a.name.localeCompare(b.name));
        return rows;
    }

    get originChoices() {
        return (this.state.options?.people || []).filter((p) => !this.hasPerson(p.id));
    }

    initials(name) {
        return String(name || "")
            .split(/\s+/)
            .filter(Boolean)
            .slice(0, 2)
            .map((w) => w[0].toUpperCase())
            .join("");
    }

    // ------------------------------------------------------------------ dates
    quick(which) {
        const { DateTime } = luxon;
        const today = DateTime.local();
        let a = today;
        let b = today;
        if (which === "tomorrow") {
            a = b = today.plus({ days: 1 });
        } else if (which === "week") {
            b = today.endOf("week");                      // Sunday
        } else if (which === "next_week") {
            a = today.plus({ weeks: 1 }).startOf("week");
            b = a.plus({ days: 5 });                      // Monday to Saturday
        }
        this.state.dateFrom = a.toISODate();
        this.state.dateTo = b.toISODate();
        this.reloadLoad();
    }

    onDate(field, ev) {
        const value = ev.target.value;
        if (!value) {
            return;
        }
        this.state[field] = value;
        if (this.state.dateTo < this.state.dateFrom) {
            this.state[field === "dateFrom" ? "dateTo" : "dateFrom"] = value;
        }
        if (field === "dateFrom") {
            this.reloadLoad();
        }
    }

    /** The "free that day" words follow the first day chosen. */
    async reloadLoad() {
        const data = await this.orm.call("lab.route.duty", "dialog_options", [
            this.props.mode, this.state.dateFrom,
        ]);
        const load = Object.fromEntries(data.people.map((p) => [p.id, p.load]));
        for (const p of this.state.options.people) {
            p.load = load[p.id] ?? p.load;
        }
    }

    get spanWord() {
        return spanWord(this.state.dateFrom, this.state.dateTo);
    }

    // ---------------------------------------------------------------- summary
    nameOf(id) {
        return (this.state.options?.people || []).find((p) => p.id === id)?.name || "";
    }

    routeNames() {
        const o = this.state.options;
        return (o?.routes || []).filter((r) => this.hasRoute(r.id)).map((r) => r.name);
    }

    get summary() {
        const routes = this.routeNames();
        const people = this.state.userIds.map((id) => this.nameOf(id)).filter(Boolean);
        if (!routes.length || !people.length) {
            return "";
        }
        const who = people.join(", ");
        const what = routes.join(", ");
        const when = this.spanWord;
        if (!this.isAssign) {
            return this.state.kind === "cover"
                ? _t("Ask %s to cover %s, %s.", who, what, when)
                : _t("Ask %s to come along on %s, %s.", who, what, when);
        }
        const origin = this.nameOf(this.state.originId);
        if (this.state.kind === "cover") {
            return _t("%s cover(s) %s for %s, %s.", who, what, origin || "…", when);
        }
        if (this.state.kind === "assist") {
            return _t("%s assist(s) %s on %s, %s.", who, origin || "…", what, when);
        }
        return _t("%s on %s, %s.", who, what, when);
    }

    get canSubmit() {
        const s = this.state;
        if (s.loading || s.busy || !s.routeIds.length || !s.userIds.length) {
            return false;
        }
        if (this.isAssign && s.kind !== "regular" && !s.originId) {
            return false;
        }
        return true;
    }

    get submitLabel() {
        if (this.isAssign) {
            return _t("Assign");
        }
        return _t("Send the request");
    }

    async submit() {
        if (!this.canSubmit) {
            return;
        }
        const s = this.state;
        s.busy = true;
        try {
            let result;
            if (this.isAssign) {
                result = await this.orm.call("lab.route.duty", "assign", [
                    s.userIds, s.routeIds, s.dateFrom, s.dateTo, s.kind,
                    s.originId || false, s.note,
                ]);
            } else {
                result = await this.orm.call("lab.route.duty", "request_transfer", [
                    s.routeIds, s.userIds[0], s.dateFrom, s.dateTo, s.kind, s.note,
                ]);
            }
            this.props.onDone?.(result);
            this.props.close();
        } finally {
            s.busy = false;
        }
    }
}
