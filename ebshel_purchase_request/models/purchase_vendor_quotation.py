# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1. See LICENSE file for full copyright and licensing details.
from markupsafe import Markup
from odoo import api, fields, models, _
from odoo.exceptions import UserError

class PurchaseVendorQuotation(models.Model):
    """One record per vendor responding to an RFQ. Captures all pricing,
    terms, and delivery info needed for side-by-side comparison."""
    _name = 'ebshel.purchase.vendor.quotation'
    _description = 'Vendor Quotation'
    _inherit = ['mail.thread']
    _order = 'rfq_id, amount_total'
    _rec_name = 'name'

    # ── Relations ─────────────────────────────────────────────────────────────
    rfq_id = fields.Many2one(
        'ebshel.purchase.rfq', string='RFQ',
        required=True, ondelete='cascade', index=True,
    )
    vendor_id = fields.Many2one(
        'res.partner', string='Vendor',
        tracking=True,
        domain=[('is_company', '=', True)],
    )
    # New-vendor support: allow adding a prospective vendor before they exist in the system
    is_new_vendor = fields.Boolean(
        'New / Prospective Vendor', default=False,
        help='Enable this when the vendor is not yet registered in the system. '
             'You must create the vendor record before or at the time of award.',
    )
    new_vendor_name = fields.Char(
        'Prospective Vendor Name',
        help='Working name for this vendor until their record is created.',
    )
    new_vendor_email = fields.Char(
        'Prospective Vendor Email',
        help='Email used to send the RFQ and carried over when the vendor '
             'is created on award.',
    )
    new_vendor_phone = fields.Char('Prospective Vendor Phone')
    # Single resolved email used for sending the RFQ (registered or prospective)
    email = fields.Char('Email', compute='_compute_email')
    vendor_registered = fields.Boolean(
        'Vendor Created in System', compute='_compute_vendor_registered', store=True,
    )
    company_id = fields.Many2one(
        related='rfq_id.company_id', store=True, readonly=True,
    )
    currency_id = fields.Many2one(
        related='rfq_id.currency_id', store=True, readonly=True,
    )
    rfq_state = fields.Selection(
        related='rfq_id.state', string='RFQ Status', readonly=True,
    )
    name = fields.Char(
        'Name', compute='_compute_name', store=True,
    )

    # ── State ─────────────────────────────────────────────────────────────────
    state = fields.Selection([
        ('draft', 'Pending'),
        ('sent', 'Sent'),
        ('received', 'Quotation Received'),
        ('selected', 'Selected'),
        ('rejected', 'Not Selected'),
    ], string='Status', default='draft', tracking=True)
    is_winner = fields.Boolean('Winner', default=False, copy=False, tracking=True)
    winner_justification = fields.Text('Selection Justification', copy=False)

    # ── Bargaining / price negotiation ────────────────────────────────────────
    bargain_round = fields.Integer(
        'Bargain Rounds', default=0, copy=False, readonly=True,
        help='How many times a revised-price request has been sent to this vendor.')
    bargain_last_date = fields.Datetime('Last Bargained', copy=False, readonly=True)

    # ── Vendor Terms ──────────────────────────────────────────────────────────
    vendor_reference = fields.Char('Vendor Quotation Ref.')
    date_quotation = fields.Date('Quote Date')
    validity_date = fields.Date('Valid Until')
    payment_term_id = fields.Many2one('account.payment.term', string='Payment Terms')
    delivery_days = fields.Integer(
        'Delivery Days',
        help='Expected lead time in days from order confirmation.',
    )
    incoterm_id = fields.Many2one('account.incoterms', string='Incoterm')
    vendor_notes = fields.Text('Vendor Remarks')

    # ── Terms & Conditions (RFQ terms + vendor's proposed terms) ──────────────
    terms_template_id = fields.Many2one(
        'ebshel.rfq.terms', string='T&C Template',
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]",
        help='Apply a saved Terms & Conditions template.')
    terms_conditions = fields.Html(
        'Terms & Conditions',
        help="Defaults to the RFQ's terms; edit to capture the vendor's "
             "proposed / agreed terms for this quotation.")

    # ── Lines ─────────────────────────────────────────────────────────────────
    line_ids = fields.One2many(
        'ebshel.purchase.vendor.quotation.line', 'quotation_id',
        string='Quoted Lines',
    )

    # ── Totals ────────────────────────────────────────────────────────────────
    amount_untaxed = fields.Monetary(
        'Untaxed Amount', compute='_compute_amounts', store=True,
        currency_field='currency_id',
    )
    amount_tax = fields.Monetary(
        'Taxes', compute='_compute_amounts', store=True,
        currency_field='currency_id',
    )
    amount_total = fields.Monetary(
        'Total', compute='_compute_amounts', store=True,
        currency_field='currency_id',
    )

    # ── Scoring ───────────────────────────────────────────────────────────────
    score = fields.Float(
        'Score', compute='_compute_score', store=True,
        help='Composite score based on price, delivery time, and payment terms. Higher is better.',
    )
    rank = fields.Integer('Rank', compute='_compute_rank')

    # ── Linked PO ─────────────────────────────────────────────────────────────
    purchase_order_id = fields.Many2one(
        'purchase.order', string='Purchase Order',
        copy=False, readonly=True, index='btree_not_null',
    )

    @api.depends('vendor_id.email', 'new_vendor_email')
    def _compute_email(self):
        for q in self:
            q.email = q.vendor_id.email if q.vendor_id else (q.new_vendor_email or False)

    @api.depends('vendor_id', 'is_new_vendor')
    def _compute_vendor_registered(self):
        for q in self:
            q.vendor_registered = bool(q.vendor_id)

    @api.constrains('vendor_id', 'is_new_vendor', 'new_vendor_name')
    def _check_vendor_identity(self):
        for q in self:
            if not q.is_new_vendor and not q.vendor_id:
                raise UserError(_(
                    'Please select a vendor, or enable "New / Prospective Vendor" '
                    'and enter the vendor\'s name.'
                ))
            if q.is_new_vendor and not q.new_vendor_name and not q.vendor_id:
                raise UserError(_('Please enter the prospective vendor\'s name.'))

    @api.depends('line_ids.price_subtotal', 'line_ids.price_tax')
    def _compute_amounts(self):
        for q in self:
            q.amount_untaxed = sum(q.line_ids.mapped('price_subtotal'))
            q.amount_tax = sum(q.line_ids.mapped('price_tax'))
            q.amount_total = q.amount_untaxed + q.amount_tax

    @api.depends('amount_total', 'delivery_days',
                 'rfq_id.vendor_quotation_ids.amount_total')
    def _compute_score(self):
        """Composite 0-100 score, higher is better:
          • Price (0-60) — relative to the cheapest quote on the same RFQ, so the
            lowest bidder gets the full price weight and pricier bids scale down.
          • Delivery (0-40) — faster lead time scores higher.
        Scoring relative to siblings (not an absolute 1/total) keeps price
        meaningful regardless of the order's magnitude."""
        # Default for every record first — a brand-new (NewId) quotation is not
        # yet in its RFQ's vendor_quotation_ids and would otherwise be left
        # unassigned ("Compute method failed to assign score").
        self.score = 0.0
        # Group the records being computed by their RFQ once, so each RFQ's
        # sibling set is scanned a single time instead of re-scanning the whole
        # recordset per RFQ (was O(records × RFQs); now O(records)).
        by_rfq = {}
        for q in self:
            by_rfq.setdefault(q.rfq_id, []).append(q)
        for rfq, members in by_rfq.items():
            totals = [t for t in rfq.vendor_quotation_ids.mapped('amount_total') if t > 0]
            cheapest = min(totals) if totals else 0.0
            for q in members:
                if not q.amount_total:
                    continue
                price_score = (cheapest / q.amount_total) * 60.0 if cheapest else 0.0
                delivery_score = max(0, (30 - (q.delivery_days or 30))) / 30 * 40.0
                q.score = round(price_score + delivery_score, 4)

    @api.depends('score', 'rfq_id.vendor_quotation_ids.score')
    def _compute_rank(self):
        """Rank within the same RFQ (1 = best). Ranks across ALL the RFQ's
        quotations so the value is correct even when a single record is read."""
        # Assign a default to every record first — a brand-new (NewId) quotation
        # is not yet part of its RFQ's vendor_quotation_ids, so it would otherwise
        # be left unassigned and raise "Compute method failed to assign rank".
        self.rank = 0
        # Group by RFQ once instead of re-scanning the whole recordset per RFQ.
        by_rfq = {}
        for q in self:
            by_rfq.setdefault(q.rfq_id, []).append(q)
        for rfq, members in by_rfq.items():
            ordered = rfq.vendor_quotation_ids.sorted(key=lambda x: x.score, reverse=True)
            ranks = {q.id: idx for idx, q in enumerate(ordered, 1)}
            for q in members:
                q.rank = ranks.get(q.id, 0)

    @api.constrains('is_winner')
    def _check_single_winner(self):
        winners = self.filtered('is_winner')
        if not winners:
            return
        # One grouped query for every affected RFQ instead of a search per record.
        groups = self.env['ebshel.purchase.vendor.quotation']._read_group(
            [('rfq_id', 'in', winners.rfq_id.ids), ('is_winner', '=', True)],
            ['rfq_id'], ['__count'])
        if any(count > 1 for __, count in groups):
            raise UserError(_('Only one vendor can be selected as winner per RFQ.'))

    @api.depends('vendor_id.name', 'new_vendor_name', 'rfq_id.name')
    def _compute_name(self):
        for q in self:
            vendor_name = q.vendor_id.name if q.vendor_id else (q.new_vendor_name or _('New Vendor'))
            q.name = f"{vendor_name} – {q.rfq_id.name}" if q.rfq_id else vendor_name

    # ── ORM ───────────────────────────────────────────────────────────────────
    @api.model_create_multi
    def create(self, vals_list):
        quotations = super().create(vals_list)
        # Pre-fill every new quotation with all the RFQ's lines so the vendor
        # quotes against the full request out of the box, and inherit the RFQ
        # terms & conditions as the starting point.
        for q in quotations:
            if q.rfq_id and not q.line_ids:
                q._populate_lines_from_rfq()
            if q.rfq_id and not q.terms_conditions:
                q.terms_conditions = q.rfq_id.terms_conditions
                if not q.terms_template_id:
                    q.terms_template_id = q.rfq_id.terms_template_id
        return quotations

    def unlink(self):
        if any(q.state != 'draft' for q in self):
            raise UserError(_('Only pending (draft) vendor quotations can be deleted.'))
        return super().unlink()

    @api.onchange('rfq_id')
    def _onchange_rfq_id_load_lines(self):
        """Load every RFQ line as a quoted line as soon as the RFQ is set —
        so the lines appear immediately in the form (before saving). Also
        seeds the terms & conditions from the RFQ."""
        if self.rfq_id and not self.line_ids:
            self.line_ids = [(0, 0, {
                'rfq_line_id': rl.id,
                'product_id': rl.product_id.id,
                'name': rl.name,
                'qty': rl.qty,
                'uom_id': rl.uom_id.id,
            }) for rl in self.rfq_id.rfq_line_ids]
        if self.rfq_id and not self.terms_conditions:
            self.terms_conditions = self.rfq_id.terms_conditions
            if not self.terms_template_id:
                self.terms_template_id = self.rfq_id.terms_template_id

    @api.onchange('terms_template_id')
    def _onchange_terms_template_id(self):
        if self.terms_template_id:
            self.terms_conditions = self.terms_template_id.terms

    def action_mark_received(self):
        for q in self:
            q.write({'state': 'received'})
        
    def action_mark_unreceive(self):
        for q in self:
            q.write({'state':'sent'})

    def action_open_create_vendor_wizard(self):
        """Open the vendor creation wizard pre-filled with the prospective vendor name."""
        self.ensure_one()
        return {
            'name': _('Create & Register Vendor'),
            'type': 'ir.actions.act_window',
            'res_model': 'ebshel.pr.create.vendor.wizard',
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'new',
            'context': {
                'default_quotation_id': self.id,
                'default_name': self.new_vendor_name or '',
                'default_email': self.new_vendor_email or '',
                'default_phone': self.new_vendor_phone or '',
                'default_payment_term_id': self.payment_term_id.id if self.payment_term_id else False,
                'default_return_to_quotation': True,
            },
        }

    def action_select_winner(self):
        """Mark this quotation as winner; guard against unregistered new vendors."""
        self.ensure_one()
        # If this is a new vendor that hasn't been registered yet, force registration first
        if self.is_new_vendor and not self.vendor_id:
            return {
                'name': _('Create Vendor Before Awarding'),
                'type': 'ir.actions.act_window',
                'res_model': 'ebshel.pr.create.vendor.wizard',
                'view_mode': 'form',
                'views': [[False, 'form']],
                'target': 'new',
                'context': {
                    'default_quotation_id': self.id,
                    'default_name': self.new_vendor_name or '',
                    'default_email': self.new_vendor_email or '',
                    'default_phone': self.new_vendor_phone or '',
                    'default_payment_term_id': self.payment_term_id.id if self.payment_term_id else False,
                    'default_return_to_quotation': True,
                    'default_proceed_to_award': True,
                    'default_justification': self.winner_justification or '',
                },
            }
        siblings = self.rfq_id.vendor_quotation_ids
        siblings.filtered(lambda q: q.id != self.id).write({
            'is_winner': False,
            'state': 'rejected',
        })
        self.write({'is_winner': True, 'state': 'selected'})
        self.rfq_id.write({'state': 'awarded'})
        vendor_label = self.vendor_id.name if self.vendor_id else self.new_vendor_name
        self.message_post(
            body=Markup(_('<b>%s</b> selected as the winning vendor.')) % (vendor_label,),
        )

    def _ensure_vendor_registered(self):
        """Guarantee this quotation points at a real res.partner.

        For a prospective ("new") vendor that has not been registered yet, create
        the partner on the fly from the captured name/email/phone so the vendor can
        participate in split allocation and PO generation without a manual wizard
        step. Returns the linked res.partner.
        """
        self.ensure_one()
        if self.vendor_id:
            return self.vendor_id
        if not self.is_new_vendor:
            raise UserError(_('This quotation has no vendor set.'))
        name = (self.new_vendor_name or '').strip()
        if not name:
            raise UserError(_(
                'Cannot register a new vendor without a name. Open the quotation '
                'and fill in the "Prospective Vendor Name".'))
        vendor = self.env['res.partner'].create({
            'name': name,
            'is_company': True,
            'supplier_rank': 1,
            'email': self.new_vendor_email or False,
            'phone': self.new_vendor_phone or False,
            'property_payment_term_id': self.payment_term_id.id if self.payment_term_id else False,
        })
        self.write({
            'vendor_id': vendor.id,
            'is_new_vendor': True,        # keep history flag
            'vendor_registered': True,
        })
        self.message_post(
            body=Markup(_('Vendor <a href="#" data-oe-model="res.partner" data-oe-id="%d">%s</a> '
                          'auto-registered for allocation.')) % (vendor.id, vendor.name),
            subtype_xmlid='mail.mt_note',
        )
        return vendor

    def _send_rfq_email(self):
        """Email the RFQ request to a single vendor and mark it sent.
        Returns True if an email actually went out."""
        self.ensure_one()
        template = self.env.ref(
            'ebshel_purchase_request.mail_template_rfq_vendor',
            raise_if_not_found=False,
        )
        recipient = self.email  # registered vendor email or prospective email
        sent = bool(template and recipient)
        if sent:
            template.send_mail(self.id, force_send=True)
        if self.state in ('draft', 'sent'):
            self.write({'state': 'sent'})
        self.message_post(
            body=(_('RFQ request emailed to %s.', recipient) if sent
                  else _('Marked as sent — no email on file, share the RFQ manually.')),
            subtype_xmlid='mail.mt_note',
        )
        return sent

    def action_send_quotation(self):
        """Send the RFQ request to one or many vendors (individual & bulk).

        Returns nothing on purpose: when this button sits in the RFQ form's
        embedded ``vendor_quotation_ids`` list, returning a client notification
        would suppress the form reload and leave the row's status badge stuck on
        "Pending" even though the record was flagged "Sent". Returning ``None``
        lets the web client reload the view so the new state is reflected (the
        per-vendor outcome is still logged to each quotation's chatter)."""
        sendable = self.filtered(lambda q: q.state in ('draft', 'sent'))
        if not sendable:
            raise UserError(_('Select quotations that are still Pending or Sent.'))
        for q in sendable:
            q._send_rfq_email()
        for rfq in sendable.mapped('rfq_id'):
            if rfq.state == 'draft':
                rfq.write({'state': 'sent'})

    def _populate_lines_from_rfq(self):
        """(Re)create quotation lines for any RFQ line not already quoted."""
        self.ensure_one()
        existing_rfq_line_ids = self.line_ids.mapped('rfq_line_id').ids
        new_lines = []
        for rfq_line in self.rfq_id.rfq_line_ids:
            if rfq_line.id not in existing_rfq_line_ids:
                new_lines.append({
                    'quotation_id': self.id,
                    'rfq_line_id': rfq_line.id,
                    'product_id': rfq_line.product_id.id,
                    'name': rfq_line.name,
                    'qty': rfq_line.qty,
                    'uom_id': rfq_line.uom_id.id,
                })
        if new_lines:
            self.env['ebshel.purchase.vendor.quotation.line'].create(new_lines)
        return True

    # ── Smart-button navigation ───────────────────────────────────────────────
    def action_view_rfq(self):
        self.ensure_one()
        return {
            'name': _('RFQ'),
            'type': 'ir.actions.act_window',
            'res_model': 'ebshel.purchase.rfq',
            'view_mode': 'form',
            'res_id': self.rfq_id.id,
        }

    def action_view_vendor(self):
        self.ensure_one()
        if not self.vendor_id:
            raise UserError(_('This quotation has no registered vendor yet.'))
        return {
            'name': self.vendor_id.name,
            'type': 'ir.actions.act_window',
            'res_model': 'res.partner',
            'view_mode': 'form',
            'res_id': self.vendor_id.id,
        }

    def action_view_purchase_order(self):
        self.ensure_one()
        if not self.purchase_order_id:
            raise UserError(_('No purchase order has been created from this quotation yet.'))
        return {
            'name': self.purchase_order_id.name,
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'view_mode': 'form',
            'res_id': self.purchase_order_id.id,
        }

    def action_open_comparison(self):
        """Jump straight to the RFQ's vendor price comparison dashboard."""
        self.ensure_one()
        return self.rfq_id.action_open_comparison_view()
