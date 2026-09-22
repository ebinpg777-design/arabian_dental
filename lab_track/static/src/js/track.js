/** @odoo-module **/

import { Component, onWillStart, useRef, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

/**
 * Track a Work — one box, one screen, everything.
 *
 * The whole design constraint is that this is used while somebody is waiting on the
 * phone. So: the search box holds focus, Enter is enough, every section is already
 * expanded, and nothing here needs a second request to fill in. Clicking is only ever
 * for going deeper into a record, never for revealing what is already known.
 */
export class LabTrack extends Component {
    static template = "lab_track.Track";
    static props = ["*"];
    // The Station Board's tracker: every name is plain text, nothing opens a record.
    static readonly = false;

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.input = useRef("search");
        this.state = useState({
            ref: "",
            data: null,
            matches: null,
            recent: [],
            // The floor as it stands: what each station holds right now.
            floor: null,
            floorBusy: false,
            // Which station's cases are open, and what they are.
            openStation: null,
            stationJobs: {},
            stationBusy: null,
            loading: false,
            searched: false,
            // Which stage chip is pressed. Filters the cards on screen; "" is all.
            stageFilter: "",
            // A refresh in flight, so the button can say so and cannot be pressed twice.
            refreshing: false,
        });
        onWillStart(async () => {
            if (this.readonly) {
                // Opened from a bench with a number already typed: track it at once.
                const ref = (this.props.ref || "").trim();
                if (ref) {
                    this.state.ref = ref;
                    await this.search(ref);
                }
                return;
            }
            this.state.floor = await this.orm.call("lab.track", "floor_now", []);
        });
    }

    get readonly() {
        return this.constructor.readonly;
    }

    /** A client action has no breadcrumb bar: on a bench tablet this is the way back. */
    get canGoBack() {
        return (this.env.config?.breadcrumbs?.length || 0) > 1;
    }

    goBack() {
        this.action.restore();
    }

    /** The same questions, asked through the floor's own door when read-only. */
    callTrack(method, args) {
        return this.orm.call(
            "lab.track", this.readonly ? `floor_${method}` : method, args);
    }

    async search(ref) {
        const term = (ref ?? this.state.ref).trim();
        if (!term) {
            return;
        }
        this.state.loading = true;
        this.state.matches = null;
        try {
            const result = await this.callTrack("search_work", [term]);
            this.state.searched = true;
            if (result.found) {
                this.state.data = result;
            } else if (result.matches) {
                // Ambiguous on purpose rather than guessing: picking the wrong case for
                // somebody reading a number down the phone is worse than one more tap.
                this.state.data = null;
                this.state.matches = result.matches;
            } else {
                this.state.data = null;
                this.notification.add(_t("Nothing found for “%s”.", term), {
                    type: "warning",
                });
            }
        } finally {
            this.state.loading = false;
        }
    }

    async open(orderId) {
        this.state.loading = true;
        try {
            this.state.matches = null;
            this.state.data = await this.callTrack("get_work", [orderId]);
            this.state.searched = true;
        } finally {
            this.state.loading = false;
        }
    }

    /**
     * Read the same case again.
     *
     * The screen is opened and left open - on a desk, beside a phone call - while
     * the floor keeps scanning. Re-running the search would need the reference
     * typed again and would land on the search results when the number is
     * ambiguous; this asks for the one case already on screen. What is showing
     * stays showing until the answer arrives, so nothing blanks under the reader.
     * (client, 2026-09-09)
     */
    /**
     * Read the floor again.
     *
     * The landing page is left open on a desk while the benches keep scanning,
     * so what it shows is a photograph. This takes another one, and says when
     * it was taken. (client, 2026-09-09)
     */
    async refreshFloor() {
        if (this.state.floorBusy) {
            return;
        }
        this.state.floorBusy = true;
        try {
            this.state.floor = await this.orm.call("lab.track", "floor_now", []);
            // A reading taken again is a reading of the jobs too.
            this.state.stationJobs = {};
            if (this.state.openStation) {
                const open = this.state.openStation;
                this.state.openStation = null;
                await this.toggleStation({ id: open });
            }
        } finally {
            this.state.floorBusy = false;
        }
    }

    /**
     * Open one station and read its cases.
     *
     * Fetched on the tap, not with the page: twelve stations of six rows is a
     * payload nobody reads, and the number that matters - how many are there -
     * is already on the row. Kept once fetched, so closing and reopening a
     * station costs nothing. (client, 2026-09-09)
     */
    async toggleStation(station) {
        if (this.state.openStation === station.id) {
            this.state.openStation = null;
            return;
        }
        this.state.openStation = station.id;
        if (this.state.stationJobs[station.id]) {
            return;
        }
        this.state.stationBusy = station.id;
        try {
            this.state.stationJobs[station.id] = await this.orm.call(
                "lab.track", "station_jobs", [station.id]);
        } finally {
            this.state.stationBusy = null;
        }
    }

    stationJobs(id) {
        return this.state.stationJobs[id] || [];
    }

    /** "3 h" beside a job, or nothing at all while it is fresh. */
    waited(hours) {
        if (!hours) {
            return "";
        }
        if (hours < 24) {
            return _t("%s h", hours);
        }
        const days = Math.floor(hours / 24);
        return days === 1 ? _t("1 day") : _t("%s days", days);
    }

    async refresh() {
        const current = this.state.data && this.state.data.order;
        if (!current || this.state.refreshing) {
            return;
        }
        this.state.refreshing = true;
        try {
            const fresh = await this.callTrack("get_work", [current.id]);
            if (fresh && fresh.found) {
                this.state.data = fresh;
            } else {
                this.notification.add(_t("That case can no longer be read."), {
                    type: "warning",
                });
            }
        } finally {
            this.state.refreshing = false;
        }
    }

    onKeydown(ev) {
        if (ev.key === "Enter") {
            clearTimeout(this._liveTimer);
            this.search();
        }
    }

    /**
     * Search as the person types, debounced. Enter still resolves fully - the
     * difference is that live results never JUMP to a case on their own: while
     * somebody is typing, the list narrowing down is the helpful behaviour, and
     * being teleported to the wrong record is not.
     */
    onInput() {
        clearTimeout(this._liveTimer);
        const term = this.state.ref.trim();
        if (term.length < 2) {
            this.state.matches = null;
            this.state.searched = false;
            return;
        }
        this._liveTimer = setTimeout(() => this.liveSearch(term), 300);
    }

    async liveSearch(term) {
        this._liveToken = (this._liveToken || 0) + 1;
        const token = this._liveToken;
        const rows = await this.callTrack("live_search", [term]);
        if (token !== this._liveToken || this.state.ref.trim() !== term) {
            return; // a newer keystroke owns the screen now
        }
        this.state.data = null;
        this.state.matches = rows;
        this.state.searched = true;
    }

    /** The cards to show, after the stage chip. */
    get visibleMatches() {
        const rows = this.state.matches || [];
        return this.state.stageFilter
            ? rows.filter((r) => r.stage === this.state.stageFilter)
            : rows;
    }

    get visibleRecent() {
        const rows = this.state.recent || [];
        return this.state.stageFilter
            ? rows.filter((r) => r.stage === this.state.stageFilter)
            : rows;
    }

    setStageFilter(stage) {
        this.state.stageFilter = this.state.stageFilter === stage ? "" : stage;
    }

    get stageFilters() {
        return [
            ["in_lab", "In the lab"],
            ["ready", "Ready"],
            ["sent", "Out for delivery"],
            ["delivered", "Delivered"],
        ];
    }

    clear() {
        clearTimeout(this._liveTimer);
        this.state.ref = "";
        this.state.stageFilter = "";
        this.state.data = null;
        this.state.matches = null;
        this.state.searched = false;
        if (this.input.el) {
            this.input.el.focus();
        }
    }

    get order() {
        return this.state.data ? this.state.data.order : null;
    }

    // --- drill-downs. The only clicks on this screen.
    openRecord(model, resId) {
        if (this.readonly) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: model,
            res_id: resId,
            views: [[false, "form"]],
        });
    }

    stageClass(stage) {
        return (
            {
                registered: "o_trk_stage_registered",
                in_lab: "o_trk_stage_lab",
                ready: "o_trk_stage_ready",
                sent: "o_trk_stage_sent",
                delivered: "o_trk_stage_delivered",
                cancel: "o_trk_stage_cancel",
            }[stage] || ""
        );
    }

    woClass(state) {
        return (
            { done: "o_trk_wo_done", progress: "o_trk_wo_now", ready: "o_trk_wo_ready" }[
                state
            ] || "o_trk_wo_todo"
        );
    }
}

registry.category("actions").add("lab_track", LabTrack);

export class LabTrackReadonly extends LabTrack {
    static readonly = true;
}

registry.category("actions").add("lab_track_readonly", LabTrackReadonly);
