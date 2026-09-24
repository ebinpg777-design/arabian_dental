/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { formatFloat } from "@web/core/utils/numbers";
import { registry } from "@web/core/registry";
import { useRecordObserver } from "@web/model/relational_model/utils";

import { formatWithUnit } from "../core/number_format";

// Three magnitudes, so every number system shows what it does.
const SAMPLES = [12.5, 3456, 1234567];

/**
 * How the card's number will read, before it is saved: the format settings
 * applied to three sample values, exactly as the card applies them.
 */
export class NumberSampleWidget extends Component {
    static template = "ebshel_dashboard.NumberSample";
    static props = {
        record: Object,
        readonly: { type: Boolean, optional: true },
    };

    setup() {
        this.state = useState({ samples: [] });
        useRecordObserver((record) => {
            this.state.samples = this.build(record.data);
        });
    }

    build(data) {
        const item = {
            aggregate: data.aggregate,
            kind: data.kind,
            digits: data.digits,
            number_system: data.number_system,
            prefix: data.prefix,
            symbol: data.symbol,
        };
        const factor = data.multiplier && ![0, 1].includes(data.multiplier) ? data.multiplier : 1;
        return SAMPLES.map((raw) => ({
            raw: formatFloat(raw, { digits: [16, raw % 1 ? 1 : 0] }),
            text: formatWithUnit(raw * factor, item),
        }));
    }

    get samples() {
        return this.state.samples;
    }
}

export const numberSampleWidget = {
    component: NumberSampleWidget,
};

registry.category("view_widgets").add("dashboard_number_sample", numberSampleWidget);
