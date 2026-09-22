# -*- coding: utf-8 -*-
from lxml import etree

from odoo import api, fields, models
from odoo.tools import safe_eval
from odoo.tools.misc import file_path

# The lab's own moments. Generic WhatsApp knows nothing about these — epg_whatsapp only
# knows how to render a template and send it — so the vocabulary lives here.
EVENTS = [
    ('order_confirm', 'Case Registered'),
    ('ready', 'Ready for Dispatch'),
    ('dispatch', 'Dispatched'),
    ('invoice', 'Invoice Ready'),
    ('payment', 'Payment Received'),
    ('reminder', 'Payment Reminder'),
    ('feedback', 'Feedback Request'),
    # THE ENQUIRIES: what the lab asks a doctor, from their own contact. Each is a
    # question the bench is waiting on, and each is one click from the contact form.
    # (client, 2026-09-18)
    ('more_info', 'Enquiry: More Information Needed'),
    ('ask_impression', 'Enquiry: Impression / Scan'),
    ('ask_shade', 'Enquiry: Shade'),
    ('ask_pickup', 'Enquiry: Anything to Collect'),
    ('ask_approval', 'Enquiry: Approval to Proceed'),
    # ON HOLD: the case the bench has put down and cannot pick up again until
    # the doctor says something. Four ways of asking the same question, because
    # the right one depends on who is asking and how long it has been - the
    # counter picks. (client, 2026-09-19)
    ('hold_ask', 'On Hold: Can We Start?'),
    ('hold_details', 'On Hold: With the Case Details'),
    ('hold_nudge', 'On Hold: A Gentle Nudge'),
    ('hold_options', 'On Hold: Ask With Choices'),
    ('manual', 'Manual / Other'),
]

# The four ways of asking a doctor to release a held case, in the order the
# counter is likely to want them: the plain ask first, the choices last.
HOLD_EVENTS = ('hold_ask', 'hold_details', 'hold_nudge', 'hold_options')

# The fields the data file seeds and a refresh may bring up to date; the ones
# added even to a message the lab has rewritten, when they are still empty.
SEED_FIELDS = ('body', 'body_2', 'body_3', 'quick_replies', 'buttons', 'button_style',
               'sequence',
               'page_label', 'alert_replies',
               'header_text', 'footer_text', 'delay_hours', 'add_payment_link',
               'payment_amount_field', 'payment_reference_field', 'nudge_after_hours',
               'nudge_text', 'report_id')
ADDITIVE_FIELDS = ('quick_replies', 'buttons', 'report_id', 'add_payment_link',
                   'payment_amount_field', 'payment_reference_field', 'alert_replies',
                   'page_label')

