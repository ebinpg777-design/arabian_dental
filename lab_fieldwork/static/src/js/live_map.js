/** @odoo-module **/

import { Component, onMounted, onWillStart, onWillUnmount, proxy, signal, useProps } from "@odoo/owl";
import { useHotkey } from "@web/core/hotkeys/hotkey_hook";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";

// Polling cadence until the payload says otherwise (config.live_ms).
const DEFAULT_LIVE_MS = 20000;
// The replay clock: two minutes of the day for every 150 ms on screen, so a
// nine-hour round plays in about forty seconds.
const REPLAY_STEP_S = 120;
const REPLAY_TICK_MS = 150;
// Clinic radius rings only from this zoom in: at town scale they are confetti.
const RING_ZOOM = 15;
// Follow mode pans once the marker leaves the inner 80% of the view.
const FOLLOW_PAD = -0.1;
const MOBILE = "(max-width: 767.98px)";

// The same four door readings the day sheet uses, so one colour means one
// thing wherever the day is shown (see day_map.js colourFor).
const DOOR_COLOURS = { ok: "#0f9d58", far: "#dc3545", nofix: "#d99b00", nopin: "#2563eb" };
const DOOR_LABELS = {
    ok: _t("at the door"), far: _t("away from the clinic"),
    nofix: _t("no fix"), nopin: _t("clinic not pinned"),
};
// Status colours for the badges, the legend and the marker rings.
const STATUS_COLOURS = {
    moving: "#0f9d58", at_clinic: "#2563eb", stopped: "#d99b00",
    offline: "#6c757d", off: "#ced4da",
};
const STATUS_RANK = { moving: 0, at_clinic: 1, stopped: 2, offline: 3, off: 4 };
const HALT_COLOUR = "#fd7e14";
const OVERSPEED_COLOUR = "#dc3545";

