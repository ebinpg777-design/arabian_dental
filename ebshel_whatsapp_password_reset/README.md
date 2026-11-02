# Password Reset on WhatsApp (Odoo 20)

A **Reset Password on WhatsApp** button in the header of the user form. It makes the
one-time link Odoo's "Send Password Reset" would email and opens WhatsApp with a ready
message to the user:

> Hello Field Person, set a new password for your Your Company login (field_person) with
> this link: https://…/web/reset_password?…  The link works once, within 4 hours.

* **The number:** the contact's WhatsApp number where a module adds one
  (`res.partner.whatsapp_number`), else the employee's work mobile or work phone, else the
  contact's phone - written the way WhatsApp wants it (`+91 98097 81772`, `098097 81772`
  and `9809781772` with the company in India all become `919809781772`). Without one,
  WhatsApp asks whom to send it to.
* **Who:** administrators (Access Rights) only; never for yourself or an archived user -
  Odoo's own rules for a reset link.
* **The link:** Odoo's own reset link; it works once, for
  `auth_signup.reset_password.validity.hours` (4 by default).
* **Any browser:** Odoo's reset page refuses the new password with "400: Bad Request -
  Session expired (invalid CSRF token)" when the browser comes back with another session
  than the one the page was given - every time, on a phone whose browser keeps no cookies
  for the site. A link with a reset token is accepted on that token alone (one-time,
  signed, expiring: whoever holds it can set the password anyway); asking for a reset
  email by login still needs the page's form token (`controllers/main.py`).
* **The record:** the user's chatter notes that it was sent and to which number - never
  the link.

Depends only on `auth_signup`. Opens `https://wa.me/...`: WhatsApp on a phone, WhatsApp
Web or the desktop app on a computer.
