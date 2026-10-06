import { patch } from "@web/core/utils/patch";
import { ViewButton } from "@web/views/view_button/view_button";

/**
 * Odoo 20 renders a button's ``icon`` as a Material Symbols name. A module
 * written for Odoo 19 says ``icon="fa-check"``; render those with Font Awesome.
 */
patch(ViewButton.prototype, {
    setup() {
        super.setup(...arguments);
        if (this.icon && typeof this.icon.name === "string" && this.icon.name.startsWith("fa-")) {
            this.icon = { ...this.icon, class: `o_button_icon fa ${this.icon.name}`, name: undefined };
        }
    },
});
