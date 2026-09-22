/** @odoo-module **/

import { registry } from "@web/core/registry";

/**
 * The floor's report was "the popup is not coming", which is exactly the half a
 * server-side test cannot see. So this presses the button the bench presses and
 * insists the question appears, with a name on it to tap. (client, 2026-09-09)
 */
registry.category("web_tour.tours").add("lab_finisher_popup", {
    steps: () => [
        {
            content: "the board has the job on the bench",
            trigger: ".o_st_go_hand",
            run: "click",
        },
        {
            content: "the second question must be on the screen",
            trigger: ".o_st_doing_dlg .modal-title:contains('finished')",
        },
        {
            content: "with the bench's people to tap",
            trigger: ".o_st_doing_dlg .o_st_tile",
        },
    ],
});
