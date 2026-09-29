import { Component, useState } from "@odoo/owl";

/**
 * Confirmation card for an operation prepared by the assistant.
 * Nothing is executed until the user presses "Confirm".
 */
export class CaiActionDialogCard extends Component {
    static template = "ebshel_ai_suite.CaiActionDialogCard";
    static props = {
        operation: Object,
        busy: Boolean,
        onDecide: Function,
    };

    setup() {
        this.state = useState({ sending: false });
    }

    async decide(approve) {
        this.state.sending = true;
        try {
            await this.props.onDecide(this.props.operation, approve);
        } finally {
            this.state.sending = false;
        }
    }
}
