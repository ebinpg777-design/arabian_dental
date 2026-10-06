/** @odoo-module **/

/**
 * Icons on Odoo 20.
 *
 * FontAwesome is gone: an icon is a Material Symbols name ("bar_chart",
 * "local_shipping") or an "oi_*" name of Odoo's own icon font, drawn as
 * `<i class="oi" t-att-data-icon="name"/>`. The server translates the old
 * FontAwesome names on the way in (models/dashboard_icons.py), so a stored icon
 * is always one of those; this only covers what has not been saved yet - a
 * name still being typed in the editor - and the empty value.
 */
export function iconName(icon, fallback = "widgets") {
    const name = String(icon || "").trim();
    if (!name || name.startsWith("fa-") || name.startsWith("fa ")) {
        return fallback;
    }
    return name;
}