# What the seeded messages said before this version - every text the seed ever
# carried (an older seed, the 19.0.2.8.0 one), as installs of different ages hold
# different ones. The refresh rewrites a template only while its message is still
# one of these, word for word.
PREVIOUS_SEED = {
    'tmpl_res_partner_manual': ("""{{name}}""",),
    'tmpl_stock_picking_manual': ("""{{origin}}: {{consignment_number}}""",),
    'tmpl_sale_order_manual': ("""{{name}}: {{patient}}""",),
    'tmpl_more_info': ("""❓ *A question about your new case*

Dear {{name|doctor}},

We have your case in hand, but we need one thing before the work can start:

• the *shade*
• the *bite / occlusion* notes
• the *date you need it by*

Tell us here, send a photo, or ask us to call - whichever is quickest for you. The moment we have it, the work goes on the bench.""",),
    'tmpl_order_confirm': (
        """🦷 *Case registered*

Dear {{partner_id.name|doctor}},

*{{patient}}* is with us and the work has started.

📋 *Case*      {{name}}
📅 *Received*  {{date_order|date}}

You will hear from us again the moment it is dispatched. Thank you for trusting us with it.""",
        """🦷 *Case registered*

Dear {{partner_id.name|doctor}},

The case for *{{patient}}* has reached the lab and is in production.

📋 *Case*      {{name}}
📅 *Received*  {{date_order|date}}

The acknowledgement is attached. You will hear from us again the moment it is dispatched.

Thank you for trusting us with it.""",
        """🦷 *Case registered*

Dear Dr. {{partner_id.name}},

We have received the case for *{{patient}}* and it is now in the lab.

*Reference*  {{name}}
*Received*   {{date_order}}

The acknowledgement is attached. We will message you again the moment the work is dispatched.

Thank you for trusting us with it.""",
        """🦷 *Case registered*

Dear Dr. {{partner_id.name}},

The case for *{{patient}}* has reached the lab and is in production.

📋 *Case*      {{name}}
📅 *Received*  {{date_order}}

The acknowledgement is attached. You will hear from us again the moment it is dispatched.

Thank you for trusting us with it.""",
    ),
    'tmpl_feedback': (
        """Dear {{partner_id.name|doctor}},

*{{name}}* for {{patient}} has been delivered. Was everything as it should be?

One tap is all it takes - and it shapes how we work.""",
        """Dear {{partner_id.name|doctor}},

Your case *{{name}}* for {{patient}} has been delivered. Was everything as it should be?

Tap a rating below - it takes a second, and it shapes how we work.""",
        "Dear Dr. {{partner_id.name}}, your case {{name}} (patient: {{patient}}) has been delivered. On a scale of 1-5, how would you rate our service? Just reply with a number. — Arabian Dental Lab",
        "Dear Dr. {{partner_id.name}}, your case {{name}} (patient: {{patient}}) has been delivered. How would you rate our service? Tap an answer below, or just reply. — Arabian Dental Lab",
        """Dear Dr. {{partner_id.name}},

Your case *{{name}}* for {{patient}} has been delivered. Was everything as it should be?

Tap a rating below - it takes a second, and it shapes how we work.""",
    ),
    'tmpl_dispatch': (
        """📦 *On its way*

Dear {{partner_id.name|doctor}},

Your work for *{{origin}}* has been dispatched.

🚚 *Courier*       {{courier_company}}
🔖 *Consignment*   {{consignment_number}}

Please check the contents on arrival and tell us how it reached you.""",
        """📦 *On its way*

Dear {{partner_id.name|doctor}},

Your work for *{{origin}}* has been dispatched.

🚚 *Courier*       {{courier_company}}
🔖 *Consignment*   {{consignment_number}}

The delivery slip is attached. Please check the contents on arrival and tap below.""",
        """Dear Dr. {{partner_id.name}},

Your work (*{{origin}}*) has been *dispatched*.
Courier: {{courier_company}}
Consignment No: {{consignment_number}}

Thank you for choosing Arabian Dental Lab.""",
        """📦 *On its way*

Dear Dr. {{partner_id.name}},

Your work for *{{origin}}* has been dispatched.

🚚 *Courier*       {{courier_company}}
🔖 *Consignment*   {{consignment_number}}

The delivery slip is attached. Please check the contents on arrival and tap below.""",
    ),
    'tmpl_invoice': (
        """🧾 *Invoice {{name}}*

Dear {{partner_id.name|doctor}},

💰 *Amount*  {{amount_total}}
📅 *Due by*  {{invoice_date_due}}

Thank you for your continued trust.""",
        """🧾 *Invoice {{name}}*

Dear {{partner_id.name|doctor}},

💰 *Amount*  {{amount_total}}
📅 *Due by*  {{invoice_date_due}}

The invoice itself, UPI payment and a one-tap reply are on the link below. Thank you for your continued trust.""",
        """🧾 *Invoice {{name}}*

Dear {{partner_id.name|doctor}},

Your tax invoice is attached.

💰 *Amount*  {{amount_total}}
📅 *Due by*  {{invoice_date_due}}

Reply here with any question on it - thank you for your continued trust.""",
        """🧾 *Invoice {{name}}*

Dear Dr. {{partner_id.name}},

Your invoice is attached to this message.

*Amount*  {{amount_total}} {{currency_id.name}}
*Due by*  {{invoice_date_due}}

Thank you for your continued trust.""",
        """🧾 *Invoice {{name}}*

Dear Dr. {{partner_id.name}},

Your tax invoice is attached.

💰 *Amount*  {{amount_total}} {{currency_id.name}}
📅 *Due by*  {{invoice_date_due}}

Reply here with any question on it - thank you for your continued trust.""",
    ),
    'tmpl_reminder': (
        """⏰ *Payment reminder*

Dear {{partner_id.name|doctor}},

Invoice *{{name}}* is still open.

💰 *Outstanding*  {{amount_residual}}
📅 *Was due*      {{invoice_date_due}}

If the payment has already gone out, one tap tells us and we will match it - thank you.""",
        """⏰ *Payment reminder*

Dear {{partner_id.name|doctor}},

Invoice *{{name}}* is still open.

💰 *Outstanding*  {{amount_residual}}
📅 *Was due*      {{invoice_date_due}}

The invoice is attached. If the payment has already gone out, tap "Paid already" and we will match it - thank you.""",
        """⏰ *Payment reminder*

Dear Dr. {{partner_id.name}},

Invoice *{{name}}* still shows an outstanding balance.

*Outstanding*  {{amount_residual}} {{currency_id.name}}
*Was due*      {{invoice_date_due}}

The invoice is attached for your reference. If you have already sent the payment, please ignore this message — and thank you.""",
        """⏰ *Payment reminder*

Dear Dr. {{partner_id.name}},

Invoice *{{name}}* is still open.

💰 *Outstanding*  {{amount_residual}} {{currency_id.name}}
📅 *Was due*      {{invoice_date_due}}

The invoice is attached. If the payment has already gone out, tap "Paid already" and we will match it - thank you.""",
    ),
    'tmpl_payment': (
        """✅ *Payment received*

Dear {{partner_id.name|doctor}},

Thank you - *{{amount}}* has reached us.

🔖 *Reference*  {{name}}
📅 *Date*       {{date}}""",
        """✅ *Payment received*

Dear {{partner_id.name|doctor}},

Thank you - your payment of *{{amount}}* has reached us.

🔖 *Reference*  {{name}}
📅 *Date*       {{date}}

The receipt is attached for your records.""",
        """Dear Dr. {{partner_id.name}},

We have received your payment of {{amount}} {{currency_id.name}}.
Reference: {{name}}

Thank you! — Arabian Dental Lab""",
        """✅ *Payment received*

Dear Dr. {{partner_id.name}},

Thank you - your payment of *{{amount}} {{currency_id.name}}* has reached us.

🔖 *Reference*  {{name}}
📅 *Date*       {{date}}

The receipt is attached for your records.""",
    ),
    'tmpl_account_move_manual': (
        """{{name}}: {{amount_total}}""",
        """{{name}}: {{amount_total}}""","{{name}}: {{amount_total}} {{currency_id.name}}",),
}


