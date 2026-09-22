# Product Label Builder Pro (`epg_product_label`)

Design label templates through the Odoo UI - no report XML, no code - and print them
as PDF or send them to a Zebra printer as ZPL. Odoo 19.

---

## What it does

### Designer

Open **Labels → Configuration → Label Templates**, set the label size, and drop
elements onto the canvas. Drag to move, grab a handle to resize, or type exact
coordinates in the property strip. Arrow keys nudge by 0.5 mm, Shift+arrow by 5 mm.
Snap-to-grid and the grid overlay are both toggleable; zoom is independent of the
printed size.

Elements that hang off the edge of the label are outlined in red before you print,
rather than being silently clipped afterwards.

**Element types**

| Type | What it prints |
|---|---|
| Text | Static text, with `{{ field.path }}` placeholders resolved against the record |
| Field | Any field, reached by dotted path (`categ_id.name`, `uom_id.name`, …) |
| Barcode | EAN-13/8, UPC-A/E, Code 128, Code 39, ITF, Codabar, or automatic |
| QR Code | From the barcode, a field, a fixed value, or a text template |
| vCard QR Code | A scannable contact card built from the partner |
| Image | Product image, product template image, contact image, company logo, an upload, or any binary field |
| Price | Regular pricelist, promotional pricelist, sales price, cost, or another field |
| Promotional Price | The promotional pricelist chosen in the print wizard |
| Price Difference | Amount saved, percentage saved, or both |
| Price per Unit | Price for N units, e.g. "€ 4.20 / 100 g" |
| Pricelist Rule | Validity period, start/end date, minimum quantity, discount, or rule name |
| Product Attributes | Variant attribute values, optionally filtered and named |
| Contact Address | A formatted address; city, state and postcode share a line |
| Date | The print date, or any date field, in your own strftime pattern |
| Box / Line / Ellipse | Frames, rules and dividers |
| Custom HTML | Raw HTML for anything the element types do not cover |

**Per element**: position, size, rotation (0/90/180/270), layer, opacity, font family
and size, bold/italic/underline/strikethrough, horizontal and vertical alignment,
line height, letter spacing, case, wrapping, colour, background, border, corner
radius and padding.

**Formatting**: prefix, suffix, fallback value when empty, character limit with an
ellipsis, and number/date/case formats.

**Conditional printing**: give an element a Python condition such as
`object.barcode`, and it prints only when that is true for the record. A condition
that fails at print time hides the element and logs a warning; it never breaks the
run.

### Output

**PDF** - on label sheets (a grid on ordinary paper) or on roll/continuous stock
(one label per page, sized to the label). Each template maintains its own
`report.paperformat`, so a 100 × 100 mm roll label really is printed on 100 × 100 mm
paper. All offsets live in CSS and the paper format carries zero margins, so the two
never double up.

