/** @odoo-module **/

import { Component, useRef, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";
import { formatMonetary } from "@web/views/fields/formatters";
import { isBinarySize } from "@web/core/utils/binary";
import { imageUrl } from "@web/core/utils/urls";
import { fileTypeMagicWordMap } from "@web/views/fields/image/image_field";
import { getFieldDomain } from "@web/model/relational_model/utils";
import { useSpecialData } from "@web/views/fields/relational_utils";

/**
 * Widgets for a person filling this in one-handed, standing at a clinic door.
 *
 * The shared constraint: **never make the phone keyboard appear if a tap will do.** The
 * on-screen keyboard covers half the display, moves the field being edited, and is the
 * single biggest reason field staff stop filling records in properly. Every widget here
 * exists to replace a keyboard or a dropdown with a target big enough to hit while
 * walking.
 */

// ---------------------------------------------------------------------------
// A selection as big tappable chips.
// ---------------------------------------------------------------------------
export class FwChoice extends Component {
    static template = "lab_fieldwork.FwChoice";
    static props = { ...standardFieldProps };

    get choices() {
        return this.props.record.fields[this.props.name].selection.filter(
            ([value]) => value
        );
    }

    get value() {
        return this.props.record.data[this.props.name];
    }

    onPick(value) {
        if (this.props.readonly) {
            return;
        }
        // Tapping the current choice clears it, so a mis-tap is undone with the same
        // finger rather than by finding a dropdown and scrolling to the empty row.
        this.props.record.update({
            [this.props.name]: this.value === value ? false : value,
        });
    }
}

export const fwChoice = {
    component: FwChoice,
    displayName: _t("Touch choice"),
    supportedTypes: ["selection"],
    isEmpty: (record, fieldName) => record.data[fieldName] === false,
};
registry.category("fields").add("fw_choice", fwChoice);

// ---------------------------------------------------------------------------
// The same chip, for a many2many: several purposes really can be true of one
// visit at once — deliver AND collect a new impression AND take payment — so this
// toggles membership instead of picking one. Visually identical to FwChoice on
// purpose: one concept (a set of tappable chips), one widget family, so a person who
// has learned one has learned the other.
// ---------------------------------------------------------------------------
export class FwChoiceMulti extends Component {
    static template = "lab_fieldwork.FwChoiceMulti";
    static props = { ...standardFieldProps, domain: { type: [Array, Function], optional: true } };

    setup() {
        this.specialData = useSpecialData((orm, props) => {
            const { relation } = props.record.fields[props.name];
            const domain = getFieldDomain(props.record, props.name, props.domain);
            // sequence order, so the chips read in the same order as everywhere
            // else this master is shown (kanban, list, config screen).
            return orm.searchRead(relation, domain, ["display_name"], {
                order: "sequence, id",
                context: this.props.context || {},
            });
        });
    }

    get choices() {
        return this.specialData.data;
    }

    get currentIds() {
        return this.props.record.data[this.props.name].currentIds;
    }

    isOn(id) {
        return this.currentIds.includes(id);
    }

    onPick(id) {
        if (this.props.readonly) {
            return;
        }
        const on = this.isOn(id);
        this.props.record.data[this.props.name].addAndRemove({
            add: on ? [] : [id],
            remove: on ? [id] : [],
        });
    }
}

registry.category("fields").add("fw_choice_multi", {
    component: FwChoiceMulti,
    displayName: _t("Touch choice (multi)"),
    supportedTypes: ["many2many"],
    extractProps: ({ options }) => ({ domain: options?.domain }),
});

// ---------------------------------------------------------------------------
// Money, without the keyboard where possible.
// ---------------------------------------------------------------------------
export class FwAmount extends Component {
    static template = "lab_fieldwork.FwAmount";
    static props = { ...standardFieldProps };

    setup() {
        this.input = useRef("input");
    }

    get value() {
        return this.props.record.data[this.props.name] || 0;
    }

    get currencyId() {
        const currency = this.props.record.data.currency_id;
        return currency && (currency.id || currency[0]);
    }

    get display() {
        return formatMonetary(this.value, { currencyId: this.currencyId });
    }

    // Notes people are actually handed. Cash at a clinic arrives in round numbers, so
    // four taps beat a keyboard for almost every real amount.
    get steps() {
        return [100, 500, 1000, 5000];
    }

    add(step) {
        if (this.props.readonly) {
            return;
        }
        this.props.record.update({ [this.props.name]: this.value + step });
    }

    clear() {
        if (!this.props.readonly) {
            this.props.record.update({ [this.props.name]: 0 });
        }
    }

    onInput(ev) {
        const parsed = parseFloat(ev.target.value.replace(/[^0-9.]/g, ""));
        this.props.record.update({
            [this.props.name]: Number.isFinite(parsed) ? parsed : 0,
        });
    }
}

export const fwAmount = {
    component: FwAmount,
    displayName: _t("Touch amount"),
    supportedTypes: ["monetary", "float"],
};
registry.category("fields").add("fw_amount", fwAmount);

// ---------------------------------------------------------------------------
// A photo, straight from the rear camera.
// ---------------------------------------------------------------------------
export class FwCamera extends Component {
    static template = "lab_fieldwork.FwCamera";
    static props = { ...standardFieldProps };

    setup() {
        this.notification = useService("notification");
        this.state = useState({ busy: false });
    }

    get value() {
        return this.props.record.data[this.props.name];
    }

    /**
     * Where the picture actually lives.
     *
     * A saved binary field does NOT hand back its bytes: Odoo returns a SIZE string
     * ("12.50 Kb") under bin_size, so treating the value as base64 produced
     * `data:image/png;base64,12.50 Kb` and a broken image on every stored photo — while
     * a freshly captured one, which really is base64, displayed perfectly. That is why
     * this only failed after saving.
     *
     * So: a size string means the bytes are on the server, and the image is fetched by
     * URL; anything else is the raw base64 still sitting in the browser. `write_date` is
     * the cache key, or the old photo survives a retake.
     */
    get src() {
        const value = this.value;
        if (!value) {
            return "";
        }
        if (isBinarySize(value)) {
            return imageUrl(
                this.props.record.resModel,
                this.props.record.resId,
                this.props.name,
                { unique: this.props.record.data.write_date }
            );
        }
        if (typeof value === "string" && value.startsWith("data:")) {
            return value;
        }
        // The magic word: the first base64 character identifies the format, so a JPEG
        // straight off a phone camera is not mislabelled as a PNG.
        const magic = fileTypeMagicWordMap[value[0]] || "png";
        return `data:image/${magic};base64,${value}`;
    }

    async onFile(ev) {
        const file = ev.target.files && ev.target.files[0];
        if (!file) {
            return;
        }
        this.state.busy = true;
        try {
            const data = await new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = () => resolve(reader.result.split(",")[1]);
                reader.onerror = reject;
                reader.readAsDataURL(file);
            });
            await this.props.record.update({ [this.props.name]: data });
        } catch {
            this.notification.add(_t("That photo could not be read. Try again."), {
                type: "warning",
            });
        } finally {
            this.state.busy = false;
            ev.target.value = "";
        }
    }

    remove() {
        this.props.record.update({ [this.props.name]: false });
    }
}

