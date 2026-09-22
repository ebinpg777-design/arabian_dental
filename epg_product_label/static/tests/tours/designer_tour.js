import { registry } from "@web/core/registry";

/**
 * Drives the label designer the way a person would: open the form, grab the
 * element on the canvas, drag it, and save. The Python side then checks that the
 * new coordinates reached the database.
 */
// No `url` here on purpose: the Python side starts the tour on the template form,
// and a url declared on the tour would send the browser back to the home action.
registry.category("web_tour.tours").add("epg_label_designer_tour", {
    steps: () => [
        {
            content: "the canvas is rendered",
            trigger: ".epg_designer .epg_designer_canvas",
            run: () => {},
        },
        {
            content: "the element is drawn on the canvas",
            trigger: ".epg_designer_element",
            run: () => {},
        },
        {
            content: "drag the element down and to the right",
            trigger: ".epg_designer_element",
            run() {
                const node = this.anchor;
                const box = node.getBoundingClientRect();
                const from = {
                    clientX: box.left + box.width / 2,
                    clientY: box.top + box.height / 2,
                };
                const options = { bubbles: true, pointerId: 1, isPrimary: true };
                node.dispatchEvent(new PointerEvent("pointerdown", { ...options, ...from }));
                const stage = node.closest(".epg_designer_stage");
                for (const step of [20, 40, 60]) {
                    stage.dispatchEvent(
                        new PointerEvent("pointermove", {
                            ...options,
                            clientX: from.clientX + step,
                            clientY: from.clientY + step,
                        })
                    );
                }
                stage.dispatchEvent(
                    new PointerEvent("pointerup", {
                        ...options,
                        clientX: from.clientX + 60,
                        clientY: from.clientY + 60,
                    })
                );
            },
        },
        {
            content: "the quick property strip picks up the selection",
            trigger: ".epg_designer_props",
            run: () => {},
        },
        {
            content: "save the template",
            trigger: ".o_form_button_save",
            run: "click",
        },
        {
            content: "the record is saved",
            trigger: ".o_form_saved",
            run: () => {},
        },
    ],
});