**ZPL II** - for Zebra printers, with configurable print density (6/8/12/24 dpmm),
whole-label rotation, character encoding, media darkness and print speed, set
globally and overridable per template. Raw ZPL prefix and suffix hooks are available
for anything the designer does not model. Images are converted to 1-bit `^GFA`
bitmaps; text is hex-escaped through `^FH`, so `^`, `~`, `\` and accented characters
survive intact.

Send the job straight to an IoT Box or print server over HTTP, download it as a
`.zpl` file, or review the generated ZPL in the wizard first.

### Print wizard

Reachable from a product list selection, a product form, a contact, a lot/serial
number, or **Labels → Print Labels**.

* Number of copies, applied to every record or set per record.
* **Skip** - leave N label positions empty at the start of the first sheet, so a
  part-used sheet can be reloaded into the printer.
* Regular and promotional pricelist selection, plus the quantity to price at, so
  quantity-based rules resolve the way they will at the till.
* Cutting guides on the PDF.
* Live preview of the first label, before anything is generated.

### Templates

Nine ready-made templates ship with the module - seven for products, two for
addresses:

| Template | Size | Layout |
|---|---|---|
| Product Label 57 × 38 mm | 57 × 38 mm | Roll |
| Product Label 52 × 30 mm | 52 × 30 mm | Roll |
| Product Label 99 × 38 mm | 99 × 38 mm | Roll |
| Product Label 50 × 89 mm | 50 × 89 mm | Roll |
| Product Label 50 × 25 mm | 50 × 25 mm | Roll |
| Product Label 101 × 50 mm | 101 × 50 mm | 10 per Letter sheet (2 × 5) |
| Product Label 100 × 100 mm | 100 × 100 mm | Roll |
| Address Label 101 × 34 mm | 101 × 33.9 mm | 14 per Letter sheet (2 × 7) |
| Address Label 66 × 25 mm | 66.7 × 25.4 mm | 30 per Letter sheet (3 × 10) |

They are marked *Predefined* and loaded `noupdate`, so a module update never
overwrites your edits. Duplicate one to start from a known-good layout.

**Import / export** - **Export XML** on a template writes a portable file;
**Labels → Configuration → Import Templates** reads it back, either alongside the
existing templates or overwriting the ones with the same reference. Use it to move
designs between databases.

### Access

Two groups, under one *Labels* privilege on the user form:

* **Print Labels** - print with the templates they are allowed to use. Implied by
  *Inventory / User*.
* **Design Labels** - create and edit templates. Implied by *Inventory / Administrator*.

A template with no users listed is available to everyone; listing users restricts
it. Each user can also set a **Default Label Template** in their own preferences.

### Settings

**Inventory → Configuration → Settings → Labels**, or the *Labels* app settings:

* **Replace Standard Label Wizard** - route Odoo's own *Print Labels* action on
  products to the builder. Off by default, so both stay available.
* Default regular and promotional pricelists for the print wizard.
* Preview product and contact for the designer.
* Default ZPL density, rotation, encoding, darkness and speed.
* ZPL printer endpoint for direct printing.

---

## Install

```bash
# the module lives on your addons path, then:
odoo-bin -d <database> -i epg_product_label
```

Depends on `base`, `mail`, `product`, `stock` and `web`. Pillow is required (Odoo
already requires it) and is used for the ZPL bitmap conversion and for transparent
and inverted barcodes.

**PDF output needs `wkhtmltopdf`** on the server, as every Odoo PDF report does. ZPL
output does not. If PDF printing returns *"Unable to find Wkhtmltopdf on this
system"*, install the patched Qt build Odoo recommends (0.12.6).

---

## Notes for developers

| Model | Purpose |
|---|---|
| `epg.label.template` | Label and page geometry, styling defaults, ZPL settings, both render entry points, XML import/export |
| `epg.label.element` | One positioned element; resolves its own value and renders itself to HTML and to ZPL |
| `epg.label.print` | Print wizard; owns copies, skipping and pricelist choices |
| `epg.label.import` | XML import wizard |

Both render engines consume the same list of slots, so a PDF and a ZPL job from one
wizard contain the same labels in the same order.

**Adding an element type**: add it to `ELEMENT_TYPES`, write a `_value_<type>`
method for its text (or extend `_render_html` for a non-text type), and handle it in
`_render_zpl`. Both renderers fall through to the text path by default.

**Extension points**

* `epg.label.template._build_render_context()` - add data your elements need.
* `epg.label.element._resolve_path()` - how a dotted path is walked.
* `ir.actions.report.get_paperformat()` - honours `epg_label_paperformat_id` from
  the context.

### Tests

```bash
odoo-bin -d <database> -u epg_product_label --test-enable --test-tags=/epg_product_label
```

86 Python tests cover the grid maths, the paper-format lifecycle, value resolution
and formatting, pricing against pricelists and taxes, barcode payloads and
post-processing, the vCard payload, conditions, ZPL escaping and command emission,
copies and skipping, and the XML round trip. A browser tour additionally drags an
element on the real canvas and checks the new coordinates reach the database; it is
skipped automatically when `websocket-client` or Chrome is unavailable.