export const fwCamera = {
    component: FwCamera,
    displayName: _t("Camera capture"),
    supportedTypes: ["binary"],
    isEmpty: (record, fieldName) => !record.data[fieldName],
};
registry.category("fields").add("fw_camera", fwCamera);

// ---------------------------------------------------------------------------
// Navigate, call, WhatsApp — the three things done with a phone, not in a form.
// ---------------------------------------------------------------------------
export class FwContactBar extends Component {
    static template = "lab_fieldwork.FwContactBar";
    static props = { ...standardWidgetProps };

    get data() {
        return this.props.record.data;
    }

    get mapUrl() {
        return this.data.map_url || "";
    }

    get telUrl() {
        return this.data.call_number ? `tel:${this.data.call_number}` : "";
    }

    get waUrl() {
        return this.data.whatsapp_number
            ? `https://wa.me/${this.data.whatsapp_number}`
            : "";
    }
}

export const fwContactBar = {
    component: FwContactBar,
    fieldDependencies: [
        { name: "map_url", type: "char" },
        { name: "call_number", type: "char" },
        { name: "whatsapp_number", type: "char" },
    ],
};
registry.category("view_widgets").add("fw_contact_bar", fwContactBar);

// ---------------------------------------------------------------------------
// Upper / lower, as the arches themselves.
// ---------------------------------------------------------------------------
export class FwArch extends Component {
    static template = "lab_fieldwork.FwArch";
    static props = { ...standardFieldProps };

    get value() {
        return this.props.record.data[this.props.name];
    }

    has(arch) {
        return this.value === arch || this.value === "ul";
    }

