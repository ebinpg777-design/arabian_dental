import {CommandPalette} from "@web/core/commands/command_palette";
import { UIPlugin } from "@web/core/ui/ui_plugin";
import {patch} from "@web/core/utils/patch";
import { proxy, usePlugin } from "@odoo/owl";

export const unpatchCommandPalette = patch(CommandPalette.prototype, {
    setup() {
        super.setup();
        this.ui = proxy(usePlugin(UIPlugin));
    },

    get small() {
        return this.ui.size < 2;
    },

    get contentClass() {
        return `o_command_palette ${this.small ? "" : "mt-5"}`;
    },
});