class EpgWhatsappTemplate(models.Model):
    """Tag a generic template with the lab event it answers to."""
    _inherit = 'epg.whatsapp.template'

    event = fields.Selection(EVENTS, index=True, help="The business moment this "
                                                      "template is sent for.")
    # The contact's WhatsApp Number, not its phone: the phone is usually the
    # clinic landline. (client, 2026-09-15)
    phone_field = fields.Char(default='partner_id.whatsapp_number')
    # The Settings switch for this moment, on the template itself: "Invoice
    # Ready" is where a manager looks to stop invoices going out, not a
    # settings page three menus away. (client, 2026-09-17)
    auto_send = fields.Boolean(
        'Sends Automatically', compute='_compute_auto_send', inverse='_inverse_auto_send',
        help="Whether this lab moment sends its message on its own. The same switch "
             "as on the WhatsApp settings page.")

    @api.depends('event')
    def _compute_auto_send(self):
        params = self.env['ir.config_parameter'].sudo()
        for template in self:
            template.auto_send = bool(
                template.event and template.event != 'manual'
                and params.get_param('lab_whatsapp.notify_%s' % template.event, 'True')
                in ('True', 'true', '1'))

    def _inverse_auto_send(self):
        params = self.env['ir.config_parameter'].sudo()
        for template in self:
            if template.event and template.event != 'manual':
                params.set_param('lab_whatsapp.notify_%s' % template.event,
                                 'True' if template.auto_send else 'False')

    # ------------------------------------------------------------------ the seed
    @api.model
    def _seeded_values(self):
        """What the module's data file says each seeded template holds, by xml id -
        the file is the one place the lab's messages are written."""
        tree = etree.parse(file_path('lab_whatsapp/data/whatsapp_template_data.xml'))
        seeded = {}
        for record in tree.iter('record'):
            if record.get('model') != 'epg.whatsapp.template':
                continue
            vals = {}
            for node in record.findall('field'):
                name = node.get('name')
                if name not in SEED_FIELDS:
                    continue
                if node.get('ref'):
                    target = self.env.ref(node.get('ref'), raise_if_not_found=False)
                    vals[name] = target.id if target else False
                elif node.get('eval') is not None:
                    vals[name] = safe_eval.safe_eval(node.get('eval'))
                else:
                    kind = self._fields[name].type
                    text = node.text or ''
                    vals[name] = (int(text or 0) if kind == 'integer' else
                                  float(text or 0) if kind == 'float' else text)
            # A field the file does not mention is empty on a fresh install.
            for name in SEED_FIELDS:
                if name not in vals:
                    field = self._fields[name]
                    vals[name] = (False if field.type in ('boolean', 'many2one') else
                                  0 if field.type in ('integer', 'float') else
                                  'links' if name == 'button_style' else False)
            seeded[record.get('id')] = vals
        return seeded

    @api.model
    def _refresh_seeded_templates(self, previous):
        """Bring the seeded templates up to the data file, where the lab has not
        rewritten the message. `previous` maps xml id to the message the seed used to
        carry: a template still saying that gets the whole new seed; one the lab has
        reworded keeps its words, and only gets what is missing - the answers, the
        buttons, the document, the payment link - where those are still empty."""
        for xmlid, vals in self._seeded_values().items():
            template = self.env.ref('lab_whatsapp.%s' % xmlid, raise_if_not_found=False)
            if not template:
                continue
            olds = previous.get(xmlid) or ()
            olds = (olds,) if isinstance(olds, str) else olds
            if (template.body or '').strip() in [old.strip() for old in olds]:
                template.write(vals)
            else:
                template.write({name: value for name, value in vals.items()
                                if name in ADDITIVE_FIELDS and value and not template[name]})
        return True

    _event_model_uniq = models.Constraint(
        'unique(event, model)',
        'A template for this event and model already exists.')

    ENQUIRY_EVENTS = ('more_info', 'ask_impression', 'ask_shade', 'ask_pickup',
                      'ask_approval')

    @api.model
    def _enquiries(self, model='res.partner'):
        """The questions the lab can put to a doctor, in the order they are asked -
        the details first, the go-ahead last, not the alphabet's order."""
        found = self.search([('model', '=', model),
                             ('event', 'in', list(self.ENQUIRY_EVENTS))])
        # By their own sequence where the lab has set one; by the order the
        # questions come up otherwise.
        rank = {event: index for index, event in enumerate(self.ENQUIRY_EVENTS)}
        return found.sorted(key=lambda t: (t.sequence, rank.get(t.event, len(rank))))

    def _find(self, model, event):
        """The active template tagged for exactly this event, or an empty recordset.

        No fallback to the model's "manual" template. Archiving "Payment Reminder" is
        how a manager stops reminders, and a fallback turned that into the reminder
        cron sending the generic invoice text to every overdue doctor instead. The
        generic templates are for a person choosing one in the composer.

        A missing template must not stop the business action that triggered it, so the
        caller treats an empty result as "send nothing" rather than as an error.
        """
        return self.search([('model', '=', model), ('event', '=', event)], limit=1)