function esc(value) {
    return String(value ?? "").replace(
        /[&<>"']/g,
        (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]
    );
}

/** HH:MM in the browser's zone — every epoch in the payload is UTC seconds. */
function clock(epoch) {
    if (!epoch && epoch !== 0) {
        return "";
    }
    return new Date(epoch * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function pad(n) {
    return String(n).padStart(2, "0");
}

function localIso(date) {
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** "A B" → "AB"; one word → its first two letters. */
function initials(name) {
    const words = String(name || "").trim().split(/\s+/).filter(Boolean);
    if (!words.length) {
        return "?";
    }
    if (words.length === 1) {
        return words[0].slice(0, 2).toUpperCase();
    }
    return (words[0][0] + words[words.length - 1][0]).toUpperCase();
}

/**
 * Every field executive on one map, live.
 *
 * Odoo Community ships no map view, so this is the module's own Leaflet, the same
 * vendored copy the day sheet's track uses. The payload comes from one server call
 * (`lab.location.ping.get_live_map`) that already decided everything that matters —
 * who is on duty, who is moving, who went quiet, where the doors were — so the
 * browser only draws. Nothing here infers a status from coordinates.
 *
 * Today is polled while somebody is looking at it; a past day is loaded once and
 * can be replayed with a slider. The two are the same drawing with a different
 * clock: during replay each executive is shown where their trail was at the chosen
 * minute, and everything that happened later is not yet on the map.
 *
 * Layers are updated in place rather than rebuilt on every poll: a marker that
 * already exists is moved with setLatLng, so a popup a manager opened on it stays
 * open while the person underneath it keeps walking. (client, 2026-10-19)
 */
export class LiveMap extends Component {
    static template = "lab_fieldwork.LiveMap";
    props = useProps(standardActionServiceProps);

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.mapRef = signal.ref();
        // Context keys from the caller: a desk row opens the map on one person.
        const action = this.props.action || {};
        const ctx = Object.assign({}, action.params || {}, action.context || {});
        const mobile = window.matchMedia(MOBILE).matches;
        this.state = proxy({
            data: null,
            day: ctx.day || "",          // "" = today, decided by the server
            isToday: true,
            loading: true,
            lost: false,                 // the last call failed; the drawing is kept
            updatedAt: "",
            focusId: ctx.user_id || null,
            follow: Boolean(ctx.user_id),
            show: "duty",                // "duty" | "all"
            panelOpen: !mobile,          // the bottom sheet starts closed on a phone
            replayAt: null,              // epoch seconds while replaying, null = live/final
            playing: false,
            range: null,                 // {min, max} epochs of the day's recording
            fullWindow: false,           // the map over the whole browser window
        });
        this.map = null;
        this.tiles = null;
        this.exLayers = new Map();       // user_id -> the executive's Leaflet layers
        this.clinicLayers = new Map();   // clinic id -> {dot, ring}
        this.ringGroup = null;
        this.fitted = false;             // the first framing of the day is still owed
        this.viewSet = false;            // Leaflet has a centre (getBounds would throw before)
        this.touchedShow = false;        // the manager picked a Show filter by hand
        this.loadedAt = Date.now();
        this.pollTimer = null;
        this.playTimer = null;
        this.resizeObserver = null;
        this.fullLink = null;

        onWillStart(() => this.load());
        // Esc leaves the full window - a dialog opened over the map closes first.
        useHotkey("escape", () => this.setFullWindow(false),
                  { isAvailable: () => this.state.fullWindow });

        // Live only while somebody is looking: a wall board on a sleeping tab must
        // not poll all day for nobody (same rule as the Ops Desk).
        this.onVisibility = () => {
            if (document.visibilityState === "visible") {
                this.tick();
            }
        };
        onMounted(() => {
            this.createMap();
            this.redraw();
            this.startPolling();
            document.addEventListener("visibilitychange", this.onVisibility);
        });
        onWillUnmount(() => {
            this.stopPolling();
            clearInterval(this.playTimer);
            document.removeEventListener("visibilitychange", this.onVisibility);
            if (this.resizeObserver) {
                this.resizeObserver.disconnect();
                this.resizeObserver = null;
            }
            if (this.map) {
                this.map.remove();
                this.map = null;
            }
        });
    }

    // ------------------------------------------------------------------ data
    async load(quiet = false) {
        if (!quiet) {
            this.state.loading = true;
        }
        try {
            const data = await this.orm.call("lab.location.ping", "get_live_map", [], {
                day: this.state.day || null,
                user_ids: null,
            });
            this.state.data = data;
            this.state.day = data.day;
            this.state.isToday = Boolean(data.is_today);
            this.state.lost = false;
            this.loadedAt = Date.now();
            this.state.range = this.rangeOf(data);
            // A past day defaults to everyone: "on duty" is a live question.
            if (!data.is_today && !quiet && this.state.show === "duty" && !this.touchedShow) {
                this.state.show = "all";
            }
            this.state.updatedAt = new Date().toLocaleTimeString(
                [], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
        } catch (error) {
            // Keep the last drawing: a manager would rather see where everybody
            // was a minute ago than a blank map with an Oops dialog.
            this.state.lost = true;
            if (!this.state.data) {
                this.state.data = {
                    day: this.state.day, is_today: true, now_epoch: Math.floor(Date.now() / 1000),
                    tiles: "", attribution: "", config: {}, executives: [], clinics: [],
                };
            }
            console.warn("live map: reload failed", error?.message || error);
        } finally {
            this.state.loading = false;
        }
    }

    /** The day's recorded span, for the replay slider. */
    rangeOf(data) {
        let min = Infinity;
        let max = -Infinity;
        for (const ex of data.executives || []) {
            for (const leg of ex.legs || []) {
                if (leg.length) {
                    min = Math.min(min, leg[0][2]);
                    max = Math.max(max, leg[leg.length - 1][2]);
                }
            }
            for (const door of ex.doors || []) {
                min = Math.min(min, door.epoch);
                max = Math.max(max, door.left_epoch || door.epoch);
            }
        }
        return min < max ? { min: Math.floor(min), max: Math.ceil(max) } : null;
    }

    get config() {
        return (this.state.data && this.state.data.config) || {};
    }

    /** The server's clock, carried forward since the last load. */
    nowEpoch() {
        const base = this.state.data ? this.state.data.now_epoch : Math.floor(Date.now() / 1000);
        return base + Math.floor((Date.now() - this.loadedAt) / 1000);
    }

    // --------------------------------------------------------------- polling
    startPolling() {
        this.stopPolling();
        const ms = Number(this.config.live_ms) || DEFAULT_LIVE_MS;
        this.pollTimer = setInterval(() => this.tick(), ms);
    }

    stopPolling() {
        if (this.pollTimer) {
            clearInterval(this.pollTimer);
            this.pollTimer = null;
        }
    }

    async tick() {
        if (!this.state.isToday || this.state.loading || this.state.replayAt !== null
            || document.visibilityState !== "visible") {
            return;
        }
        await this.load(true);
        this.redraw();
    }

    // --------------------------------------------------------------- the map
    createMap() {
        const L = window.L;
        const el = this.mapRef();
        if (!L || !el) {
            return;
        }
        const map = L.map(el, {
            zoomControl: true, scrollWheelZoom: true, attributionControl: true,
        });
        this.map = map;
        // A view BEFORE any layer: a path added to a map with no centre yet is
        // drawn by a renderer that has not measured itself, and Leaflet throws
        // from deep inside ("reading 'intersects'") when the deferred adds run.
        // The world is the placeholder; the first payload re-frames it.
        map.setView([20, 0], 2);
        this.viewSet = true;
        this.ensureTiles();
        this.ringGroup = L.layerGroup();
        // Dragging the map is the manager taking the view back: follow stops.
        map.on("dragstart", () => {
            this.state.follow = false;
        });
        map.on("zoomend", () => this.toggleRings());

        // "Show everyone" — a way back after zooming into one person.
        const ShowAll = L.Control.extend({
            options: { position: "topleft" },
            onAdd: () => {
                const box = L.DomUtil.create("div", "leaflet-bar o_fw_live_reset");
                const link = L.DomUtil.create("a", "", box);
                link.href = "#";
                link.title = _t("Show everyone");
                link.innerHTML = '<i class="fa fa-arrows-alt"/>';
                L.DomEvent.on(link, "click", (ev) => {
                    L.DomEvent.stop(ev);
                    this.clearFocus();
                });
                return box;
            },
        });
        map.addControl(new ShowAll());

        // The map over the whole window, for a wall screen or a phone, and back.
        // The resize observer below re-measures it either way. (client, 2026-10-22)
        const FullWindow = L.Control.extend({
            options: { position: "topleft" },
            onAdd: () => {
                const box = L.DomUtil.create("div", "leaflet-bar o_fw_live_full");
                const link = L.DomUtil.create("a", "", box);
                link.href = "#";
                link.setAttribute("role", "button");
                this.fullLink = link;
                this.paintFullLink();
                L.DomEvent.on(link, "click", (ev) => {
                    L.DomEvent.stop(ev);
                    this.setFullWindow(!this.state.fullWindow);
                });
                return box;
            },
        });
        map.addControl(new FullWindow());

        // The client action lays out in stages and a phone rotates: re-measure
        // whenever the box changes, and take the first fit once it has a size.
        if (window.ResizeObserver) {
            this.resizeObserver = new ResizeObserver(() => {
                if (!this.map) {
                    return;
                }
                this.map.invalidateSize({ animate: false });
                if (!this.fitted) {
                    this.fitAll();
                }
            });
            this.resizeObserver.observe(el);
        } else {
            setTimeout(() => {
                if (this.map) {
                    this.map.invalidateSize({ animate: false });
                    if (!this.fitted) {
                        this.fitAll();
                    }
                }
            }, 200);
        }
    }

    /** The map over the whole window (the page's head and lists hidden), or back. */
    setFullWindow(on) {
        this.state.fullWindow = Boolean(on);
        this.paintFullLink();
    }

    paintFullLink() {
        const link = this.fullLink;
        if (!link) {
            return;
        }
        const on = this.state.fullWindow;
        link.title = on ? _t("Back to the page (Esc)") : _t("Full window");
        link.setAttribute("aria-label", link.title);
        link.innerHTML = on ? '<i class="fa fa-compress"/>' : '<i class="fa fa-expand"/>';
    }

    /** The tile layer waits for the payload: the lab picks its tile server. */
    ensureTiles() {
        const L = window.L;
        const data = this.state.data;
        if (!this.map || this.tiles || !data || !data.tiles) {
            return;
        }
        this.tiles = L.tileLayer(data.tiles, {
            maxZoom: 19, attribution: data.attribution || "",
        }).addTo(this.map);
    }

    hasSize() {
        if (!this.map) {
            return false;
        }
        const size = this.map.getSize();
        return size.x > 0 && size.y > 0;
    }

    // --------------------------------------------------------------- drawing
    /** The rows of the panel: filtered, the focused person always kept. */
    get executives() {
        const all = (this.state.data && this.state.data.executives) || [];
        const focus = this.state.focusId;
        const rows = all.filter(
            (ex) => this.state.show === "all" || ex.on_duty || ex.user_id === focus);
        return [...rows].sort(
            (a, b) => (STATUS_RANK[a.status] ?? 9) - (STATUS_RANK[b.status] ?? 9)
                || String(a.name).localeCompare(String(b.name)));
    }

    get focused() {
        const id = this.state.focusId;
        return id ? this.executives.find((ex) => ex.user_id === id) || null : null;
    }

    get counts() {
        const all = (this.state.data && this.state.data.executives) || [];
        return {
            duty: all.filter((ex) => ex.on_duty).length,
            moving: all.filter((ex) => ex.status === "moving").length,
            at_clinic: all.filter((ex) => ex.status === "at_clinic").length,
            offline: all.filter((ex) => ex.status === "offline").length,
        };
    }

    /** Redraw the dynamic layers in place; the clinics only when they changed. */
    redraw() {
        const L = window.L;
        if (!L || !this.map || !this.state.data) {
            return;
        }
        this.ensureTiles();
        const at = this.state.replayAt;
        const seen = new Set();
        for (const ex of this.executives) {
            seen.add(ex.user_id);
            this.drawExecutive(ex, at);
        }
        // Filtered out or gone from the payload: off the map, layers and all.
        for (const [id, lay] of this.exLayers) {
            if (!seen.has(id)) {
                lay.group.remove();
                this.exLayers.delete(id);
            }
        }
        this.drawClinics();
        if (!this.fitted) {
            this.fitAll();
        }
        this.followFocus();
    }

    /**
     * Update one store of layers against one list of items, by key. What is
     * already there is moved, what is new is created, what is gone is removed —
     * so a popup open on a marker survives the poll that moved it.
     */
    sync(store, items, keyOf, create, update) {
        const seen = new Set();
        for (const item of items) {
            const key = keyOf(item);
            seen.add(key);
            const layer = store.get(key);
            if (layer) {
                update(layer, item);
            } else {
                store.set(key, create(item));
            }
        }
        for (const [key, layer] of store) {
            if (!seen.has(key)) {
                layer.remove();
                store.delete(key);
            }
        }
    }

    /** The trail cut at the replay time: legs keep their breaks. */
    legsAt(ex, at) {
        // The trail along the roads when the server has worked it out for this
        // person (field_live.js loadRoad), the fixes joined straight otherwise.
        const legs = ex.road_legs || ex.legs || [];
        if (at === null) {
            return legs;
        }
        return legs.map((leg) => leg.filter((p) => p[2] <= at)).filter((leg) => leg.length);
    }

    /** Where the trail had the executive at `at`: the last point at or before it. */
    positionAt(ex, at) {
        let last = null;
        for (const leg of ex.legs || []) {
            for (const p of leg) {
                if (p[2] <= at) {
                    last = p;
                } else {
                    return last;
                }
            }
        }
        return last;
    }

    drawExecutive(ex, at) {
        const L = window.L;
        let lay = this.exLayers.get(ex.user_id);
        if (!lay) {
            lay = {
                group: L.layerGroup().addTo(this.map),
                trails: [], gaps: new Map(), doors: new Map(), halts: new Map(),
                speed: new Map(), marker: null, iconKey: "",
            };
            this.exLayers.set(ex.user_id, lay);
        }
        const dim = Boolean(this.state.focusId && this.state.focusId !== ex.user_id);
        const alpha = dim ? 0.3 : 1;
        const colour = ex.colour || "#4f46e5";

        // The trail, one polyline per run of one kind: a break in the line is a
        // stretch the phone was not watching, and bridging it would invent a road
        // nobody took. A stretch on foot is dashed, and a stretch the phone crossed
        // with no clear signal is a dotted grey line - a straight line under the
        // road, not the road. (2026-10-23)
        const legs = this.legsAt(ex, at).filter((leg) => leg.length > 1);
        const runs = [];
        for (const leg of legs) {
            let run = { mode: "vehicle", points: [leg[0]] };
            runs.push(run);
            for (let i = 1; i < leg.length; i++) {
                // A fix pushed live carries no mode until the next reload classifies
                // it: it continues the run it joins, like a still step.
                const said = leg[i][4] || "still";
                const mode = said === "foot" || said === "blind" ? said : "vehicle";
                if (said !== "still" && mode !== run.mode && run.points.length > 1) {
                    run = { mode, points: [leg[i - 1], leg[i]] };
                    runs.push(run);
                } else {
                    if (said !== "still") {
                        run.mode = mode;
                    }
                    run.points.push(leg[i]);
                }
            }
        }
        const styleFor = (mode) => mode === "foot"
            ? { color: colour, weight: dim ? 2 : 3, opacity: 0.7 * alpha, dashArray: "4 7", lineJoin: "round" }
            : mode === "blind"
                ? { color: "#6c757d", weight: dim ? 2 : 3, opacity: 0.6 * alpha, dashArray: "1 7", lineCap: "round" }
                : { color: colour, weight: dim ? 3 : 4, opacity: 0.55 * alpha, dashArray: null, lineJoin: "round" };
        runs.forEach((run, i) => {
            const latlngs = run.points.map((p) => [p[0], p[1]]);
            const style = styleFor(run.mode);
            if (lay.trails[i]) {
                lay.trails[i].setLatLngs(latlngs);
                lay.trails[i].setStyle(style);
            } else {
                lay.trails[i] = L.polyline(latlngs, style).addTo(lay.group);
            }
        });
        while (lay.trails.length > runs.length) {
            lay.trails.pop().remove();
        }

        // The ends of every break, so a gap reads as a gap and not as a map that
        // failed to draw. The server's gap list carries the minutes; the legs
        // carry the places.
        const gapEnds = [];
        const allLegs = this.legsAt(ex, at);
        for (let i = 0; i < allLegs.length - 1; i++) {
            const end = allLegs[i][allLegs[i].length - 1];
            const start = allLegs[i + 1][0];
            if (!end || !start) {
                continue;
            }
            const gap = (ex.gaps || []).find((g) => Math.abs(g.from_epoch - end[2]) < 120)
                || { from_epoch: end[2], to_epoch: start[2],
                     minutes: Math.round((start[2] - end[2]) / 60) };
            const text = esc(_t("recording stopped %(from)s - resumed %(to)s (%(min)s min)", {
                from: clock(gap.from_epoch), to: clock(gap.to_epoch), min: Math.round(gap.minutes),
            }));
            gapEnds.push({ key: `${gap.from_epoch}:end`, lat: end[0], lon: end[1], text });
            gapEnds.push({ key: `${gap.from_epoch}:start`, lat: start[0], lon: start[1], text });
        }
        this.sync(lay.gaps, gapEnds, (g) => g.key,
            (g) => L.circleMarker([g.lat, g.lon], {
                radius: 4, color: colour, weight: 2, fillColor: "#fff", fillOpacity: 1,
                opacity: alpha,
            }).bindPopup(g.text).addTo(lay.group),
            (layer, g) => {
                layer.setLatLng([g.lat, g.lon]);
                layer.setStyle({ opacity: alpha, fillOpacity: alpha });
                layer.setPopupContent(g.text);
            });

        // Doors: the fixes taken at check-in and check-out, coloured by reading.
        const doors = (ex.doors || []).filter(
            (d) => (d.lat || d.lon) && (at === null || d.epoch <= at));
        this.sync(lay.doors, doors, (d) => `${d.visit_id}:${d.kind}`,
            (d) => L.circleMarker([d.lat, d.lon], {
                radius: d.kind === "in" ? 8 : 6, color: "#fff", weight: 2,
                fillColor: DOOR_COLOURS[d.state] || "#6c757d", fillOpacity: alpha, opacity: alpha,
            }).bindPopup(this.doorPopup(ex, d)).addTo(lay.group),
            (layer, d) => {
                layer.setLatLng([d.lat, d.lon]);
                layer.setStyle({ fillColor: DOOR_COLOURS[d.state] || "#6c757d",
                                 fillOpacity: alpha, opacity: alpha });
                layer.setPopupContent(this.doorPopup(ex, d));
            });

        // Halts: an orange tile with the minutes, where the phone sat still.
        const halts = (ex.halts || []).filter(
            (h) => (h.lat || h.lon) && (at === null || h.started_epoch <= at));
        this.sync(lay.halts, halts, (h) => h.id,
            (h) => L.marker([h.lat, h.lon], {
                opacity: alpha, zIndexOffset: 500, icon: this.haltIcon(h),
            }).bindPopup(this.haltPopup(h)).addTo(lay.group),
            (layer, h) => {
                layer.setLatLng([h.lat, h.lon]);
                layer.setOpacity(alpha);
                layer.setIcon(this.haltIcon(h));
                layer.setPopupContent(this.haltPopup(h));
            });

        // Overspeed: small red dots, only when the person is in focus or nobody
        // is — dimmed they would be noise over somebody else's round.
        const speed = dim ? [] : (ex.overspeed_points || []).filter(
            (p) => at === null || p[2] <= at);
        this.sync(lay.speed, speed, (p) => p[2],
            (p) => L.circleMarker([p[0], p[1]], {
                radius: 3, color: OVERSPEED_COLOUR, fillColor: OVERSPEED_COLOUR,
                fillOpacity: 0.9, weight: 1,
            }).bindTooltip(esc(_t("%(speed)s km/h at %(time)s", {
                speed: Math.round(p[3] || 0), time: clock(p[2]),
            }))).addTo(lay.group),
            (layer, p) => layer.setLatLng([p[0], p[1]]));

        // The executive: a coloured circle with their initials. Live, it stands
        // on the last fix; in replay, on the trail at the replay minute.
        let pos = null;
        let status = ex.status;
        if (at !== null) {
            const p = this.positionAt(ex, at);
            pos = p ? { lat: p[0], lon: p[1], epoch: p[2], speed_kmh: p[3] } : null;
            status = "replay";
        } else if (ex.last && (ex.last.lat || ex.last.lon)) {
            pos = ex.last;
        }
        if (!pos) {
            if (lay.marker) {
                lay.marker.remove();
                lay.marker = null;
                lay.iconKey = "";
            }
            return;
        }
        const iconKey = `${status}|${ex.name}|${colour}`;
        if (!lay.marker) {
            lay.marker = L.marker([pos.lat, pos.lon], {
                zIndexOffset: 1000, opacity: alpha, icon: this.executiveIcon(ex, status),
            }).bindPopup(this.executivePopup(ex, pos, at), { maxWidth: 280 })
              .on("click", () => this.focus(ex, false))
              .addTo(lay.group);
            lay.iconKey = iconKey;
        } else {
            lay.marker.setLatLng([pos.lat, pos.lon]);
            lay.marker.setOpacity(alpha);
            if (lay.iconKey !== iconKey) {
                lay.marker.setIcon(this.executiveIcon(ex, status));
                lay.iconKey = iconKey;
            }
            lay.marker.setPopupContent(this.executivePopup(ex, pos, at));
        }
    }

    /** Today's clinics: a small grey pin, and the radius ring at street zoom. */
    drawClinics() {
        const L = window.L;
        const clinics = ((this.state.data && this.state.data.clinics) || [])
            .filter((c) => c.lat || c.lon);
        const ringGroup = this.ringGroup;
        this.sync(this.clinicLayers, clinics, (c) => c.id,
            (c) => {
                const dot = L.circleMarker([c.lat, c.lon], {
                    radius: 5, color: "#6c757d", weight: 1.5, fillColor: "#fff", fillOpacity: 0.9,
                }).bindTooltip(esc(c.name)).addTo(this.map);
                const ring = L.circle([c.lat, c.lon], {
                    radius: c.radius_m || 60, color: "#6c757d", weight: 1,
                    fillOpacity: 0.05, interactive: false,
                }).addTo(ringGroup);
                return {
                    dot, ring,
                    remove: () => {
                        dot.remove();
                        ringGroup.removeLayer(ring);
                    },
                };
            },
            (pair, c) => {
                pair.dot.setLatLng([c.lat, c.lon]);
                pair.ring.setLatLng([c.lat, c.lon]);
                pair.ring.setRadius(c.radius_m || 60);
            });
        this.toggleRings();
    }

    toggleRings() {
        if (!this.map || !this.ringGroup) {
            return;
        }
        const show = this.map.getZoom() >= RING_ZOOM;
        if (show && !this.map.hasLayer(this.ringGroup)) {
            this.ringGroup.addTo(this.map);
        } else if (!show && this.map.hasLayer(this.ringGroup)) {
            this.ringGroup.remove();
        }
    }

    // ----------------------------------------------------------------- icons
    executiveIcon(ex, status) {
        const L = window.L;
        const colour = ex.colour || "#4f46e5";
        const ring = status === "moving" ? '<span class="o_fw_live_pulse"></span>' : "";
        return L.divIcon({
            className: "o_fw_live_icon",
            html: `<div class="o_fw_live_ex o_fw_live_ex_${esc(status)}" style="--c:${esc(colour)}">`
                + ring
                + `<span class="o_fw_live_ex_dot">${esc(initials(ex.name))}</span>`
                + `<span class="o_fw_live_ex_name">${esc(ex.name)}</span>`
                + "</div>",
            iconSize: [32, 32],
            iconAnchor: [16, 16],
            popupAnchor: [0, -14],
        });
    }

    haltIcon(halt) {
        const L = window.L;
        return L.divIcon({
            className: "o_fw_live_icon",
            html: `<div class="o_fw_live_halt">${esc(Math.round(halt.minutes || 0))}′</div>`,
            iconSize: [26, 26],
            iconAnchor: [13, 13],
            popupAnchor: [0, -12],
        });
    }

    // ---------------------------------------------------------------- popups
    executivePopup(ex, pos, at) {
        const lines = [`<strong>${esc(ex.name)}</strong>`];
        if (at !== null) {
            lines.push(esc(_t("Replay · %(time)s · %(speed)s km/h", {
                time: clock(pos.epoch), speed: Math.round(pos.speed_kmh || 0),
            })));
        } else {
            lines.push(`<span class="o_fw_live_pop_status" style="color:${STATUS_COLOURS[ex.status] || "#6c757d"}">`
                + esc(ex.status_label || ex.status) + "</span>");
            lines.push(esc(_t("Last seen %s", this.lastSeen(ex))));
            const last = ex.last;
            if (last) {
                const bits = [];
                if (last.speed_kmh !== null && last.speed_kmh !== undefined) {
                    bits.push(_t("%s km/h", Math.round(last.speed_kmh)));
                }
                if (last.accuracy_m) {
                    bits.push(_t("±%s m", Math.round(last.accuracy_m)));
                }
                if (last.battery !== null && last.battery !== undefined && last.battery !== "") {
                    bits.push(_t("battery %s%%", Math.round(last.battery)));
                }
                if (bits.length) {
                    lines.push(esc(bits.join(" · ")));
                }
            }
            if (ex.at_clinic) {
                lines.push(esc(_t("At %(clinic)s since %(time)s", {
                    clinic: ex.at_clinic.name, time: clock(ex.at_clinic.since_epoch),
                })));
            }
            if (ex.next) {
                const eta = ex.next.eta_min !== null && ex.next.eta_min !== undefined
                    ? _t("~%s min", Math.round(ex.next.eta_min)) : "";
                const dist = ex.next.distance_km !== null && ex.next.distance_km !== undefined
                    ? _t("%s km", Number(ex.next.distance_km).toFixed(1)) : "";
                lines.push(esc(_t("Next: %(clinic)s %(rest)s", {
                    clinic: ex.next.name, rest: [dist, eta].filter(Boolean).join(" · "),
                })));
            }
        }
        lines.push(`<span class="text-muted">${esc(_t("%(km)s km today · %(done)s/%(planned)s visits", {
            km: Number(ex.km || 0).toFixed(1), done: ex.visits_done || 0, planned: ex.visits_planned || 0,
        }))}</span>`);
        if (ex.phone) {
            lines.push(`<a href="tel:${esc(ex.phone)}"><i class="fa fa-phone me-1"></i>${esc(ex.phone)}</a>`);
        }
        return lines.join("<br/>");
    }

    doorPopup(ex, door) {
        const lines = [`<strong>${esc(door.name)}</strong>`, `<span class="text-muted">${esc(ex.name)}</span>`];
        const when = door.kind === "out"
            ? _t("Checked out %s", clock(door.epoch))
            : door.left_epoch
                ? _t("In %(in)s – out %(out)s", { in: clock(door.epoch), out: clock(door.left_epoch) })
                : _t("Checked in %s", clock(door.epoch));
        lines.push(esc(when));
        const colour = DOOR_COLOURS[door.state] || "#6c757d";
        lines.push(`<span style="color:${colour}">${esc(DOOR_LABELS[door.state] || door.state)}</span>`);
        return lines.join("<br/>");
    }

    haltPopup(halt) {
        const lines = [`<strong>${esc(_t("Halt · %s min", Math.round(halt.minutes || 0)))}</strong>`];
        if (halt.place) {
            lines.push(esc(halt.place));
        }
        lines.push(esc(halt.ended_epoch
            ? _t("%(from)s – %(to)s", { from: clock(halt.started_epoch), to: clock(halt.ended_epoch) })
            : _t("since %s, still there", clock(halt.started_epoch))));
        return lines.join("<br/>");
    }

    // --------------------------------------------------------- fit and follow
    /** Every point worth framing for one executive: trail, doors, position. */
    pointsOf(ex) {
        const at = this.state.replayAt;
        const points = [];
        for (const leg of this.legsAt(ex, at)) {
            for (const p of leg) {
                points.push([p[0], p[1]]);
            }
        }
        for (const d of ex.doors || []) {
            if ((d.lat || d.lon) && (at === null || d.epoch <= at)) {
                points.push([d.lat, d.lon]);
            }
        }
        if (at === null && ex.last && (ex.last.lat || ex.last.lon)) {
            points.push([ex.last.lat, ex.last.lon]);
        }
        return points;
    }

    fitAll() {
        if (!this.map || !this.state.data || !this.hasSize()) {
            return;
        }
        const focus = this.focused;
        let points = focus ? this.pointsOf(focus) : [];
        if (!points.length) {
            // Nobody in focus, or a focused person with nothing recorded yet:
            // frame everyone shown, and today's clinics with them.
            points = this.executives.flatMap((ex) => this.pointsOf(ex));
            for (const c of this.state.data.clinics || []) {
                if (c.lat || c.lon) {
                    points.push([c.lat, c.lon]);
                }
            }
        }
        this.fitted = true;
        if (points.length) {
            this.map.fitBounds(points, { padding: [40, 40], maxZoom: 16 });
            this.viewSet = true;
        } else {
            // Nothing recorded yet and no clinic planned: the lab's own region (the
            // server sends the company's pin, else its clinics' middle, else Kerala),
            // never the whole world - a manager opening the map before the morning
            // starts must see their territory, not Greenland. (2026-10-19)
            const home = this.state.data && this.state.data.home;
            if (home && home.lat !== undefined) {
                this.map.setView([home.lat, home.lon], home.zoom || 10);
            } else if (!this.viewSet) {
                this.map.setView([20, 0], 2);
            }
            this.viewSet = true;
        }
    }

    /** Follow mode: pan once the marker leaves the inner 80% of the view. */
    followFocus() {
        const focus = this.focused;
        if (!focus || !this.state.follow || this.state.replayAt !== null || !this.map
            || !this.hasSize() || !this.viewSet) {
            return;
        }
        const last = focus.last;
        if (!last || !(last.lat || last.lon)) {
            return;
        }
        const here = [last.lat, last.lon];
        if (!this.map.getBounds().pad(FOLLOW_PAD).contains(here)) {
            this.map.panTo(here);
        }
    }

    // --------------------------------------------------------------- actions
    /** A row or a marker: this person, framed, the rest dimmed. */
    focus(ex, fit = true) {
        const same = this.state.focusId === ex.user_id;
        if (same && fit) {
            return this.clearFocus();
        }
        this.state.focusId = ex.user_id;
        this.state.follow = true;
        this.redraw();
        if (fit && this.map) {
            const points = this.pointsOf(ex);
            if (points.length && this.hasSize()) {
                this.map.fitBounds(points, { padding: [40, 40], maxZoom: 16 });
                this.viewSet = true;
            }
        }
        if (window.matchMedia(MOBILE).matches) {
            this.state.panelOpen = false;
            setTimeout(() => this.map && this.map.invalidateSize(), 250);
        }
    }

    clearFocus() {
        this.state.focusId = null;
        this.state.follow = false;
        this.fitted = false;
        this.redraw();
    }

    setShow(value) {
        this.touchedShow = true;
        this.state.show = value;
        this.fitted = false;
        this.redraw();
    }

    /** Toggle follow by hand; the map's dragstart also switches it off. */
    toggleFollow() {
        this.state.follow = !this.state.follow;
        if (this.state.follow) {
            this.followFocus();
        }
    }

    togglePanel() {
        this.state.panelOpen = !this.state.panelOpen;
        setTimeout(() => this.map && this.map.invalidateSize(), 250);
    }

    get todayIso() {
        return localIso(new Date());
    }

    async pickDate(value) {
        if (!value || value === this.state.day) {
            return;
        }
        await this.changeDay(value);
    }

    async goToday() {
        await this.changeDay("");
    }

    /** A different day is a different drawing: everything is rebuilt. */
    async changeDay(day) {
        this.exitReplay(false);
        this.state.day = day;
        this.fitted = false;
        this.touchedShow = false;
        for (const lay of this.exLayers.values()) {
            lay.group.remove();
        }
        this.exLayers.clear();
        await this.load();
        if (this.state.isToday && this.state.show === "all" && !this.touchedShow) {
            this.state.show = "duty";
        }
        this.redraw();
        this.startPolling();
    }

    // ---------------------------------------------------------------- replay
    onReplay(ev) {
        this.state.replayAt = Number(ev.target.value);
        this.redraw();
    }

    togglePlay() {
        if (this.state.playing) {
            clearInterval(this.playTimer);
            this.playTimer = null;
            this.state.playing = false;
            return;
        }
        const range = this.state.range;
        if (!range) {
            return;
        }
        if (this.state.replayAt === null || this.state.replayAt >= range.max) {
            this.state.replayAt = range.min;
        }
        this.state.playing = true;
        this.playTimer = setInterval(() => {
            this.state.replayAt = Math.min(this.state.replayAt + REPLAY_STEP_S, range.max);
            this.redraw();
            if (this.state.replayAt >= range.max) {
                this.togglePlay();
            }
        }, REPLAY_TICK_MS);
    }

    /** Back to the day's final picture (and, for today, back to live). */
    exitReplay(redraw = true) {
        if (this.state.playing) {
            this.togglePlay();
        }
        this.state.replayAt = null;
        if (redraw) {
            this.redraw();
        }
    }

    get replayLabel() {
        const t = this.state.replayAt;
        return t === null ? "" : clock(t);
    }

    // ------------------------------------------------------------ formatting
    clock(epoch) {
        return clock(epoch);
    }

    /** "3 min ago" today; the clock time on a past day, where "ago" means nothing. */
    lastSeen(ex) {
        const epoch = ex.last ? ex.last.epoch : ex.since_epoch;
        if (!epoch) {
            return this.state.isToday ? _t("not seen today") : _t("no fix");
        }
        if (!this.state.isToday) {
            return clock(epoch);
        }
        const minutes = Math.max(0, Math.round((this.nowEpoch() - epoch) / 60));
        if (minutes < 1) {
            return _t("just now");
        }
        if (minutes < 60) {
            return _t("%s min ago", minutes);
        }
        const hours = Math.floor(minutes / 60);
        if (hours < 24) {
            return _t("%(h)sh %(m)sm ago", { h: hours, m: minutes % 60 });
        }
        return clock(epoch);
    }

    statusColour(status) {
        return STATUS_COLOURS[status] || "#6c757d";
    }

    km(value) {
        return Number(value || 0).toFixed(1);
    }

    round(value) {
        return Math.round(value || 0);
    }

    get legend() {
        return [
            { kind: "status", colour: STATUS_COLOURS.moving, label: _t("moving") },
            { kind: "status", colour: STATUS_COLOURS.at_clinic, label: _t("at a clinic") },
            { kind: "status", colour: STATUS_COLOURS.stopped, label: _t("stopped") },
            { kind: "status", colour: STATUS_COLOURS.offline, label: _t("offline") },
            { kind: "door", colour: DOOR_COLOURS.ok, label: _t("door: at the clinic") },
            { kind: "door", colour: DOOR_COLOURS.far, label: _t("door: away") },
            { kind: "door", colour: DOOR_COLOURS.nofix, label: _t("door: no fix") },
            { kind: "door", colour: DOOR_COLOURS.nopin, label: _t("door: clinic not pinned") },
            { kind: "foot", colour: "transparent", label: _t("on foot") },
            { kind: "blind", colour: "transparent", label: _t("no clear signal") },
            { kind: "halt", colour: HALT_COLOUR, label: _t("halt (minutes)") },
            { kind: "speed", colour: OVERSPEED_COLOUR, label: _t("over the speed limit") },
            { kind: "gap", colour: "#fff", label: _t("recording gap") },
        ];
    }

    openSheet(ex) {
        // The day behind the dot, when there is one to open.
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "lab.daily.update",
            views: [[false, "list"], [false, "form"]],
            domain: [["user_id", "=", ex.user_id], ["date", "=", this.state.day]],
            name: _t("%(name)s — %(day)s", { name: ex.name, day: this.state.day }),
        });
    }
}

registry.category("actions").add("lab_live_map", LiveMap);
