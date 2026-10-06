import { Component, proxy, t, useProps } from "@odoo/owl";

/**
 * Confirmation card for an operation prepared by the assistant.
 * Nothing is executed until the user presses "Confirm".
 */
export class CaiActionDialogCard extends Component {
    static template = "ebshel_ai_suite.CaiActionDialogCard";
    props = useProps({
        operation: t.object(),
        busy: t.boolean(),
        onDecide: t.function(),
    });

    setup() {
        this.state = proxy({ sending: false });
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
