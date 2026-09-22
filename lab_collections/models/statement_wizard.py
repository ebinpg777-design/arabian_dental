# -*- coding: utf-8 -*-
"""The statement dialog, opened by whoever is standing in front of the doctor.

The dialog reads ledger lines as the person asking, and a field executive may not
read the ledger - so the Statement button used to hand them a finished PDF on
fixed defaults instead. That answered one question only: an executive at the door
needs "since April", or open items only, the same as the office does.

So the dialog opens for them too, fixed to the clinic they came from, and the
render runs elevated once the clinic is confirmed to be on their round - the same
scope check the card's download already made. (client, 2026-09-18)
"""
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


class PartnerStatementWizard(models.TransientModel):
    _inherit = 'epg.partner.statement.wizard'

    # Set when the dialog is opened from Collections by somebody without an
    # accounting role: the clinic is theirs to look at, the selection is not
    # theirs to widen.
    scoped_to_round = fields.Boolean(readonly=True, copy=False)

    @api.model
    def open_for_partner(self, partner, name=None):
        """The statement dialog for one clinic, whoever is asking."""
        Perf = self.env['lab.collection.performance']
        scoped = not Perf._is_accounting_user()
        wizard = self.create({
            'partner_ids': [(6, 0, partner.ids)],
            'scoped_to_round': scoped,
            # The floor asks the ledger which clinics are worth printing, which is
            # a question this person may not put to it - and pointless for one
            # clinic they asked for by name. (client, 2026-09-18)
            **({'min_outstanding': 0.0} if scoped else {}),
        })
        return {
            'type': 'ir.actions.act_window',
            'name': name or _('Statement — %s', partner.display_name),
            'res_model': self._name,
            'res_id': wizard.id,
            'view_mode': 'form',
            'views': [(self.env.ref(
                'epg_partner_statement.view_partner_statement_wizard').id, 'form')],
            'target': 'new',
        }

    def _scope_partners(self, partners):
        """The clinics this person may be shown, or an error naming the others."""
        self.ensure_one()
        Perf = self.env['lab.collection.performance']
        Perf._check_collections_access()
        outside = partners.filtered(
            lambda p: not Perf._partner_in_viewer_scope(p.commercial_partner_id))
        if outside:
            raise AccessError(_("Not on your round: %s.",
                                ', '.join(outside[:5].mapped('display_name'))))
        return partners

    def _elevated(self):
        """Does this run have to be rendered for somebody who cannot read the ledger?"""
        self.ensure_one()
        return bool(self.scoped_to_round) and not \
            self.env['lab.collection.performance']._is_accounting_user()

    def _scoped_selection(self):
        """The clinics of a scoped run: the ones it was opened on, checked. Never
        through `_partners()`, which asks the ledger questions this person may not
        put to it."""
        self.ensure_one()
        if not self.partner_ids:
            raise UserError(_("Nothing to print: the clinic is missing."))
        return self._scope_partners(self.partner_ids)

    def action_print(self):
        if not self._elevated():
            return super().action_print()
        partners = self._scoped_selection()
        pdf = self.env['epg.partner.statement'].sudo().render_pdf(partners, self._options())
        name = _('Statement - %s.pdf', partners.display_name) if len(partners) == 1 \
            else _('Statements.pdf')
        return self.env['lab.collection.performance']._pdf_download(pdf, name)

    def action_export_xlsx(self):
        if not self._elevated():
            return super().action_export_xlsx()
        partners = self._scoped_selection()
        content = self.env['epg.partner.statement'].sudo().render_xlsx(
            partners, self._options())
        return self.env['lab.collection.performance']._file_download(
            content, _('Statement - %s.xlsx', partners.display_name)
            if len(partners) == 1 else _('Statements.xlsx'),
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    def action_send_email(self):
        if self._elevated():
            raise AccessError(_(
                "Statements are e-mailed from the accounts office. Print it here "
                "and hand it over, or ask accounts to send it."))
        return super().action_send_email()
