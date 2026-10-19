/** @odoo-module **/

import { Component, onMounted, onWillStart, onWillUnmount, proxy, signal, t, useProps } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { useLayoutEffect } from "@web/owl2/utils";

/**
 * The executive's own trail, on My Day.
 *
 * The manager's live map shows every round; this is one person's, drawn for the
 * person it belongs to. An executive who can see their own trail notices a dead GPS
 * at the clinic door rather than hearing about it at the day sheet - and knows,
 * because it is on their own screen, exactly what is being kept about them.
 *
 * It reads the same payload the manager's board reads (`get_live_map`, for one
 * user), so the two can never disagree about where somebody was. The map is built
 * once and its layers are redrawn on every reload: rebuilding the map would throw
 * away the zoom the executive just made, every sixty seconds.
 *
 * Leaflet is vendored by the module and loaded as window.L before this file.
 */

const RELOAD_MS = 60000;
// The same four readings the day sheet's map uses, so one colour means one thing
// wherever the day is shown.
const DOOR_COLOURS = {
    ok: "#0f9d58",      // at the door
    far: "#dc3545",     // away from the clinic
    nofix: "#d99b00",   // no fix
    nopin: "#2563eb",   // recorded, clinic not pinned
};
const TRAIL_COLOUR = "#0ea5e9";
const HALT_COLOUR = "#f97316";
const HERE_COLOUR = "#6d28d9";

export class MyTrail extends Component {
    static template = "lab_fieldwork.MyTrail";
    props = useProps({ userId: t.number() });

    setup() {
        this.orm = useService("orm");
        this.mapRef = signal.ref();
        this.state = proxy({ data: null, error: null, loading: true });
        this.map = null;
        this.layers = null;
        this.bounds = null;
        this.userMoved = false;
        this.fitting = false;
        this.loading = false;

        onWillStart(() => this.load());

        // Build when the element and the data are both there; redraw the layers when
        // the data changes under us. The map itself survives a reload.
        useLayoutEffect(
            () => {
                if (!this.mapRef()) {
                    this.destroyMap();
                    return;
                }
                if (this.state.data) {
                    this.ensureMap();
                    this.drawLayers();
                }
            },
            () => [this.state.data, this.mapRef()]
        );

        onMounted(() => {
            // While the card is on a screen somebody is looking at. A hidden tab
            // gets nothing: the recorder has its own flush on the way back.
            this.timer = setInterval(() => {
                if (document.visibilityState === "visible") {
                    this.load();
                }
            }, RELOAD_MS);
        });
        onWillUnmount(() => {
            clearInterval(this.timer);
            this.destroyMap();
        });
    }

    async load() {
        if (this.loading) {
            return;                        // a slow answer must not stack
        }
        this.loading = true;
        try {
            const data = await this.orm.silent.call(
                "lab.location.ping", "get_live_map", [],
                { day: null, user_ids: [this.props.userId] });
            this.state.data = data;
            this.state.error = null;
        } catch (error) {
            // Only an empty card says so; a missed minute on the road is not news.
            if (!this.state.data) {
                this.state.error = error.message?.data?.message || error.message
                    || String(error);
            }
        } finally {
            this.loading = false;
            this.state.loading = false;
        }
    }

    // ------------------------------------------------------------------ reading
    /** This executive's row. The contract returns one for the uid asked for. */
    get executive() {
        const d = this.state.data;
        if (!d || !d.executives || !d.executives.length) {
            return null;
        }
        return d.executives.find((e) => e.user_id === this.props.userId
                                     || e.id === this.props.userId)
            || d.executives[0];
    }

    get legs() {
        return (this.executive && this.executive.legs) || [];
    }

    get doors() {
        return (this.executive && this.executive.doors) || [];
    }

    get halts() {
        return (this.executive && this.executive.halts) || [];
    }

    get hasPoints() {
        const ex = this.executive;
        return Boolean(ex && (this.legs.some((leg) => leg.length) || this.doors.length
                              || ex.last));
    }

    /** "3.4 km recorded · 2 doors" - what the map is showing, in words. */
    get caption() {
        const ex = this.executive;
        if (!ex) {
            return "";
        }
        const km = Number(ex.km) || 0;
        const doors = this.doors.length;
        const parts = [
            `${km.toFixed(1)} km recorded`,
            `${doors} ${doors === 1 ? "door" : "doors"}`,
        ];
        const halts = this.halts.length;
        if (halts) {
            parts.push(`${halts} ${halts === 1 ? "stop" : "stops"}`);
        }
        if (ex.last && Number.isFinite(ex.last.age_s)) {
            parts.push(`last fix ${ago(ex.last.age_s)}`);
        }
        return parts.join(" · ");
    }

