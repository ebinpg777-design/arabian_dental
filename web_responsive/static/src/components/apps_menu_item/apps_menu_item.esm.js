/* Copyright 2018 Tecnativa - Jairo Llopis
 * Copyright 2021 ITerra - Sergey Shebanin
 * Copyright 2023 Onestein - Anjeel Haria
 * Copyright 2023 Taras Shabaranskyi
 * License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl). */

import {Component, onWillUpdateProps, t, useProps} from "@odoo/owl";
import {getWebIconData} from "@web_responsive/components/apps_menu_tools.esm";

export class AppMenuItem extends Component {
    static template = "web_responsive.AppMenuItem";
    props = useProps({
        app: t.object(),
        href: t.string(),
        // null while no app is open
        currentApp: t.or([t.object(), t.literal(null)]).optional(),
        onClick: t.function(),
    });

    setup() {
        this.webIconData = getWebIconData(this.props.app);
        onWillUpdateProps(this.onUpdateProps);
    }

    get isActive() {
        const {currentApp} = this.props;
        return currentApp && currentApp.id === this.props.app.id;
    }

    get className() {
        const classItems = ["o-app-menu-item"];
        if (this.isActive) {
            classItems.push("active");
        }
        return classItems.join(" ");
    }

    onUpdateProps(nextProps) {
        this.webIconData = getWebIconData(nextProps.app);
    }

    onClick() {
        if (typeof this.props.onClick === "function") {
            this.props.onClick(this.props.app);
        }
    }
}
