# -*- coding: utf-8 -*-
"""Reading the consignment number off a courier receipt.

The number on the receipt in the sender's hand is the only handle the lab ever gets on
a parcel, and it was being typed from a photo on somebody's phone — eleven digits, at
a counter, in a hurry. Every downstream thing (the tracking link, the stalled check,
what the doctor is told) inherits the typo.

Two readers, layered, neither one required:

* **The barcode.** Every courier prints the consignment number as a Code 128 / Code 39
  barcode, and the browser can decode that from a photo with no server, no network
  and no cost. The client sends what it decoded; this side says which of the values
  is a consignment number and whose.
* **The printed text.** When the barcode is smudged, folded or missing, a vision model
  reads the receipt itself. That needs an API key the lab sets in Settings, and when
  it is not set the feature quietly does without — a receipt scanned with no key still
  gets its barcode read.

What comes back is a ranked list of *candidates* with a reason for each, not a single
answer: the receipt also carries a booking reference, a phone number, a GST number and
a pin code, and the person holding it is the right one to pick when the shapes tie.
The courier's own number pattern (`lab.courier.awb_pattern`) and, for postal items,
the UPU S10 check digit do most of the ranking. (client, 2026-08-28)
"""
import base64
import io
import json
import logging
import re

from odoo import _, api, models

_logger = logging.getLogger(__name__)

# UPU S10: two letters, nine digits (the ninth a check digit), two-letter country.
S10_RE = re.compile(r'^([A-Z]{2})([0-9]{8})([0-9])([A-Z]{2})$')
S10_WEIGHTS = (8, 6, 4, 2, 3, 5, 9, 7)

# The vision pass. Model and key live in ir.config_parameter; the key is only ever
# read under sudo here and never written to the log.
PARAM_KEY = 'lab_delivery.vision_api_key'
PARAM_MODEL = 'lab_delivery.vision_model'
DEFAULT_MODEL = 'claude-opus-5'
VISION_MAX_PX = 1600          # a receipt is legible at this size; the upload is not
VISION_TIMEOUT = 45.0         # seconds — a wizard button, not a batch job

# A candidate the wizard fills in without being asked. Below this the list is shown
# and the person picks.
AUTO_APPLY_SCORE = 70

# The shape of what the vision model must return. Strict JSON: the answer is parsed,
# never pattern-matched out of prose.
RECEIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "consignment_number": {
            "type": "string",
            "description": "The consignment / AWB / tracking / article number exactly "
                           "as printed — the number the courier's tracking page "
                           "accepts. Empty string if not legible."},
        "courier_name": {"type": "string",
                         "description": "The courier or postal service as printed."},
        "booking_date": {"type": "string",
                         "description": "Booking date as printed, YYYY-MM-DD, or empty."},
        "receiver": {"type": "string", "description": "Receiver / consignee name, or empty."},
        "destination": {"type": "string", "description": "Destination city / pin, or empty."},
        "other_numbers": {
            "type": "array", "items": {"type": "string"},
            "description": "Every other long number printed (booking ref, phone, "
                           "invoice, GST), so a person can choose if the main pick "
                           "is wrong."},
        "legible": {"type": "boolean",
                    "description": "False if the consignment number could not be read "
                                   "with confidence."},
    },
    "required": ["consignment_number", "courier_name", "booking_date", "receiver",
                 "destination", "other_numbers", "legible"],
    "additionalProperties": False,
}

PROMPT = (
    "This is a photo of a courier or postal booking receipt from India. Return the "
    "consignment number — also printed as AWB, waybill, tracking, docket, C/N or "
    "article number — exactly as printed, without spaces. It is the number the "
    "courier's tracking page accepts. It is NOT the booking reference, invoice number, "
    "phone number, GST number, pin code, weight or amount; list those under "
    "other_numbers instead. If the consignment number is not legible, leave it empty "
    "and set legible to false rather than guessing. Read the courier's name and the "
    "booking date if printed.")


def s10_check_digit(serial8):
    """The UPU S10 check digit for an eight-digit serial."""
    total = sum(w * int(d) for w, d in zip(S10_WEIGHTS, serial8))
    check = 11 - (total % 11)
    if check == 10:
        return 0
    if check == 11:
        return 5
    return check


def s10_status(token):
    """None when `token` is not S10-shaped; True/False for the check digit."""
    match = S10_RE.match(token or '')
    if not match:
        return None
    return s10_check_digit(match.group(2)) == int(match.group(3))


