/** @odoo-module **/

import { Component, onWillStart, proxy, t, useProps } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

/**
 * Clinic / Doctor, picked with a thumb.
 *
 * The plain field on a phone is a search dialog over every clinic on the round:
 * type, wait, scroll a list of bare names. The executive nearly always wants one
 * of a handful - the clinics they starred, the ones they took cases at this week
 * - so those come first as large rows, and typing narrows every clinic of theirs
 * by name, place or phone as it is typed. The chosen clinic is a card that says
 * where it is, so the wrong DR. ANSALA is visible before the case is saved.
 * (client, 2026-11-27: "Provide an attractive easy widget for adding Clinic/Doctor")
 */
export class FwClinicPicker extends Component {
    static template = "lab_fieldwork.FwClinicPicker";
    props = useProps({ ...standardFieldProps, placeholder: t.string().optional() });

    setup() {
        this.orm = useService("orm");
        this.state = proxy({
            open: false, query: "", busy: false, loaded: false,
            starred: [], recent: [], results: [], chosen: null,
        });
        this.token = 0;
        this.timer = null;
        onWillStart(async () => {
            if (!this.value && !this.props.readonly) {
                this.state.open = true;
                await this.load("");
            } else if (this.value) {
                await this.describe();
            }
        });
    }

    get value() {
        return this.props.record.data[this.props.name];
    }

    get chosenName() {
        return this.value ? this.value.display_name : "";
    }

    /** Where the chosen clinic is, for the card - read once, by its name. */
    async describe() {
        if (!this.value) {
            return;
        }
        try {
            const data = await this.orm.call("lab.case", "clinic_picker", [this.chosenName, 5]);
            this.state.chosen = (data.results || []).find((r) => r.id === this.value.id) || null;
        } catch {
            this.state.chosen = null;
        }
    }

    /** Newest answer wins: a slow reply never overwrites a fresher one. */
    async load(query) {
        const token = ++this.token;
        this.state.busy = true;
        try {
            const data = await this.orm.call("lab.case", "clinic_picker", [query || ""]);
            if (token !== this.token) {
                return;
            }
            this.state.starred = data.starred || [];
            this.state.recent = data.recent || [];
            this.state.results = data.results || [];
            this.state.loaded = true;
        } finally {
            if (token === this.token) {
                this.state.busy = false;
            }
        }
    }

    onInput(ev) {
        this.state.query = ev.target.value;
        clearTimeout(this.timer);
        this.timer = setTimeout(() => this.load(this.state.query.trim()), 250);
    }

    clearQuery() {
        this.state.query = "";
        this.load("");
    }

    async openPicker() {
        if (this.props.readonly) {
            return;
        }
        this.state.open = true;
        if (!this.state.loaded) {
            await this.load("");
        }
    }

    closePicker() {
        if (this.value) {
            this.state.open = false;
        }
    }

    async pick(row) {
        await this.props.record.update({
            [this.props.name]: { id: row.id, display_name: row.name },
        });
        this.state.chosen = row;
        this.state.open = false;
        this.state.query = "";
    }

    async toggleStar(row, ev) {
        ev.stopPropagation();
        const starred = await this.orm.call("lab.clinic.favourite", "toggle", [row.id]);
        for (const list of [this.state.starred, this.state.recent, this.state.results]) {
            for (const r of list) {
                if (r.id === row.id) {
                    r.starred = starred;
                }
            }
        }
        if (this.state.chosen && this.state.chosen.id === row.id) {
            this.state.chosen.starred = starred;
        }
    }

    get showEmpty() {
        const s = this.state;
        if (!s.loaded || s.busy) {
            return false;
        }
        return s.query.trim() ? !s.results.length : !(s.starred.length || s.recent.length);
    }

    get emptyText() {
        if (this.state.query.trim()) {
            return _t("No clinic of yours matches “%s”.", this.state.query.trim());
        }
        return _t("Type a clinic, a doctor, a place or a phone number.");
    }
}

export const fwClinicPicker = {
    component: FwClinicPicker,
    displayName: _t("Clinic picker"),
    supportedTypes: ["many2one"],
    extractProps: ({ placeholder }) => ({ placeholder: placeholder || undefined }),
};
registry.category("fields").add("fw_clinic", fwClinicPicker);
