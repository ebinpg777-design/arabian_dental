# -*- coding: utf-8 -*-
"""Purchase documents to a vendor on WhatsApp: the PO, and the Purchase Request's
RFQ as PDF and Excel. (client, 2026-11-27: "Add a button in PO and RFQ (in PR
module) to send the PO pdf and RFQ (pdf and excel) through WhatsApp")

On the lab's free channel a document travels as a private link in the text; two
documents for one vendor are two messages opened as the one WhatsApp they receive.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


class EpgWhatsappMessage(models.Model):
    _inherit = 'epg.whatsapp.message'

    @api.model
    def _lab_send_documents(self, record, partner, number, documents):
        """One message per document, opened in the send dialog as one WhatsApp.

        `documents`: [(text, filename, content, mimetype)].
        """
        number = self.env['epg.whatsapp.template']._normalise_number(number or '', record)
        if not number:
            raise UserError(_(
                "%s has no WhatsApp number. Add a mobile number to the contact first.",
                partner.display_name if partner else _("This vendor")))
        account = self.env['epg.whatsapp.account']._default_account()
        if not account:
            raise UserError(_("No WhatsApp sender is configured (WhatsApp > Configuration > Senders)."))
        messages = self.browse()
        for text, filename, content, mimetype in documents:
            message = self.create({
                'account_id': account.id,
                'res_model': record._name,
                'res_id': record.id,
                'partner_id': partner.id if partner else False,
                'number': number,
                'body': text,
                'user_id': self.env.user.id,
            })
            attachment = self.env['ir.attachment'].sudo().create({
                'name': filename, 'type': 'binary', 'raw': content, 'mimetype': mimetype,
                'res_model': self._name, 'res_id': message.id,
            })
            message.sudo().attachment_id = attachment.id
            messages |= message
        messages.action_send()
        if account.channel != 'link':
            # The Cloud API sends at once: nothing to open.
            return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
                'type': 'success', 'title': _("WhatsApp"),
                'message': _("Sent to %s.", partner.display_name if partner else number)}}
        return {'type': 'ir.actions.client', 'tag': 'epg_whatsapp_send',
                'params': {'message_id': messages[0].id, 'message_ids': messages.ids,
                           'close': False, 'reload': True, 'discard_on_close': True}}


def _vendor_number(partner):
    if not partner:
        return ''
    for name in ('whatsapp_number', 'mobile', 'phone'):
        if name in partner._fields and partner[name]:
            return partner[name]
    return ''


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    def action_whatsapp_vendor(self):
        """The PO (or, before confirmation, the RFQ) PDF to the vendor."""
        self.ensure_one()
        is_rfq = self.state in ('draft', 'sent', 'to approve')
        report = self.env.ref('purchase.report_purchase_quotation' if is_rfq
                              else 'purchase.action_report_purchase_order')
        pdf, _ext = report.sudo()._render_qweb_pdf(report.report_name, self.ids)
        kind = _("Request for Quotation") if is_rfq else _("Purchase Order")
        vendor = self.partner_id
        if is_rfq:
            text = _("Dear %(vendor)s,\nPlease find our Request for Quotation %(name)s. "
                     "Kindly send us your best rates.\n- %(company)s",
                     vendor=vendor.name, name=self.name, company=self.company_id.name)
        else:
            text = _("Dear %(vendor)s,\nPlease find our Purchase Order %(name)s dated %(date)s. "
                     "Please quote the PO number on your invoice and delivery challan.\n- %(company)s",
                     vendor=vendor.name, name=self.name, company=self.company_id.name,
                     date=fields.Date.to_date(self.date_approve or self.date_order).strftime('%d/%m/%Y'))
        filename = '%s - %s.pdf' % (kind, (self.name or '').replace('/', '-'))
        return self.env['epg.whatsapp.message']._lab_send_documents(
            self, vendor, _vendor_number(vendor), [(text, filename, pdf, 'application/pdf')])


class PurchaseVendorQuotation(models.Model):
    _inherit = 'ebshel.purchase.vendor.quotation'

    def action_whatsapp_rfq(self):
        """The RFQ to this vendor: the printed sheet and the Excel to fill in."""
        self.ensure_one()
        rfq = self.rfq_id
        vendor = self.vendor_id
        number = _vendor_number(vendor) if vendor else (self.new_vendor_phone or '')
        name = vendor.name if vendor else (self.new_vendor_name or _("Sir/Madam"))
        report = self.env.ref('ebshel_purchase_request.action_report_rfq')
        pdf, _ext = report.sudo()._render_qweb_pdf(report.report_name, rfq.ids)
        safe = (rfq.name or '').replace('/', '-')
        by = _(", to be quoted by %s", rfq.deadline.strftime('%d/%m/%Y')) if rfq.deadline else ''
        first = _("Dear %(vendor)s,\nPlease find our Request for Quotation %(name)s%(by)s. "
                  "Please fill in your rates in the yellow cells of the Excel sheet below and "
                  "send it back to us.\n- %(company)s",
                  vendor=name, name=rfq.name, by=by, company=rfq.company_id.name)
        second = _("RFQ %s - Excel sheet for your rates", rfq.name)
        return self.env['epg.whatsapp.message']._lab_send_documents(rfq, vendor, number, [
            (first, _('Request for Quotation - %s.pdf', safe), pdf, 'application/pdf'),
            (second, rfq._rfq_xlsx_filename(), rfq._rfq_xlsx_content(), XLSX),
        ])


class PurchaseRFQ(models.Model):
    _inherit = 'ebshel.purchase.rfq'

    def action_whatsapp_vendors(self):
        """From the header: straight to the vendor when there is one; with several,
        each vendor's row has its own WhatsApp button."""
        self.ensure_one()
        quotations = self.vendor_quotation_ids
        if not quotations:
            raise UserError(_("Add the vendors first (Vendors tab), then send them the RFQ."))
        if len(quotations) == 1:
            return quotations.action_whatsapp_rfq()
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'type': 'info', 'title': _("Which vendor?"),
            'message': _("This RFQ has %s vendors: press the WhatsApp button on a vendor's "
                         "row under Vendors to send them the PDF and the Excel sheet.",
                         len(quotations))}}