    /**
     * Two independent arches that combine, rather than a three-way choice.
     *
     * "U", "L" and "UL" as three separate options is how the data is stored, but it is
     * not how anyone thinks: a technician looks at a slip and asks "is the upper in this
     * job, is the lower in it". Selecting both to mean UL needs no explaining, and it
     * makes the impossible fourth state — neither — unreachable rather than merely
     * discouraged.
     */
    toggle(arch) {
        if (this.props.readonly) {
            return;
        }
        const upper = arch === "upper" ? !this.has("upper") : this.has("upper");
        const lower = arch === "lower" ? !this.has("lower") : this.has("lower");
        let value = false;
        if (upper && lower) {
            value = "ul";
        } else if (upper) {
            value = "upper";
        } else if (lower) {
            value = "lower";
        } else {
            // Untoggling the last arch would leave a required field empty, so the tap
            // moves to the other arch instead of clearing the line.
            value = arch === "upper" ? "lower" : "upper";
        }
        this.props.record.update({ [this.props.name]: value });
    }

    get label() {
        return { upper: "Upper", lower: "Lower", ul: "Upper + Lower" }[this.value] || "—";
    }
}

export const fwArch = {
    component: FwArch,
    displayName: _t("Dental arch"),
    supportedTypes: ["selection"],
};
registry.category("fields").add("fw_arch", fwArch);

// ---------------------------------------------------------------------------
// A quantity, without the keyboard.
// ---------------------------------------------------------------------------
export class FwStepper extends Component {
    static template = "lab_fieldwork.FwStepper";
    static props = { ...standardFieldProps };

    get value() {
        return this.props.record.data[this.props.name] || 0;
    }

    get display() {
        // Whole numbers are the normal case for appliances; only show a decimal when
        // one is genuinely there.
        return Number.isInteger(this.value) ? this.value : this.value.toFixed(2);
    }

    step(by) {
        if (this.props.readonly) {
            return;
        }
        this.props.record.update({
            [this.props.name]: Math.max(0, this.value + by),
        });
    }

    /**
     * The number is typed as well as stepped.
     *
     * Stepping alone is fine for a quantity, which is almost always 1 or 2, and absurd
     * for an age: reaching 34 is thirty-four taps. The buttons are for the common small
     * adjustment and the field is for everything else — and it asks the phone for a
     * numeric pad, so typing it is still one keyboard shorter than a plain field.
     */
    onInput(ev) {
        if (this.props.readonly) {
            return;
        }
        const isInt = this.props.record.fields[this.props.name].type === "integer";
        const raw = (ev.target.value || "").replace(/[^0-9.]/g, "");
        const parsed = isInt ? parseInt(raw, 10) : parseFloat(raw);
        const value = Number.isFinite(parsed) ? Math.max(0, parsed) : 0;
        this.props.record.update({ [this.props.name]: value });
        // Put the cleaned value back, so "abc" does not sit in the box reading as data.
        ev.target.value = value;
    }
}

export const fwStepper = {
    component: FwStepper,
    displayName: _t("Stepper"),
    supportedTypes: ["integer", "float"],
};
registry.category("fields").add("fw_stepper", fwStepper);

// ---------------------------------------------------------------------------
// A row of booleans as toggle chips.
// ---------------------------------------------------------------------------
export class FwToggles extends Component {
    static template = "lab_fieldwork.FwToggles";
    static props = {
        ...standardWidgetProps,
        fieldNames: { type: Array },
    };

    get items() {
        const record = this.props.record;
        return this.props.fieldNames
            .filter((name) => name in record.fields)
            .map((name) => ({
                name,
                label: record.fields[name].string,
                on: !!record.data[name],
            }));
    }

    toggle(item) {
        if (this.props.readonly) {
            return;
        }
        this.props.record.update({ [item.name]: !item.on });
    }
}

export const fwToggles = {
    component: FwToggles,
    extractProps: ({ attrs }) => ({
        fieldNames: (attrs.fields || "").split(",").map((f) => f.trim()).filter(Boolean),
    }),
    fieldDependencies: [],
};
registry.category("view_widgets").add("fw_toggles", fwToggles);

// ---------------------------------------------------------------------------
// A flag that has to be noticed.
// ---------------------------------------------------------------------------
export class FwFlag extends Component {
    static template = "lab_fieldwork.FwFlag";
    static props = { ...standardFieldProps, icon: { type: String, optional: true } };

    get on() {
        return !!this.props.record.data[this.props.name];
    }

    get field() {
        return this.props.record.fields[this.props.name];
    }

    toggle() {
        if (!this.props.readonly) {
            this.props.record.update({ [this.props.name]: !this.on });
        }
    }
}

export const fwFlag = {
    component: FwFlag,
    displayName: _t("Attention flag"),
    supportedTypes: ["boolean"],
    extractProps: ({ options }) => ({ icon: options.icon }),
};
registry.category("fields").add("fw_flag", fwFlag);
