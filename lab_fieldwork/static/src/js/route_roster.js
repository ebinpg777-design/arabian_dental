/** @odoo-module **/

import { Component, onWillStart, proxy, usePlugin, useProps } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { NotificationPlugin } from "@web/core/notifications/notification_plugin";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { RouteDutyDialog, isoPlus, isoToday, routeColour, spanWord } from "@lab_fieldwork/js/route_duty_dialog";

const KIND_ICONS = { standing: "fa-circle", regular: "fa-thumb-tack", cover: "fa-plane", assist: "fa-user-plus" };
const KIND_WORDS = {
    standing: _t("their own route"), regular: _t("assigned"), cover: _t("covering"), assist: _t("assisting"),
};

/**
 * Route Roster (2026-11-07): the manager's grid of who is on which sales route,
 * day by day, for a week or a fortnight.
 *
 * By route: a row per route, a column per day, the people on it as chips -
 * standing members faint, assigned / covering / assisting solid, a request not
 * yet answered dashed, somebody away struck through with who covers them. A
 * cell nobody is on is amber. By person: the same grid turned round, a person's
 * heavy days amber. Drag a person from the rail onto a cell to put them on that
 * route that day; drag a chip to another cell to move the duty; tap a chip for
 * what it is and the buttons; tap the + for the full dialog.
 */
