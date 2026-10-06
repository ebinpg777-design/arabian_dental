/** @odoo-module **/

/**
 * Tile icons on Odoo 20.
 *
 * FontAwesome is gone: an icon is a Material Symbols name ("flash_on",
 * "local_shipping") or an "oi_*" name of Odoo's own icon font, drawn as
 * `<i class="oi" t-att-data-icon="name"/>`. The server translates the old
 * FontAwesome names on the way in (models/tile_icons.py), so a stored icon is
 * always one of those; this only covers what has not been saved yet - a name
 * still being typed in the editor - and the empty value.
 */

/** A tile with no icon at all. */
export const TILE_ICON_EMPTY = "radio_button_unchecked";
/** A FontAwesome class the server has not translated yet. */
export const TILE_ICON_FALLBACK = "filter_alt";

/** The name to put in `data-icon` for a tile's stored `icon`. */
export function tileIconName(icon) {
    const name = String(icon || "").trim();
    if (!name) {
        return TILE_ICON_EMPTY;
    }
    if (name.startsWith("fa-") || name.startsWith("fa ")) {
        return TILE_ICON_FALLBACK;
    }
    return name;
}