    // ------------------------------------------------------------------ the map
    ensureMap() {
        const L = window.L;
        const el = this.mapRef();
        const d = this.state.data;
        if (this.map || !L || !el || !d) {
            return;
        }
        const map = L.map(el, {
            // The card sits in a page that scrolls: the wheel keeps scrolling the
            // page, and the +/- buttons zoom the map.
            scrollWheelZoom: false,
            dragging: true,
            keyboard: false,
            attributionControl: true,
        });
        this.map = map;
        L.tileLayer(d.tiles, { maxZoom: 19, attribution: d.attribution }).addTo(map);
        this.layers = L.layerGroup().addTo(map);

        // Once the executive has zoomed or panned, the view is theirs and no reload
        // may take it back. Our own fits are not theirs.
        this.userMoved = false;
        map.on("zoomstart dragstart", () => {
            if (!this.fitting) {
                this.userMoved = true;
            }
        });

        // "Show the whole day" — the way back after zooming into one door.
        const ResetView = L.Control.extend({
            options: { position: "topleft" },
            onAdd: () => {
                const box = L.DomUtil.create("div", "leaflet-bar o_fw_trail_reset");
                const link = L.DomUtil.create("a", "", box);
                link.href = "#";
                link.title = "Show the whole day";
                link.innerHTML = '<i class="fa fa-arrows-alt"/>';
                L.DomEvent.on(link, "click", (ev) => {
                    L.DomEvent.stop(ev);
                    this.userMoved = false;
                    this.fitToDay();
                });
                return box;
            },
        });
        map.addControl(new ResetView());

        // The card opens with a toggle, inside a column that is still laying out:
        // Leaflet measured the box at creation and will frame the day for a size it
        // no longer has. Re-measure whenever the box changes, until the executive
        // takes the view over. (same trap as the day sheet, 2026-09-23)
        if (window.ResizeObserver) {
            this.resizeObserver = new ResizeObserver(() => {
                if (this.map) {
                    this.map.invalidateSize({ animate: false });
                    this.fitToDay();
                }
            });
            this.resizeObserver.observe(el);
        } else {
            setTimeout(() => {
                if (this.map) {
                    this.map.invalidateSize({ animate: false });
                    this.fitToDay();
                }
            }, 150);
        }
    }

    destroyMap() {
        if (this.resizeObserver) {
            this.resizeObserver.disconnect();
            this.resizeObserver = null;
        }
        if (this.map) {
            this.map.remove();
            this.map = null;
            this.layers = null;
        }
    }

    fitToDay() {
        if (!this.map || !this.bounds || this.userMoved) {
            return;
        }
        this.fitting = true;
        try {
            this.map.fitBounds(this.bounds, { padding: [20, 20], animate: false, maxZoom: 17 });
        } finally {
            this.fitting = false;
        }
    }

