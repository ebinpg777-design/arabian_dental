import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("ebshel_ai_suite_console_tour", {
    url: "/odoo",
    steps: () => [
        { trigger: ".o_cai_launcher button", run: "click" },
        { trigger: ".o_cai_console .o_cai_input", run: "edit Hello from the tour" },
        { trigger: ".o_cai_console .o_cai_send:enabled", run: "click" },
        { trigger: ".o_cai_message_assistant .o_cai_markdown:contains('You said: Hello from the tour')" },
        { trigger: ".o_cai_console .o_cai_input", run: 'edit [[call:create_contact {"values": {"name": "Tour Created Contact"}}]]' },
        { trigger: ".o_cai_console .o_cai_send:enabled", run: "click" },
        { trigger: ".o_cai_operation .o_cai_approve:enabled", run: "click" },
        { trigger: ".o_cai_operation .o_cai_operation_state:contains('executed')" },
        { trigger: ".o_cai_console .o_cai_close", run: "click" },
        { trigger: "body:not(:has(.o_cai_console_host))" },
    ],
});

registry.category("web_tour.tours").add("ebshel_ai_suite_page_tour", {
    url: "/odoo/action-ebshel_ai_suite.action_cai_console_page",
    steps: () => [
        { trigger: ".o_cai_console_page .o_cai_input", run: "edit What does our warranty cover?" },
        { trigger: ".o_cai_console_page .o_cai_send:enabled", run: "click" },
        { trigger: ".o_cai_message_assistant .o_cai_sources:contains('Warranty Policy')" },
        { trigger: ".o_cai_history_item:contains('What does our warranty cover?')" },
    ],
});

registry.category("web_tour.tours").add("ebshel_ai_suite_assist_field_tour", {
    steps: () => [
        { trigger: ".o_notebook a[name='internal_notes']", run: "click" },
        { trigger: ".o_cai_assist_field .o_cai_assist_toggle", run: "click" },
        { trigger: ".o-dropdown-item:contains('Shorten')", run: "click" },
        { trigger: ".o_cai_assist_field:contains('[mock]')" },
        { trigger: ".o_form_button_save", run: "click" },
        { trigger: ".o_form_saved" },
        // the console opened from a form shows (and can drop) the record context
        { trigger: ".o_cai_launcher button", run: "click" },
        { trigger: ".o_cai_context:contains('Tour Widget Partner')" },
        { trigger: ".o_cai_console .o_cai_input", run: "edit Summarize this record" },
        { trigger: ".o_cai_console .o_cai_send:enabled", run: "click" },
        { trigger: ".o_cai_message_assistant .o_cai_markdown:contains('Summarize this record')" },
        { trigger: ".o_cai_context:contains('shared with the assistant')" },
    ],
});
