# WhatsApp on Meta — Step-by-step Setup

Everything to do on Meta's side before Odoo can send a WhatsApp message, in the order it has to be done, and what to copy into Odoo at each point.

> Meta renames these screens often. The names here are what they were called in September 2026 — if a menu has moved, look for the purpose described in the step rather than the exact label.

## Contents

1. [Before you start](#before-you-start)
2. [Step 1 — the Business account](#step-1-the-business-account)
3. [Step 2 — the app](#step-2-the-app)
4. [Step 3 — the WhatsApp number](#step-3-the-whatsapp-number)
5. [Step 4 — Phone Number ID and Business Account ID](#step-4-phone-number-id-and-business-account-id)
6. [Step 5 — a permanent access token](#step-5-a-permanent-access-token)
7. [Step 6 — the App Secret](#step-6-the-app-secret)
8. [Step 7 — the webhook](#step-7-the-webhook)
9. [Step 8 — message templates](#step-8-message-templates)
10. [Step 9 — billing](#step-9-billing)
11. [Step 10 — go live](#step-10-go-live)
12. [What goes where in Odoo](#what-goes-where-in-odoo)
13. [When Meta refuses](#when-meta-refuses)

## Before you start

- A **phone number the lab owns** that is **not currently registered on WhatsApp** — not on the normal WhatsApp app, not on WhatsApp Business. If it is, delete that account first and wait a few minutes. A landline works if it can receive a voice call.
- Someone who is an **admin of the lab's Meta Business account** (or can create one).
- A **credit or debit card** for the WhatsApp billing account.
- Odoo reachable over **HTTPS on the lab's own address** — Meta calls back to it.

The whole run takes about an hour, plus template review time.

## Step 1 — the Business account

1. Open **business.facebook.com** and sign in with the Facebook account that should own this.
2. If the lab has no business yet: **Create account**, and enter the lab's legal name, your name and a work email.
3. In **Business settings ▸ Business info**, start **Business verification** if it is not done. Meta asks for a registration document or a utility bill in the business's name.

> Verification is not needed to send your first test message, but it is needed to raise the daily message limit beyond the starting tier and to use some template categories. Start it early — it can take a few days.

## Step 2 — the app

1. Go to **developers.facebook.com ▸ My Apps ▸ Create app**.
2. Choose the **Business** app type, give it a name (for example "Arabian Dental Lab Odoo"), and pick the business you just set up.
3. On the app dashboard, find **WhatsApp** in the product list and press **Set up**.

The app is the thing that holds the credentials; the business owns the WhatsApp account.

## Step 3 — the WhatsApp number

1. In the app, open **WhatsApp ▸ API Setup** (sometimes "Getting started").
2. Meta offers a **test number** to begin with. It only messages up to five numbers you list by hand, so it is useful for a first look but not for the lab.
3. Press **Add phone number** and fill in:
   - **Display name** — what doctors see as the sender. Meta reviews it; a name that is not obviously the business gets rejected.
   - **Category** and a short description.
4. Verify the number by **SMS or voice call**.
5. Set the **two-step verification PIN** when asked and keep it somewhere safe. Meta asks for it again when the number moves.

## Step 4 — Phone Number ID and Business Account ID

On **WhatsApp ▸ API Setup**, with the lab's number selected:

- **Phone Number ID** — the long number under the phone number. It is **not** the phone number itself.
- **WhatsApp Business Account ID** (WABA id) — on the same page.

Copy both. In Odoo they go into **WhatsApp ▸ Configuration ▸ Senders** as **Phone Number ID** and **Business Account ID**.

## Step 5 — a permanent access token

The token shown on the API Setup page expires in 24 hours. Sending stops dead when it does, so make a permanent one:

1. **business.facebook.com ▸ Business settings ▸ Users ▸ System users**.
2. **Add** a system user — name it something like "Odoo", role **Admin**.
3. **Add assets** and give that system user:
   - the **app** from Step 2, with full control;
   - the **WhatsApp account** from Step 3, with full control.
4. Press **Generate new token**, choose the app, and tick these two permissions:
   - `whatsapp_business_messaging`
   - `whatsapp_business_management`
5. Set the expiry to **Never**, generate, and **copy the token now** — Meta shows it once.

In Odoo it goes into the sender's **Access Token**.

> Treat the token like a password: anyone holding it can message the lab's doctors. In Odoo only Settings administrators can read it.

## Step 6 — the App Secret

1. **developers.facebook.com ▸ your app ▸ App settings ▸ Basic**.
2. Next to **App secret**, press **Show** and copy it.

In Odoo it goes into the sender's **App Secret**. Without it the sender refuses every callback from Meta, because an unsigned callback cannot be proven to come from Meta — and a forged inbound message would open the free-form reply window for any number.

## Step 7 — the webhook

Do the Odoo side first: open the sender and copy **Webhook Url** (for example `https://www.arabiandentallab.com/whatsapp/webhook`) and **Verify Token**.

Then in Meta:

1. **Your app ▸ WhatsApp ▸ Configuration ▸ Webhook ▸ Edit**.
2. **Callback URL** — paste the Webhook Url.
3. **Verify token** — paste the Verify Token.
4. Press **Verify and save**. Meta calls the address once; if it fails, the address is not reachable from the internet or the token does not match.
5. Press **Manage** next to Webhook fields and **subscribe to `messages`**.

That one subscription carries both directions: delivery and read receipts for what the lab sends, and anything a doctor replies. Without it a message stays *Sent* for ever and the 24-hour reply window never opens.

## Step 8 — message templates

Any message sent **more than 24 hours after the doctor last wrote** must be an approved template.

1. **business.facebook.com ▸ WhatsApp Manager ▸ Manage message templates ▸ Create template**.
2. Choose the **category**:
   - **Utility** — order confirmations, dispatch notices, invoices, payment receipts. This is what the lab mostly sends.
   - **Marketing** — offers and anything promotional. Priced higher and refused more often.
3. Choose the **language**. It must match **Template Language** on the Odoo sender exactly (`en` by default).
4. Write the body, using `{{1}}`, `{{2}}` … where values go, and give a sample for each. Meta rejects a template whose samples look like placeholders.
5. Submit. Review usually takes minutes, sometimes up to 24 hours.

Then in Odoo, on the matching template (**WhatsApp ▸ Configuration ▸ Templates**):

- **Approved Template Name** — the name exactly as approved.
- **Template Variables** — the field paths filling `{{1}}`, `{{2}}` … in order, comma separated, for example `partner_id.name, name, amount_total`.
- Press **Sync with Meta** to pull the approval state; it shows *Approved*, *In review* or *Rejected* with Meta's reason.

## Step 9 — billing

1. **business.facebook.com ▸ Business settings ▸ WhatsApp accounts ▸ your account ▸ Payment settings** (or WhatsApp Manager ▸ Billing).
2. Add a card and set the billing country and currency.

Meta gives a monthly allowance of free service conversations; beyond that, messages are charged per conversation and sending stops if there is no payment method. Utility conversations started by the lab are charged; a reply the doctor starts is free for 24 hours.

## Step 10 — go live

1. On the app dashboard, switch the app from **Development** to **Live** (the toggle at the top).
2. Check **WhatsApp ▸ API Setup** shows the lab's own number, not the test number.
3. In WhatsApp Manager, check the number's **Display name status** is approved and its **Quality rating** is green.
4. In Odoo, turn **Simulation mode** off on the sender and send one message to your own phone.

New numbers start at a **1,000 business-initiated conversations a day** limit. It rises automatically with good quality and completed business verification.

## What goes where in Odoo

| Meta | Odoo — WhatsApp ▸ Configuration ▸ Senders |
| --- | --- |
| Phone Number ID (API Setup) | **Phone Number ID** |
| WhatsApp Business Account ID | **Business Account ID** |
| System user token, never expiring | **Access Token** |
| App settings ▸ Basic ▸ App secret | **App Secret** |
| Graph API version you target | **Api Version** (`v21.0`) |
| Template language as approved | **Template Language** (`en`) |
| Webhook callback URL | **Webhook Url** (copy from Odoo into Meta) |
| Webhook verify token | **Verify Token** (copy from Odoo into Meta) |
| Approved template name | On each **Template** |

## When Meta refuses

| What you see | What it means |
| --- | --- |
| `(#131030) Recipient phone number not in allowed list` | The app is still in Development, or you are using the test number. Go live, or add the number to the test list. |
| `(#190) Access token has expired` | The 24-hour token was used. Make a system user token (Step 5). |
| `(#132001) Template name does not exist` | The name or the language does not match what was approved. Check both, then Sync with Meta. |
| `(#131047) Re-engagement message` | The 24-hour window has closed and no approved template was used. |
| Webhook "The callback URL couldn't be validated" | The address is not reachable over HTTPS from the internet, or the verify token differs. |
| Callbacks arrive but Odoo ignores them | The App Secret is missing or wrong — Odoo rejects unsigned callbacks. |
| Display name rejected | Meta wants a name that plainly matches the business. Submit a new one in WhatsApp Manager. |
| Quality rating drops to red | Doctors are blocking or reporting the messages. Slow down, and check the reminder settings in Odoo. |
