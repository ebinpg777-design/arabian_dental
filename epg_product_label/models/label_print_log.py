# -*- coding: utf-8 -*-
"""A record of every label run.

Labels are consumable stock: someone always needs to know who burned through a
roll, and "print those same forty tags again" is a weekly request.  The wizard is
transient and forgets everything, so the facts worth keeping are copied here as
the job leaves.
"""

import json

from odoo import _, api, fields, models


class EpgLabelPrintLog(models.Model):
    _name = 'epg.label.print.log'
    _description = 'Label Print History'
    _order = 'create_date desc, id desc'
    _rec_name = 'display_summary'

    template_id = fields.Many2one(
        'epg.label.template', string='Template', required=True,
        ondelete='restrict', index=True)
    user_id = fields.Many2one(
        'res.users', string='Printed By', required=True, index=True,
        default=lambda self: self.env.user)
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company,
        index=True)
    print_date = fields.Datetime(
        string='Printed On', required=True, default=fields.Datetime.now, index=True)

    label_type = fields.Selection([
        ('product', 'Product'),
        ('partner', 'Contact / Address'),
        ('lot', 'Lot / Serial Number'),
    ], string='Label For', required=True)
    output_format = fields.Selection([
        ('pdf', 'PDF'),
        ('zpl', 'ZPL (Zebra)'),
        ('preview', 'Preview'),
    ], string='Format', required=True)

    label_count = fields.Integer(string='Labels', required=True)
    page_count = fields.Integer(string='Pages')
    record_count = fields.Integer(string='Records Printed')

    res_model = fields.Char(string='Record Model')
    res_ids = fields.Text(
        string='Record IDs',
        help="JSON list of the printed record ids, used to repeat the run.")
    record_summary = fields.Char(string='Records')
    display_summary = fields.Char(compute='_compute_display_summary', store=True)

    pricelist_id = fields.Many2one('product.pricelist', string='Pricelist')
    promo_pricelist_id = fields.Many2one(
        'product.pricelist', string='Promotional Pricelist')
    quantity = fields.Integer(string='Copies per Record')
    skip_labels = fields.Integer(string='Skipped')
    printer_url = fields.Char(string='Printer Endpoint')

    @api.depends('template_id', 'label_count', 'record_summary')
    def _compute_display_summary(self):
        for log in self:
            log.display_summary = _(
                "%(count)s × %(template)s",
                count=log.label_count, template=log.template_id.name or '')

    @api.model
    def _log_print(self, wizard, output_format, label_count=None):
        """Record a print run. Never raises: a logging problem must not eat a job."""
        try:
            records = wizard._records()
            return self.sudo().create({
                'template_id': wizard.template_id.id,
                'user_id': self.env.user.id,
                'company_id': (wizard.company_id or self.env.company).id,
                'label_type': wizard.label_type,
                'output_format': output_format,
                'label_count': label_count if label_count is not None
                               else wizard.label_count,
                'page_count': wizard.page_count,
                'record_count': len(records),
                'res_model': records._name if records else False,
                'res_ids': json.dumps(records.ids) if records else '[]',
                'record_summary': self._summarise(records),
                'pricelist_id': wizard.pricelist_id.id,
                'promo_pricelist_id': wizard.promo_pricelist_id.id,
                'quantity': wizard.quantity,
                'skip_labels': wizard.skip_labels,
                'printer_url': wizard.printer_url if output_format == 'zpl' else False,
            })
        except Exception:  # pragma: no cover - best-effort bookkeeping
            self.env['ir.logging'].sudo().create({
                'name': 'epg_product_label',
                'type': 'server',
                'level': 'WARNING',
                'dbname': self.env.cr.dbname,
                'message': 'Could not write the label print history entry.',
                'path': __name__,
                'func': '_log_print',
                'line': '0',
            })
            return self.browse()

    @staticmethod
    def _summarise(records, limit=3):
        """A short human label for the printed selection."""
        if not records:
            return ''
        names = records[:limit].mapped('display_name')
        summary = ', '.join(name for name in names if name)
        if len(records) > limit:
            summary += _(" and %s more", len(records) - limit)
        return summary[:255]

    def action_reprint(self):
        """Reopen the print wizard with exactly the settings of this run."""
        self.ensure_one()
        try:
            record_ids = json.loads(self.res_ids or '[]')
        except ValueError:
            record_ids = []
        # Records get deleted; a repeat print should quietly skip those rather
        # than fail on a stale id.
        model = self.env.get(self.res_model) if self.res_model else None
        live_ids = model.browse(record_ids).exists().ids if model is not None else []

        field_by_model = {
            'product.product': 'product_ids',
            'product.template': 'product_tmpl_ids',
            'res.partner': 'partner_ids',
            'stock.lot': 'lot_ids',
        }
        context = {
            'default_template_id': self.template_id.id,
            'default_label_type': self.label_type,
            'default_output_format': self.output_format
                                     if self.output_format != 'preview' else 'pdf',
            'default_quantity': self.quantity or 1,
            'default_skip_labels': self.skip_labels,
            'default_pricelist_id': self.pricelist_id.id,
            'default_promo_pricelist_id': self.promo_pricelist_id.id,
        }
        target = field_by_model.get(self.res_model)
        if target and live_ids:
            context['default_%s' % target] = [(6, 0, live_ids)]
        action = self.env['ir.actions.act_window']._for_xml_id(
            'epg_product_label.action_epg_label_print')
        action['context'] = context
        return action

    def action_view_records(self):
        """Open the records this run covered."""
        self.ensure_one()
        try:
            record_ids = json.loads(self.res_ids or '[]')
        except ValueError:
            record_ids = []
        model = self.env.get(self.res_model) if self.res_model else None
        live = model.browse(record_ids).exists() if model is not None else None
        if not live:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'type': 'warning',
                    'title': _("Nothing to show"),
                    'message': _("None of the printed records still exist."),
                },
            }
        return {
            'type': 'ir.actions.act_window',
            'name': _('Printed Records'),
            'res_model': self.res_model,
            'view_mode': 'list,form',
            'domain': [('id', 'in', live.ids)],
        }
