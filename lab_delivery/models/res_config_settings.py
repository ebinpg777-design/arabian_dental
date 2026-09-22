# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    lab_delivery_grace_minutes = fields.Integer(
        'Delivery grace (minutes)', default=120,
        config_parameter='lab_delivery.grace_minutes',
        help="How long past the promised time before a delivery counts as delayed.")
    lab_emergency_grace_minutes = fields.Integer(
        'Emergency grace (minutes)', default=30,
        config_parameter='lab_delivery.emergency_grace_minutes')
    lab_escalation_after_hours = fields.Integer(
        'Escalate after (hours)', default=4,
        config_parameter='lab_delivery.escalation_after_hours',
        help="A still-late delivery re-escalates each time this many hours pass — "
             "managers are included from the second escalation.")
    lab_courier_stale_days = fields.Integer(
        'Chase a consignment after (days)', default=3,
        config_parameter='lab_delivery.courier_stale_days',
        help="A courier parcel with no tracking movement for this many days is "
             "flagged and the managers are told. Distinct from the delay engine: "
             "this asks whether anything has happened at all, not whether it is late.")

    # Reading the receipt itself, not just its barcode. Optional: without a key the
    # scan widget still decodes barcodes in the browser at no cost. The key is only
    # readable by administrators and is never written to the log.
    # (client, 2026-08-28)
    lab_vision_api_key = fields.Char(
        'Receipt reader API key',
        config_parameter='lab_delivery.vision_api_key',
        help="An Anthropic API key. With it, a photographed courier receipt is read "
             "for its consignment number, courier and booking date even when the "
             "barcode is smudged or missing. Leave empty to read barcodes only.")
    lab_vision_model = fields.Char(
        'Receipt reader model', default='claude-opus-5',
        config_parameter='lab_delivery.vision_model',
        help="Which model reads receipts. Leave as is unless told otherwise.")
    lab_vision_enabled = fields.Boolean(compute='_compute_lab_vision_enabled')

    @api.depends('lab_vision_api_key')
    def _compute_lab_vision_enabled(self):
        for settings in self:
            settings.lab_vision_enabled = bool(settings.lab_vision_api_key)
