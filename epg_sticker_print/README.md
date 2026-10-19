# Sticker & Label Printing (`epg_sticker_print`)

Replaces `print_sticker`. Destination stickers for sale orders, built by one engine
(`epg.sticker.engine`) and printed on configurable label formats.

| Feature | Where |
|---|---|
| One sticker per **customer** (orders combined), per **order** (work lines, U/L, colour, patient, priority) or per **sales route** (route address/phone) | wizard → *One sticker per* |
| **Live preview** of the first sticker at label size, updating as you tick options | wizard |
| **Barcode (Code 128) / QR / none** of the order references, rendered inline (no HTTP fetch) | wizard |
| **Label formats**: 100 × 60 roll (default), 65 × 35 and 50 × 30 rolls, A4 sheets 3 × 8 (65 × 35) and 2 × 5 — add your own; a sheet format carries the sheet size and where the grid sits on it | Sales → Configuration → Sticker Formats |
| **The sheet in the tray** (sheet formats): the wizard draws the sheet, you mark the labels already peeled off — click, drag over a run, *Invert* when few are left — and the stickers are numbered onto the free labels in reading order, then fresh sheets. What a print takes is remembered per format (`epg.sticker.sheet`), so the next run starts from the sheet as it is | wizard |
| Copies, sender block, phone / route / patient / lines toggles | wizard |
| *Preview all* (HTML in a new tab) before feeding labels | wizard |
| Entry points: **Sticker** button on the sale order and on the delivery transfer, Action menu on order / transfer lists (batch) | forms & lists |

Route address & phone live on the sales team (`crm.team.address / phone`).

Tests: `-u epg_sticker_print --test-enable --test-tags /epg_sticker_print`.