    /** Everything but the tiles, redrawn from the latest answer. */
    drawLayers() {
        const L = window.L;
        const d = this.state.data;
        const ex = this.executive;
        if (!this.map || !this.layers || !L || !d) {
            return;
        }
        this.layers.clearLayers();
        const add = (layer) => layer.addTo(this.layers);
        const points = [];

        // The clinics' own pins, as rings the size of their check-in radius: the
        // thing a door is measured against.
        for (const clinic of d.clinics || []) {
            if (!isPoint(clinic.lat, clinic.lon)) {
                continue;
            }
            add(L.circle([clinic.lat, clinic.lon], {
                radius: clinic.radius_m || 60, color: "#6c757d", weight: 1, fillOpacity: 0.06,
            })).bindPopup(`<b>${esc(clinic.name)}</b><br/>clinic pin`);
        }
        if (!ex) {
            return;
        }

        // The recorded route, under everything else. Each leg on its own — a break
        // between legs is a stretch the phone was not watching, and bridging it
        // would invent a road nobody took.
        for (const leg of this.legs) {
            const line = leg.filter((p) => isPoint(p[0], p[1])).map((p) => [p[0], p[1]]);
            if (line.length > 1) {
                add(L.polyline(line, {
                    color: TRAIL_COLOUR, weight: 5, opacity: 0.6, lineJoin: "round",
                }));
            } else if (line.length === 1) {
                add(L.circleMarker(line[0], {
                    radius: 3, color: TRAIL_COLOUR, weight: 2, fillColor: "#fff", fillOpacity: 1,
                }));
            }
            points.push(...line);
        }

        // Where the phone sat still. Orange, between the trail and the doors: a
        // stop at a clinic is a visit, a stop by the road is a question.
        for (const halt of this.halts) {
            const [lat, lon] = haltPoint(halt);
            if (!isPoint(lat, lon)) {
                continue;
            }
            add(L.circleMarker([lat, lon], {
                radius: 7, color: "#fff", weight: 2, fillColor: HALT_COLOUR, fillOpacity: 0.95,
            })).bindPopup(haltLabel(halt));
            points.push([lat, lon]);
        }

        // The doors: every place a visit was checked in, coloured by how the fix
        // compared with the clinic's pin.
        for (const door of this.doors) {
            if (!isPoint(door.lat, door.lon)) {
                continue;
            }
            const bits = [`<b>${esc(door.name)}</b>`];
            const when = clock(door.epoch);
            const head = [when, door.kind && door.kind !== "visit" ? door.kind : ""]
                .filter(Boolean).join(" · ");
            if (head) {
                bits.push(esc(head));
            }
            if (door.state === "far") {
                bits.push('<span style="color:#dc3545">away from the clinic</span>');
            } else if (door.state === "nofix") {
                bits.push("no location at check-in");
            } else if (door.state === "nopin") {
                bits.push("clinic has no pin to measure against");
            }
            add(L.circleMarker([door.lat, door.lon], {
                radius: 8, color: "#fff", weight: 2,
                fillColor: DOOR_COLOURS[door.state] || "#6c757d", fillOpacity: 1,
            })).bindPopup(bits.join("<br/>"));
            points.push([door.lat, door.lon]);
        }

        // Where the phone is now, with the fix's own uncertainty round it so a
        // ±120 m network fix is not read as a doorstep.
        const last = ex.last;
        if (last && isPoint(last.lat, last.lon)) {
            const here = [last.lat, last.lon];
            if (last.accuracy_m > 0) {
                add(L.circle(here, {
                    radius: last.accuracy_m, color: HERE_COLOUR, weight: 1, fillOpacity: 0.08,
                }));
            }
            const bits = ["<b>You</b>"];
            if (Number.isFinite(last.age_s)) {
                bits.push(ago(last.age_s));
            }
            if (Number.isFinite(last.speed_kmh) && last.speed_kmh > 0) {
                bits.push(`${Math.round(last.speed_kmh)} km/h`);
            }
            if (last.accuracy_m) {
                bits.push(`±${Math.round(last.accuracy_m)} m`);
            }
            if (Number.isFinite(last.battery)) {
                bits.push(`battery ${Math.round(last.battery)}%`);
            }
            add(L.circleMarker(here, {
                radius: 9, color: "#fff", weight: 3, fillColor: HERE_COLOUR, fillOpacity: 1,
                className: "o_fw_trail_here",
            })).bindPopup(bits.join("<br/>"));
            points.push(here);
        }

        this.bounds = points.length ? L.latLngBounds(points) : null;
        this.fitToDay();
    }
}

function isPoint(lat, lon) {
    return Number.isFinite(lat) && Number.isFinite(lon) && (lat !== 0 || lon !== 0);
}

/** A halt's place, whichever way the server spells it: {lat, lon} or [lat, lon, ...]. */
function haltPoint(halt) {
    if (Array.isArray(halt)) {
        return [halt[0], halt[1]];
    }
    return [halt.lat ?? halt.latitude, halt.lon ?? halt.longitude];
}

function haltLabel(halt) {
    const bits = ["<b>Stopped</b>"];
    if (Array.isArray(halt)) {
        return bits[0];
    }
    const from = clock(halt.from_epoch ?? halt.start_epoch ?? halt.epoch);
    const to = clock(halt.to_epoch ?? halt.end_epoch);
    if (from) {
        bits.push(esc(to ? `${from}–${to}` : from));
    }
    const minutes = halt.minutes ?? halt.mins ?? halt.duration_min;
    if (Number.isFinite(minutes)) {
        bits.push(`${Math.round(minutes)} min`);
    }
    if (halt.name || halt.clinic) {
        bits.push(esc(halt.name || halt.clinic));
    }
    return bits.join("<br/>");
}

/** "HH:MM" in the phone's own zone from an epoch in seconds (or milliseconds). */
function clock(epoch) {
    if (!Number.isFinite(epoch) || !epoch) {
        return "";
    }
    const date = new Date(epoch > 1e12 ? epoch : epoch * 1000);
    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function ago(seconds) {
    const s = Math.max(0, Math.round(seconds));
    if (s < 45) {
        return "just now";
    }
    if (s < 3600) {
        return `${Math.round(s / 60)} min ago`;
    }
    return `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min ago`;
}

function esc(text) {
    const div = document.createElement("div");
    div.textContent = text || "";
    return div.innerHTML;
}
