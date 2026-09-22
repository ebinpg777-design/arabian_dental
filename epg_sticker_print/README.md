# Sticker & Label Printing (`epg_sticker_print`)

Replaces `print_sticker`. Destination stickers for sale orders, built by one engine
(`epg.sticker.engine`) and printed on configurable label formats.

| Feature | Where |
|---|---|
| One sticker per **customer** (orders combined), per **order** (work lines, U/L, colour, patient, priority) or per **sales route** (route address/phone) | wizard → *One sticker per* |
| **Live preview** of the first sticker at label size, updating as you tick options | wizard |
| **Barcode (Code 128) / QR / none** of the order references, rendered inline (no HTTP fetch) | wizard |
| **Label formats**: 100 × 60 roll (default), 50 × 30 roll, A4 sheet 2 × 5 — add your own | Sales → Configuration → Sticker Formats |
| Copies, sender block, phone / route / patient / lines toggles | wizard |
| *Preview all* (HTML in a new tab) before feeding labels | wizard |
| Entry points: **Sticker** button on the sale order and on the delivery transfer, Action menu on order / transfer lists (batch) | forms & lists |

Route address & phone live on the sales team (`crm.team.address / phone`).

Tests: `-u epg_sticker_print --test-enable --test-tags /epg_sticker_print`.
