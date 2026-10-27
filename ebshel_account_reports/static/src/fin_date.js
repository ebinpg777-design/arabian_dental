/** @odoo-module **/
/**
 * Dates on the accounting screens, the way the company writes them (Settings >
 * Accounting > Dynamic Reports > Date format) - not each reader's language, and
 * never the "2026-12-31" a screen receives from the server.
 * (client, 2026-10-27: "the date format should be based on the company's date format")
 */
import { user } from "@web/core/user";
import { deserializeDate, deserializeDateTime } from "@web/core/l10n/dates";
import { session } from "@web/session";

const FALLBACK = "dd/MM/yyyy";

/** The active company's date format, as the browser's date library writes it. */
export function finDateFormat() {
    const formats = session.ebshel_fin_date_formats || {};
    const company = user.activeCompany && user.activeCompany.id;
    return formats[company] || Object.values(formats)[0] || FALLBACK;
}

/**
 * "2026-12-31" (or "2026-12-31 09:30:00", in UTC) the company's way, a time
 * added for the second. Anything that is not such a date comes back as it was.
 */
export function finDate(value) {
    if (!value || typeof value !== "string") {
        return value || "";
    }
    const withTime = value.length > 10;
    const day = withTime ? deserializeDateTime(value) : deserializeDate(value);
    if (!day.isValid) {
        return value;
    }
    const text = day.toFormat(finDateFormat());
    return withTime ? `${text} ${day.toFormat("HH:mm")}` : text;
}
