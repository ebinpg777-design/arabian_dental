/* Copyright 2023 Taras Shabaranskyi
 * License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl). */

import {animationFrame, beforeEach, expect, runAllTimers, test} from "@odoo/hoot";
import {
    contains,
    defineMenus,
    getService,
    mountWithCleanup,
} from "@web/../tests/web_test_helpers";
import {NavBar} from "@web/webclient/navbar/navbar";
import {defineMailModels} from "@mail/../tests/mail_test_helpers";

defineMailModels();

beforeEach(() => {
    defineMenus([
        {
            id: 1,
            name: "App0",
            appID: 1,
            actionID: 101,
            xmlid: "menu_1",
            children: [{id: 11, name: "Orders", appID: 1, actionID: 111, xmlid: "menu_11"}],
        },
        {id: 2, name: "App1", appID: 2, actionID: 102, xmlid: "menu_2"},
    ]);
});

test.tags("desktop");
test("the apps menu button replaces the apps dropdown", async () => {
    await mountWithCleanup(NavBar);
    expect(".o_grid_apps_menu button.o_grid_apps_menu__button").toHaveCount(1);
    expect(".o_navbar_apps_menu .dropdown-toggle").toHaveCount(0);
});

test.tags("desktop");
test("the apps menu opens and closes", async () => {
    await mountWithCleanup(NavBar);
    await contains("button.o_grid_apps_menu__button").click();
    expect(".o-app-menu-list").toHaveCount(1);
    expect(".o-app-menu-list .o-app-menu-item").toHaveCount(2);
    await contains("button.o_grid_apps_menu__button").click();
    expect(".o-app-menu-list").toHaveCount(0);
});

test.tags("desktop");
test("the current app is marked active", async () => {
    await mountWithCleanup(NavBar);
    await contains("button.o_grid_apps_menu__button").click();
    getService("menu").setCurrentMenu(1);
    await animationFrame();
    expect('.o-app-menu-item.active[data-menu-xmlid="menu_1"]').toHaveCount(1);
    getService("menu").setCurrentMenu(2);
    await animationFrame();
    expect('.o-app-menu-item.active[data-menu-xmlid="menu_2"]').toHaveCount(1);
    expect('.o-app-menu-item.active[data-menu-xmlid="menu_1"]').toHaveCount(0);
});

test.tags("desktop");
test("the search finds apps and their menus", async () => {
    await mountWithCleanup(NavBar);
    await contains("button.o_grid_apps_menu__button").click();
    expect(".app-menu-container .search-input input").toHaveCount(1);
    await contains(".app-menu-container .search-input input").edit("Orders", {confirm: false});
    await runAllTimers();
    await animationFrame();
    expect(".search-list .search-item").toHaveCount(1);
    expect(".search-list .search-item a").toHaveAttribute("href", "/odoo/action-111");
    expect(".search-list .search-item").toHaveText("App0 / Orders");
    await contains(".app-menu-container .search-input input").edit("zzz", {confirm: false});
    await runAllTimers();
    await animationFrame();
    expect(".search-list .empty-search-item").toHaveCount(1);
});
