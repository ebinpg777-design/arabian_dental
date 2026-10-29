/** @odoo-module **/

import { Component, onWillUnmount, proxy, signal, t, useProps } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { usePopover } from "@web/core/popover/popover_hook";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { useRecordObserver } from "@web/model/relational_model/utils";
import {
    Many2ManyTagsField,
    many2ManyTagsField,
    many2ManyTagsFieldProps,
} from "@web/views/fields/many2many_tags/many2many_tags_field";
import { useX2ManyCrud } from "@web/views/fields/relational_utils";

/**
 * A new technician, made from the order (client, 2026-10-29: "Provide a new widget
 * button of creating new technician from SO. place it near to technicians field").
 *
 * While the name is typed, the people it may already be are listed - a technician
 * whose name holds it, or any contact spelled exactly so - with how many orders name
 * them, and one tap adds that person instead: a second spelling of somebody who
 * exists is how "RANA" and "RANA." both came to be.
 */
export class NewTechnicianPopover extends Component {
    static template = "sale_custom.NewTechnicianPopover";
    props = useProps({
        lookup: t.function(),
        create: t.function(),
        pick: t.function(),
        close: t.function(),
    });

    nameRef = signal.ref();

    setup() {
        this.state = proxy({ name: "", phone: "", matches: [], busy: false });
        this.timer = null;
        this.asked = 0;
        onWillUnmount(() => clearTimeout(this.timer));
    }

    /** The exact technician the typed name already is, if any. */
    get same() {
        return this.state.matches.find((m) => m.exact && m.technician) || null;
    }

    get canCreate() {
        return !this.state.busy && this.state.name.replace(/[^0-9a-z]/gi, "").length >= 2;
    }

    get createLabel() {
        return this.same ? _t("Add %s", this.same.name) : _t("Create & add");
    }

    onName(ev) {
        this.state.name = ev.target.value;
        clearTimeout(this.timer);
        this.timer = setTimeout(() => this.lookup(), 220);
    }

    async lookup() {
        const ask = ++this.asked;
        const matches = this.state.name.trim() ? await this.props.lookup(this.state.name) : [];
        if (ask === this.asked) {
            this.state.matches = matches;
        }
    }

    onKeydown(ev) {
        if (ev.key === "Enter" && this.canCreate) {
            ev.preventDefault();
            this.create();
        } else if (ev.key === "Escape") {
            this.props.close();
        }
    }

    async create() {
        if (!this.canCreate) {
            return;
        }
        if (this.same) {
            return this.pick(this.same);
        }
        this.state.busy = true;
        try {
            await this.props.create(this.state.name, this.state.phone);
            this.props.close();
        } finally {
            this.state.busy = false;
        }
    }

    async pick(match) {
        this.state.busy = true;
        try {
            await this.props.pick(match);
            this.props.close();
        } finally {
            this.state.busy = false;
        }
    }
}

/**
 * The order's Technicians: the usual tags, a "new technician" button beside them,
 * and the technicians orders of the same department named most in the last two
 * months, one tap each - the counter fills this field on every order, and the same
 * few people do most of a department's work.
 */
export class TechnicianTagsField extends Component {
    static template = "sale_custom.TechnicianTagsField";
    static components = { Many2ManyTagsField };
    props = useProps(many2ManyTagsFieldProps);

    newRef = signal.ref();

    setup() {
        this.orm = useService("orm");
        this.state = proxy({ suggestions: [], scope: "lab" });
        this.popover = usePopover(NewTechnicianPopover, {
            position: "bottom-end",
            popoverClass: "o_lab_tech_popover",
        });
        const { saveRecord } = useX2ManyCrud(() => this.props.record.data[this.props.name], true);
        this.saveRecord = saveRecord;
        this.asked = 0;
        useRecordObserver((record) => {
            const department = record.data.department_id;
            const chosen = record.data[this.props.name].currentIds;
            if (!this.props.readonly) {
                this.loadSuggestions(department && department.id, [...chosen]);
            }
        });
    }

    // The order's department, where the form shows it (lab_cost_centre adds it; this
    // module does not depend on that one, so it is read, never declared).
    get departmentName() {
        const department = this.props.record.data.department_id;
        return (department && department.display_name) || "";
    }

    async loadSuggestions(departmentId, chosen) {
        const ask = ++this.asked;
        const result = await this.orm.call("sale.order", "lab_technician_suggestions", [], {
            department_id: departmentId || false,
            exclude_ids: chosen,
        });
        if (ask === this.asked) {
            this.state.suggestions = result.technicians;
            this.state.scope = result.scope;
        }
    }

    async add(id) {
        if (!this.props.record.data[this.props.name].currentIds.includes(id)) {
            await this.saveRecord([id]);
        }
    }

    openNew() {
        if (this.popover.isOpen) {
            return this.popover.close();
        }
        this.popover.open(this.newRef(), {
            lookup: (name) => this.orm.call("res.partner", "lab_technician_lookup", [name]),
            create: async (name, phone) => {
                const made = await this.orm.call("res.partner", "lab_create_technician", [name], {
                    phone: phone || false,
                });
                await this.add(made.id);
            },
            pick: async (match) => {
                if (!match.technician) {
                    await this.orm.call("res.partner", "lab_mark_technician", [[match.id]]);
                }
                await this.add(match.id);
            },
        });
    }
}

export const technicianTagsField = {
    ...many2ManyTagsField,
    component: TechnicianTagsField,
    displayName: _t("Technicians, with a new-technician button"),
    additionalClasses: ["o_field_many2many_tags"],
};

registry.category("fields").add("technician_tags", technicianTagsField);
