import { Component, t, useProps } from "@odoo/owl";

/** Shows which record the assistant can see, with a switch to leave it out. */
export class CaiContextBadge extends Component {
    static template = "ebshel_ai_suite.CaiContextBadge";
    props = useProps({
        context: t.or([t.object(), t.literal(null)]).optional(),
        enabled: t.boolean(),
        locked: t.boolean(),
        onToggle: t.function(),
    });
}
