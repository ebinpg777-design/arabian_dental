# -*- coding: utf-8 -*-
"""Report models behind the PDF and ZPL outputs.

Both reports are data-driven: the print wizard hands over its own id, and the
report reads the slots back from it.  That keeps the URL short no matter how many
labels are queued, and it means the wizard - not the report - owns the rules about
copies, skipped positions and pricelists.
"""

import logging

from markupsafe import Markup

from odoo import _, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ReportEpgLabel(models.AbstractModel):
    _name = 'report.epg_product_label.report_label'
    _description = 'Product Label Builder - PDF'

    def _get_report_values(self, docids, data=None):
        data = data or {}
        wizard = self._get_wizard(docids, data)
        template = wizard.template_id
        slots = wizard._collect_slots()
        if not slots:
            raise UserError(_("There is nothing to print."))
        context = template._build_render_context(
            pricelist=wizard.pricelist_id,
            promo_pricelist=wizard.promo_pricelist_id,
            partner=wizard.partner_ids[:1] or None,
            company=wizard.company_id,
            quantity=wizard.price_quantity)
        pages = template._render_pages_html(slots, context, skip=wizard.skip_labels)
        return {
            'doc_ids': wizard.ids,
            'doc_model': 'epg.label.print',
            'docs': wizard,
            'template': template,
            'pages': [Markup(page) for page in pages],
            'page_style': template._page_style(),
            'show_grid': wizard.show_cut_grid,
            'grid_style': self._grid_style(template) if wizard.show_cut_grid else '',
        }

    def _get_wizard(self, docids, data):
        wizard_id = data.get('wizard_id') or (docids and docids[0])
        wizard = self.env['epg.label.print'].browse(wizard_id).exists()
        if not wizard:
            raise UserError(_(
                "The print request has expired. Please open the print wizard again."))
        return wizard

    @staticmethod
    def _grid_style(template):
        """A hairline outline around every label slot, for cutting by hand."""
        return 'outline:0.1mm dashed #b0b0b0;outline-offset:0;'


class ReportEpgLabelZpl(models.AbstractModel):
    _name = 'report.epg_product_label.report_label_zpl'
    _description = 'Product Label Builder - ZPL'

    def _get_report_values(self, docids, data=None):
        data = data or {}
        wizard_id = data.get('wizard_id') or (docids and docids[0])
        wizard = self.env['epg.label.print'].browse(wizard_id).exists()
        if not wizard:
            raise UserError(_(
                "The print request has expired. Please open the print wizard again."))
        return {
            'doc_ids': wizard.ids,
            'doc_model': 'epg.label.print',
            'docs': wizard,
            'zpl': wizard._build_zpl(),
        }