def normalize(value):
    """Uppercase, no whitespace: receipts print numbers in groups of four and
    people type them the same way, and a barcode never has either."""
    return re.sub(r'\s+', '', str(value or '')).upper()


class LabCourierReceipt(models.AbstractModel):
    _name = 'lab.courier.receipt'
    _description = 'Courier Receipt Reader'

    # ------------------------------------------------------------ configuration
    @api.model
    def vision_enabled(self):
        return bool(self.env['ir.config_parameter'].sudo().get_param(PARAM_KEY))

    # ------------------------------------------------------------ the ranking
    @api.model
    def classify(self, token, couriers=None):
        """Which couriers' numbers does `token` look like, and is it a valid S10?"""
        token = normalize(token)
        couriers = couriers if couriers is not None else \
            self.env['lab.courier'].search([])
        matches = [c for c in couriers if c.matches_awb(token)]
        return {
            'token': token,
            'courier_ids': [c.id for c in matches],
            'courier_names': [c.name for c in matches],
            's10': s10_status(token),
        }

    @api.model
    def _clash_for(self, token):
        """Another parcel already carrying this number."""
        if not token:
            return ''
        other = self.env['lab.delivery'].sudo().search(
            [('courier_awb', '=ilike', token)], limit=1)
        return other.name if other else ''

    @api.model
    def _rank(self, barcodes, vision, courier_id):
        """Merge what the barcode reader and the vision pass found into one list."""
        couriers = self.env['lab.courier'].search([])
        chosen = couriers.filtered(lambda c: c.id == courier_id) if courier_id else None
        rows = {}

        def add(raw, base, why):
            token = normalize(raw)
            if len(token) < 5 or len(token) > 24:
                return
            row = rows.setdefault(token, {'token': token, 'score': 0, 'why': []})
            row['score'] += base
            if why not in row['why']:
                row['why'].append(why)

        for value in barcodes or []:
            add(value, 60, _("printed as a barcode"))
        if vision:
            if vision.get('consignment_number'):
                add(vision['consignment_number'], 45, _("read from the receipt"))
            for value in vision.get('other_numbers') or []:
                add(value, 10, _("another number on the receipt"))

        out = []
        for token, row in rows.items():
            info = self.classify(token, couriers)
            names = info['courier_names']
            if chosen and chosen.id in info['courier_ids']:
                row['score'] += 20
                row['why'].append(_("looks like a %s number", chosen.name))
            elif chosen and chosen.matches_awb(token) is False:
                row['score'] -= 15
                row['why'].append(_("does not look like a %s number", chosen.name))
            elif names:
                row['score'] += 10
                row['why'].append(_("looks like a %s number", ', '.join(names)))
            if info['s10'] is True:
                row['score'] += 15
                row['why'].append(_("postal check digit is valid"))
            elif info['s10'] is False:
                row['score'] -= 40
                row['why'].append(_("postal check digit FAILS — a digit was misread"))
            # A bare phone number or pin code is never a consignment number, however
            # confidently it was read.
            if re.fullmatch(r'[6-9][0-9]{9}', token):
                row['score'] -= 35
                row['why'].append(_("looks like a mobile number"))
            if re.fullmatch(r'[1-9][0-9]{5}', token):
                row['score'] -= 35
                row['why'].append(_("looks like a pin code"))
            row['score'] = max(0, min(100, row['score']))
            row['courier_ids'] = info['courier_ids']
            row['courier_names'] = names
            row['clash'] = self._clash_for(token)
            row['reason'] = '; '.join(row.pop('why'))
            out.append(row)
        out.sort(key=lambda r: (-r['score'], r['token']))
        return out

    @api.model
    def _suggest_courier(self, candidates, vision):
        """A courier to fill in when the person did not choose one first."""
        Courier = self.env['lab.courier']
        top = candidates[0] if candidates else None
        if top and len(top['courier_ids']) == 1:
            courier = Courier.browse(top['courier_ids'][0])
            return {'id': courier.id, 'display_name': courier.display_name,
                    'why': _("from the number's shape")}
        printed = normalize(vision.get('courier_name')) if vision else ''
        if printed:
            for courier in Courier.search([]):
                mine = normalize(courier.name)
                code = normalize(courier.code)
                if (mine and (mine in printed or printed in mine)) or \
                        (code and code == printed):
                    return {'id': courier.id, 'display_name': courier.display_name,
                            'why': _("printed on the receipt")}
        return None

    # ------------------------------------------------------------ the vision pass
    @api.model
    def _vision_prepare(self, image_b64):
        """Shrink and re-encode the photo: a phone's 12-megapixel JPEG is 4 MB the
        model does not need, and the upload from a courier counter is on 4G."""
        from PIL import Image, ImageOps
        image = Image.open(io.BytesIO(base64.b64decode(image_b64)))
        image = ImageOps.exif_transpose(image).convert('RGB')
        image.thumbnail((VISION_MAX_PX, VISION_MAX_PX))
        out = io.BytesIO()
        image.save(out, format='JPEG', quality=85, optimize=True)
        return base64.standard_b64encode(out.getvalue()).decode('ascii')

    @api.model
    def _vision_client(self, api_key):
        """Split out so a test can hand in a fake without any network."""
        import anthropic
        return anthropic.Anthropic(api_key=api_key, timeout=VISION_TIMEOUT,
                                   max_retries=1)

    @api.model
    def _vision_read(self, image_b64):
        """Ask the model what is printed. Returns the parsed answer, or
        {'error': <message>} — never raises: a failed reading must not stop the
        person typing the number by hand."""
        params = self.env['ir.config_parameter'].sudo()
        api_key = params.get_param(PARAM_KEY)
        if not api_key:
            return None
        model = params.get_param(PARAM_MODEL) or DEFAULT_MODEL
        try:
            import anthropic
        except ImportError:
            return {'error': _("The receipt reader is not installed on this server "
                               "(python package 'anthropic').")}
        try:
            data = self._vision_prepare(image_b64)
        except Exception as exc:                                    # noqa: BLE001
            return {'error': _("That file is not an image the server can read: %s", exc)}

        try:
            client = self._vision_client(api_key)
            # A refused request is re-run server-side on a fallback model rather than
            # coming back empty; the receipt still gets read.
            response = client.beta.messages.create(
                model=model,
                max_tokens=1024,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": RECEIPT_SCHEMA},
                },
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image",
                         "source": {"type": "base64", "media_type": "image/jpeg",
                                    "data": data}},
                        {"type": "text", "text": PROMPT},
                    ],
                }],
            )
        except anthropic.AuthenticationError:
            return {'error': _("The receipt-reading API key was refused. Check it "
                               "under Settings.")}
        except anthropic.RateLimitError:
            return {'error': _("The receipt reader is busy — try again in a moment, "
                               "or type the number.")}
        except anthropic.APIStatusError as exc:
            _logger.warning("courier receipt: API error %s", exc.status_code)
            return {'error': _("The receipt reader returned an error (%s).",
                               exc.status_code)}
        except anthropic.APIConnectionError:
            return {'error': _("Could not reach the receipt reader — no network?")}

        if response.stop_reason == 'refusal':
            return {'error': _("The receipt reader declined to read this image.")}
        if response.stop_reason == 'max_tokens':
            return {'error': _("The receipt reader's answer was cut short.")}
        text = next((b.text for b in response.content if b.type == 'text'), '')
        try:
            return json.loads(text)
        except ValueError:
            _logger.warning("courier receipt: unparseable answer %r", text[:200])
            return {'error': _("The receipt reader's answer could not be understood.")}

    # ------------------------------------------------------------ the entry point
    @api.model
    def read_receipt(self, image_b64=None, barcodes=None, courier_id=None):
        """Everything the scan widget needs, in one call.

        `barcodes` is what the browser already decoded from the photo; `image_b64`
        is the photo itself, for the vision pass; either may be empty (a live camera
        scan sends a barcode and no image).
        """
        vision = None
        vision_error = ''
        engines = ['barcode']
        if image_b64 and self.vision_enabled():
            engines.append('vision')
            vision = self._vision_read(image_b64)
            if vision and vision.get('error'):
                vision_error, vision = vision['error'], None

        candidates = self._rank(barcodes or [], vision, courier_id)
        top = candidates[0] if candidates else None
        return {
            'candidates': candidates,
            'auto': bool(top and top['score'] >= AUTO_APPLY_SCORE and not top['clash']),
            'courier': self._suggest_courier(candidates, vision) if not courier_id else None,
            'booking_date': (vision or {}).get('booking_date') or '',
            'receiver': (vision or {}).get('receiver') or '',
            'engines': engines,
            'vision_enabled': 'vision' in engines,
            'vision_error': vision_error,
            'legible': (vision or {}).get('legible', True),
        }
