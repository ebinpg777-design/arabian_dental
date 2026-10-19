import { patch } from "@web/core/utils/patch";
import { FormController } from "@web/views/form/form_controller";

/**
 * Odoo 20's form saves itself when the browser tab is hidden, and reads
 * `this.model.root` to decide whether there is anything to save. A form whose
 * record has not arrived yet - an invoice opened on a slow line, the tab switched
 * while it loads - or never arrived has no root, and the save crashed with
 * "Cannot read properties of undefined (reading 'activeFields')", an Oops dialog
 * the user met on coming back (arabian-dental-lab.org, 2026-10-19, on an invoice).
 * With no record there is nothing to save.
 */
patch(FormController.prototype, {
    async beforeVisibilityChange() {
        if (!this.model.root) {
            return;
        }
        return super.beforeVisibilityChange(...arguments);
    },
});
