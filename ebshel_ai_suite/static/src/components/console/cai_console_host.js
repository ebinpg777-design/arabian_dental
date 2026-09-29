import { Component, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { CommunityAIConsole } from "./cai_console";

/** Mounted once in the web client; shows the console as a side panel when opened. */
export class CommunityAIConsoleHost extends Component {
    static template = "ebshel_ai_suite.CommunityAIConsoleHost";
    static components = { CommunityAIConsole };
    static props = {};

    setup() {
        this.bridge = useService("communityAiBridge");
        this.state = useState(this.bridge.state);
    }
}

/** Full-page console (Ebshel AI › Assistant). */
export class CommunityAIConsolePage extends Component {
    static template = "ebshel_ai_suite.CommunityAIConsolePage";
    static components = { CommunityAIConsole };
    static props = ["*"];
}

registry.category("main_components").add("ebshel_ai_suite.ConsoleHost", {
    Component: CommunityAIConsoleHost,
});
registry.category("actions").add("ebshel_ai_suite.console_page", CommunityAIConsolePage);
