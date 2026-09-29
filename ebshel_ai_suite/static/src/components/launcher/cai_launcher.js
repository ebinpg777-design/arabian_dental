import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

/** Systray button opening the AI console (also reachable from the command palette). */
export class CommunityAILauncher extends Component {
    static template = "ebshel_ai_suite.CommunityAILauncher";
    static props = {};

    setup() {
        this.bridge = useService("communityAiBridge");
        this.state = useState(this.bridge.state);
        onWillStart(() => this.bridge.bootstrap());
    }

    onClick() {
        this.bridge.toggle();
    }
}

registry.category("systray").add("ebshel_ai_suite.launcher", { Component: CommunityAILauncher }, { sequence: 35 });
