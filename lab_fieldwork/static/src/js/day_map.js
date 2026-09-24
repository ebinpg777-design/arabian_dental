/** @odoo-module **/

import { Component, onWillStart, onWillUnmount, useEffect, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";

/**
 * The day's track, on a map, inside the day sheet.
 *
 * Odoo Community ships no map view (web_map is Enterprise), so this draws its own with
 * the Leaflet the module vendors and whichever tile server the lab configures — Open
 * Street Map by default, which needs no key.
 *
 * Every mark is a fix the phone already recorded: a check-in, a check-out, a delivery.
 * They are joined in the order they happened, so an approver reads the round as a path
 * rather than as a column of coordinates. A clinic that carries its own pin is drawn as
 * a ring, with a dashed line to the fix whenever the two are not the same place — that
 * gap is the thing the whole location feature exists to show.
 *
 * The map is only built once the element has a size. A form renders inside a notebook
 * and inside a dialog, and Leaflet measures its container at creation: built too early
 * it lays the tiles out for a zero-width box and shows one grey square.
 */
export class FwDayMap extends Component {
    static template = "lab_fieldwork.DayMap";
    static props = { ...standardWidgetProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.mapRef = useRef("map");
        this.state = useState({ track: null, error: null, loading: true });
        this.map = null;

        onWillStart(() => this.load());

        // Draw when the data and the element are both there, and redraw when the
        // record changes under us (Previous day / Next day keep the same widget).
        useEffect(
            () => {
                if (this.state.track && this.mapRef.el) {
                    this.draw();
                }
                return () => this.destroyMap();
            },
            () => [this.state.track, this.mapRef.el]
        );

        onWillUnmount(() => this.destroyMap());
    }

    get resId() {
        return this.props.record.resId;
    }

    async load() {
        this.state.loading = true;
        try {
            if (!this.resId) {
                // An unsaved sheet has no day to draw yet.
                this.state.track = { points: [], clinics: [] };
            } else {
                this.state.track = await this.orm.call(
                    "lab.daily.update", "get_day_track", [[this.resId]]
                );
            }
        } catch (error) {
            this.state.error = error.message?.data?.message || error.message || String(error);
        } finally {
            this.state.loading = false;
        }
    }

    get hasPoints() {
        const t = this.state.track;
        return Boolean(t && (t.points.length || (t.trail && t.trail.length)));
    }

    /** The caption under the heading: what the map is showing, in words. */
    get caption() {
        const t = this.state.track;
        if (!t) {
            return "";
        }
        const parts = [`${t.located} of ${t.visits} ${t.visits === 1 ? "door" : "doors"} located`];
        if (t.odo_km) {
            parts.push(`${t.odo_km} km on the odometer`);
        }
        if (t.road_km) {
            // The figure worth putting next to the odometer: both are road distance.
            parts.push(`${t.road_km} km by road`);
        } else if (t.straight_km) {
            parts.push(`${t.straight_km} km straight-line between fixes`);
        }
        if (t.trail_km) {
            parts.push(`${t.trail_km} km recorded`);
        }
        if (t.route_state === "failed") {
            parts.push("routing service unavailable");
        }
        return parts.join(" · ");
    }

    /** Ask the routing service again, for a day drawn while it was down. */
    async redraw() {
        this.state.loading = true;
        try {
            this.state.track = await this.orm.call(
                "lab.daily.update", "get_day_track", [[this.resId]], { force_route: true }
            );
        } finally {
            this.state.loading = false;
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
        }
    }

    colourFor(state) {
        // The same four readings the timeline uses, so one colour means one thing
        // wherever the day is shown.
        return {
            ok: "#0f9d58",      // at the door
            far: "#dc3545",     // away from the clinic
            nofix: "#d99b00",   // no fix
            nopin: "#2563eb",   // recorded, clinic not pinned
        }[state] || "#6c757d";
    }

    draw() {
        const L = window.L;
        const track = this.state.track;
        this.destroyMap();
        if (!L || !this.mapRef.el || !this.hasPoints) {
            return;
        }

        const map = L.map(this.mapRef.el, {
            scrollWheelZoom: true,      // asked for: the wheel zooms while over the map
            dragging: true,
            keyboard: true,
            attributionControl: true,
        });
        this.map = map;
        L.tileLayer(track.tiles, { maxZoom: 19, attribution: track.attribution }).addTo(map);

        // The recorded route, under everything else: this is where the phone actually
        // was, minute by minute, so it is the ground the rest of the map sits on. Each
        // segment is drawn on its own — a break in the line is a stretch the phone was
        // not watching, and bridging it would invent a road nobody took.
        for (const segment of track.trail || []) {
            const line = segment.map((p) => [p[0], p[1]]);
            L.polyline(line, {
                color: "#0ea5e9", weight: 5, opacity: 0.45, lineJoin: "round",
            }).addTo(map);
        }
        // The ends of every break, marked, so a gap reads as a gap and not as a map
        // that failed to draw.
        const segments = track.trail || [];
        segments.forEach((segment, index) => {
            const ends = [];
            if (index > 0) {
                ends.push(["resumed", segment[0]]);
            }
            if (index < segments.length - 1) {
                ends.push(["stopped", segment[segment.length - 1]]);
            }
            for (const [what, ping] of ends) {
                L.circleMarker([ping[0], ping[1]], {
                    radius: 4, color: "#0ea5e9", weight: 2,
                    fillColor: "#fff", fillOpacity: 1,
                }).addTo(map).bindPopup(
                    `Recording ${what} at ${this.escape(ping[2])}<br/>` +
                    '<span class="text-muted">the app was closed or the phone ' +
                    "had no signal</span>"
                );
            }
        });

        // The path through the day. A road route is drawn solid; without one the map
        // falls back to straight lines, dashed, so nobody reads a crow's flight as a
        // road that was driven.
        if (track.geometry && track.geometry.length > 1) {
            L.polyline(track.geometry, {
                color: "#6d28d9", weight: 4, opacity: 0.8,
            }).addTo(map);
        } else {
            const line = track.points.map((p) => [p.lat, p.lon]);
            if (line.length > 1) {
                L.polyline(line, {
                    color: "#6d28d9", weight: 3, opacity: 0.7, dashArray: "6 6",
                }).addTo(map);
            }
        }

        // The clinics' own pins, and the gap to the fix taken there.
        const clinics = {};
        for (const clinic of track.clinics) {
            clinics[clinic.id] = clinic;
            L.circle([clinic.lat, clinic.lon], {
                radius: 60, color: "#6c757d", weight: 1, fillOpacity: 0.06,
            }).addTo(map).bindPopup(`<b>${this.escape(clinic.name)}</b><br/>clinic pin`);
        }

        for (const point of track.points) {
            const colour = this.colourFor(point.state);
            const clinic = point.clinic_id && clinics[point.clinic_id];
            if (clinic && point.metres > 120) {
                L.polyline([[point.lat, point.lon], [clinic.lat, clinic.lon]], {
                    color: colour, weight: 1, dashArray: "4 4", opacity: 0.8,
                }).addTo(map);
            }
            const marker = L.circleMarker([point.lat, point.lon], {
                radius: point.kind === "delivery" ? 7 : 9,
                color: "#fff", weight: 2, fillColor: colour, fillOpacity: 1,
            }).addTo(map);
            marker.bindPopup(this.popupFor(point));
            marker.on("click", () => marker.openPopup());
            marker.getElement()?.addEventListener("dblclick", () => this.open(point));
        }

        // Once the user has zoomed or panned, the view is theirs and nothing below
        // may take it back.
        this.userMoved = false;
        map.on("zoomstart dragstart", (ev) => {
            if (ev.hard !== true) {
                this.userMoved = true;
            }
        });

        this.fitToDay = () => {
            if (this.map && track.bounds && !this.userMoved) {
                this.map.fitBounds(track.bounds, { padding: [24, 24] });
            }
        };
        this.fitToDay();

        // A form lays out in stages — the widget is measured inside a group, inside a
        // sheet, sometimes inside a dialog — and Leaflet picks its zoom from whatever
        // width the box had when the map was built. Fitting once left the day framed
        // for a container that no longer existed: a street instead of the round.
        // Re-measure and re-fit whenever the box actually changes size, until the
        // user takes the view over. (client, 2026-09-23)
        // "Show the whole day" — a way back after zooming into one door.
        const ResetView = L.Control.extend({
            options: { position: "topleft" },
            onAdd: () => {
                const box = L.DomUtil.create("div", "leaflet-bar o_fw_daymap_reset");
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

        if (window.ResizeObserver) {
            this.resizeObserver = new ResizeObserver(() => {
                if (!this.map) {
                    return;
                }
                this.map.invalidateSize({ animate: false });
                this.fitToDay();
            });
            this.resizeObserver.observe(this.mapRef.el);
        } else {
            setTimeout(() => {
                if (this.map) {
                    this.map.invalidateSize({ animate: false });
                    this.fitToDay();
                }
            }, 150);
        }
    }

    escape(text) {
        const div = document.createElement("div");
        div.textContent = text || "";
        return div.innerHTML;
    }

    popupFor(point) {
        const bits = [`<b>${this.escape(point.label)}</b>`];
        const when = point.left_at ? `${point.at}–${point.left_at}` : point.at;
        const head = [when, point.note].filter(Boolean).join(" · ");
        if (head) {
            bits.push(this.escape(head));
        }
        if (point.state === "far" && point.metres) {
            bits.push(`<span style="color:#dc3545">${point.metres} m from the clinic</span>`);
        } else if (point.state === "ok" && point.metres) {
            bits.push(`${point.metres} m from the clinic`);
        } else if (point.state === "nopin") {
            bits.push("clinic has no pin to measure against");
        }
        bits.push('<span class="text-muted">double-click to open</span>');
        return bits.join("<br/>");
    }

    open(point) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: point.res_model,
            res_id: point.res_id,
            views: [[false, "form"]],
            target: "current",
        });
    }
}

export const fwDayMap = { component: FwDayMap };
registry.category("view_widgets").add("fw_day_map", fwDayMap);
