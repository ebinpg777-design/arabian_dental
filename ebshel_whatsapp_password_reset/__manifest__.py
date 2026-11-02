# Part of ebshel_whatsapp_password_reset. See LICENSE file for full copyright and licensing details.
{
    "name": "Password Reset on WhatsApp",
    "version": "20.0.1.0.0",
    "category": "Hidden/Tools",
    "summary": "A button on the user form that opens WhatsApp with a fresh link to set a new "
    "password - for the people who read WhatsApp, not email.",
    "description": """
Password Reset on WhatsApp
==========================
Odoo sends a password reset link by email. Field staff, drivers and shop people
often never open their email, so the reset goes unread and the administrator ends
up reading a password out over the phone. "Reset Password on WhatsApp" on the user
form makes the same one-time link Odoo would email and opens WhatsApp with it in a
ready message to the user's number (their contact's WhatsApp number where a module
adds one, else their work mobile, else their phone); without a number, WhatsApp asks
whom to send it to. The link works once, for as long as the reset password validity
allows (4 hours by default). The user's chatter records that it was sent - never the
link itself.
    """,
    "author": "Ebshel Technologies",
    "maintainer": "Ebshel Technologies",
    "website": "https://ebshel.com",
    "support": "ebinpg777@gmail.com",
    "license": "OPL-1",
    "price": 9.0,
    "currency": "USD",
    "depends": ["auth_signup"],
    "data": [
        "views/res_users_views.xml",
    ],
    "installable": True,
    "application": False,
}