export class RouteRoster extends Component {
    static template = "lab_fieldwork.RouteRoster";
    props = useProps();

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.notification = usePlugin(NotificationPlugin);
        const today = isoToday();
        this.state = proxy({
            loading: true,
            data: null,
            from: luxon.DateTime.fromISO(today).startOf("week").toISODate(),   // Monday
            days: 7,
            view: "routes",          // "routes" | "people"
            rail: "requests",        // "requests" | "away" | "people"
            railOpen: true,
            query: "",
            focus: null,             // { key, chip }
            dragging: null,
            busy: false,
        });
        onWillStart(() => this.load());
    }

    get to() {
        return isoPlus(this.state.from, this.state.days - 1);
    }

    get spanWord() {
        return spanWord(this.state.from, this.to);
    }

    get isThisWeek() {
        return this.state.from === luxon.DateTime.fromISO(isoToday()).startOf("week").toISODate();
    }

    async load() {
        this.state.loading = true;
        this.state.focus = null;
        this.state.data = await this.orm.call("lab.route.duty", "get_roster", [this.state.from, this.to]);
        this.state.loading = false;
    }

    // ----------------------------------------------------------------- navigation
    shift(weeks) {
        this.state.from = isoPlus(this.state.from, 7 * weeks);
        this.load();
    }

    thisWeek() {
        this.state.from = luxon.DateTime.fromISO(isoToday()).startOf("week").toISODate();
        this.load();
    }

    pickDate(iso) {
        if (!iso) {
            return;
        }
        this.state.from = luxon.DateTime.fromISO(iso).startOf("week").toISODate();
        this.load();
    }

    setDays(n) {
        this.state.days = n;
        this.load();
    }

    setView(view) {
        this.state.view = view;
        this.state.focus = null;
    }

    setRail(rail) {
        this.state.rail = rail;
        this.state.railOpen = true;
    }

    toggleRail() {
        this.state.railOpen = !this.state.railOpen;
    }

    onQuery(ev) {
        this.state.query = ev.target.value;
    }

    // --------------------------------------------------------------------- reading
    routeColour(index) {
        return routeColour(index);
    }

    get routes() {
        const d = this.state.data;
        if (!d) {
            return [];
        }
        const q = this.state.query.trim().toLowerCase();
        return d.routes.filter((r) => !q || r.name.toLowerCase().includes(q));
    }

    get people() {
        const d = this.state.data;
        if (!d) {
            return [];
        }
        const q = this.state.query.trim().toLowerCase();
        return d.people.filter((p) => !q || p.name.toLowerCase().includes(q));
    }

    person(id) {
        return (this.state.data?.people || []).find((p) => p.id === id) || { id, name: "?", avatar: "", colour: "#999" };
    }

    route(id) {
        return (this.state.data?.routes || []).find((r) => r.id === id) || { id, name: "?", color: 0 };
    }

    duty(id) {
        return (this.state.data?.duties || []).find((x) => x.id === id);
    }

    firstName(name) {
        return String(name || "").split(/\s+/)[0];
    }

    key(teamId, iso) {
        return `${teamId}|${iso}`;
    }

    cell(teamId, iso) {
        return this.state.data?.cells[this.key(teamId, iso)] || [];
    }

    isUncovered(teamId, iso) {
        return (this.state.data?.uncovered || []).includes(this.key(teamId, iso));
    }

    isHeavy(userId, iso) {
        return (this.state.data?.heavy || []).includes(`${userId}|${iso}`);
    }

    /** The person view: every route this person is on that day. */
    personCell(userId, iso) {
        const d = this.state.data;
        if (!d) {
            return [];
        }
        const out = [];
        for (const r of d.routes) {
            for (const chip of this.cell(r.id, iso)) {
                if (chip.user_id === userId) {
                    out.push({ ...chip, team_id: r.id });
                }
            }
        }
        return out;
    }

    chipClass(chip) {
        return {
            [`o_fw_rr_chip_${chip.kind}`]: true,
            o_fw_rr_chip_pending: chip.state === "pending",
            o_fw_rr_chip_away: chip.away,
            o_fw_rr_chip_focus: this.state.focus && this.state.focus.chip === chip,
        };
    }

    chipTitle(chip) {
        const p = this.person(chip.user_id);
        if (chip.away) {
            return _t("%s is away - %s covers", p.name, chip.covered_by);
        }
        const duty = chip.duty_id ? this.duty(chip.duty_id) : null;
        let word = KIND_WORDS[chip.kind] || chip.kind;
        if (duty && duty.origin) {
            word += ` ${duty.kind === "cover" ? _t("for") : _t("with")} ${duty.origin}`;
        }
        if (chip.state === "pending") {
            word += ` · ${_t("not answered yet")}`;
        }
        return `${p.name} · ${word}`;
    }

    kindIcon(kind) {
        return KIND_ICONS[kind] || "fa-circle";
    }

    kindWord(kind) {
        return KIND_WORDS[kind] || kind;
    }

    dayClass(day) {
        return { o_fw_rr_today: day.is_today, o_fw_rr_off: day.off, o_fw_rr_past: day.past };
    }

    // ----------------------------------------------------------------- the focus
    focusChip(ev, chip, teamId, iso) {
        ev.stopPropagation();
        if (this.state.focus && this.state.focus.chip === chip) {
            this.state.focus = null;
            return;
        }
        this.state.focus = { chip, teamId, iso, key: this.key(teamId, iso), duty: chip.duty_id ? this.duty(chip.duty_id) : null };
    }

    closeFocus() {
        this.state.focus = null;
    }

    isFocusCell(teamId, iso) {
        return this.state.focus && this.state.focus.key === this.key(teamId, iso);
    }

    isFocusPersonCell(userId, iso) {
        const f = this.state.focus;
        return f && f.iso === iso && f.chip.user_id === userId;
    }

    // ---------------------------------------------------------------- the actions
    openAssign(presets = {}) {
        // The week shown, unless it has already begun: then today.
        const today = isoToday();
        const start = this.state.from < today ? today : this.state.from;
        this.dialog.add(RouteDutyDialog, {
            mode: "assign",
            presets: { dateFrom: start, dateTo: start, ...presets },
            onDone: (result) => this.afterAssign(result),
        });
    }

    assignHere(teamId, iso) {
        this.openAssign({ routes: [teamId], dateFrom: iso, dateTo: iso });
    }

    assignPerson(userId, iso) {
        this.openAssign({ users: [userId], dateFrom: iso, dateTo: iso });
    }

    assignHereClick(ev, teamId, iso) {
        ev.stopPropagation();
        this.assignHere(teamId, iso);
    }

    assignPersonClick(ev, userId, iso) {
        ev.stopPropagation();
        this.assignPerson(userId, iso);
    }

    afterAssign(result) {
        if (result && result.count) {
            this.notification.add(_t("%s duty(ies) arranged.", result.count), { type: "success" });
        }
        for (const line of (result && result.skipped) || []) {
            this.notification.add(line, { type: "warning" });
        }
        this.load();
    }

    /** From a standing chip: this person's away, somebody covers. */
    coverFor(chip, teamId, iso) {
        this.openAssign({ kind: "cover", routes: [teamId], originUserId: chip.user_id, dateFrom: iso, dateTo: iso });
    }

    assistFor(chip, teamId, iso) {
        this.openAssign({ kind: "assist", routes: [teamId], originUserId: chip.user_id, dateFrom: iso, dateTo: iso });
    }

    async answer(duty, accept) {
        await this.call(accept ? "action_accept" : "action_decline", duty.id);
        this.notification.add(accept ? _t("Accepted on their behalf.") : _t("Declined."), { type: "success" });
    }

    cancelDuty(duty) {
        this.dialog.add(ConfirmationDialog, {
            title: _t("End this duty?"),
            body: duty.kind === "cover"
                ? _t("%s stops covering %s on %s, and the planned visits go back to %s.", duty.user, duty.route, duty.span, duty.origin)
                : _t("%s comes off %s, %s.", duty.user, duty.route, duty.span),
            confirmLabel: _t("End it"),
            confirm: () => this.call("action_cancel", duty.id),
            cancel: () => {},
        });
    }

    async call(method, id) {
        if (this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            await this.orm.call("lab.route.duty", method, [[id]]);
            await this.load();
        } finally {
            this.state.busy = false;
        }
    }

    openDuty(duty) {
        this.action.doAction({
            type: "ir.actions.act_window", res_model: "lab.route.duty", res_id: duty.id,
            views: [[false, "form"]], target: "current",
        });
    }

    openDuties() {
        this.action.doAction("lab_fieldwork.action_route_duty");
    }

    // --------------------------------------------------------------------- drag
    dragPerson(ev, userId) {
        ev.dataTransfer.setData("text/plain", JSON.stringify({ user_id: userId }));
        ev.dataTransfer.effectAllowed = "copy";
        this.state.dragging = { user_id: userId };
    }

    dragChip(ev, chip) {
        if (!chip.duty_id) {
            // A standing member is not a duty; dragging them makes a day's assignment.
            return this.dragPerson(ev, chip.user_id);
        }
        ev.dataTransfer.setData("text/plain", JSON.stringify({ duty_id: chip.duty_id }));
        ev.dataTransfer.effectAllowed = "move";
        this.state.dragging = { duty_id: chip.duty_id };
    }

    dragEnd() {
        this.state.dragging = null;
    }

    async drop(ev, teamId, iso) {
        ev.preventDefault();
        let payload = null;
        try {
            payload = JSON.parse(ev.dataTransfer.getData("text/plain") || "null");
        } catch {
            payload = null;
        }
        this.state.dragging = null;
        if (!payload || this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            if (payload.user_id) {
                const result = await this.orm.call("lab.route.duty", "assign", [
                    [payload.user_id], [teamId], iso, iso, "regular", false, "",
                ]);
                this.afterAssign(result);
                return;
            }
            if (payload.duty_id) {
                const moved = await this.orm.call("lab.route.duty", "move_duty", [payload.duty_id, teamId, iso]);
                if (moved) {
                    this.notification.add(_t("Moved."), { type: "success" });
                }
                await this.load();
            }
        } finally {
            this.state.busy = false;
        }
    }

    /** The person view's drop: that person on the dropped route? No - a person
        row has no route; dropping a route chip there is not a thing. */
    allowDrop(ev) {
        ev.preventDefault();
        ev.dataTransfer.dropEffect = this.state.dragging && this.state.dragging.duty_id ? "move" : "copy";
    }
}

registry.category("actions").add("lab_route_roster", RouteRoster);
