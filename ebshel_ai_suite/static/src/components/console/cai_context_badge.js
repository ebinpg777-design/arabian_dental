import { Component } from "@odoo/owl";

/** Shows which record the assistant can see, with a switch to leave it out. */
export class CaiContextBadge extends Component {
    static template = "ebshel_ai_suite.CaiContextBadge";
    static props = {
        context: { type: [Object, { value: null }], optional: true },
        enabled: Boolean,
        locked: Boolean,
        onToggle: Function,
    };
}
