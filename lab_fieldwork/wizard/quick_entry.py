# -*- coding: utf-8 -*-
"""Quick case entry (R1): every case collected at a clinic, punched in as one batch.

The full slip form is right when the executive is standing in front of the doctor with
questions to ask. It is wrong at 6 pm with eight impressions in the bag: eight rounds of
the same header is how same-day entry quietly becomes tomorrow-morning entry.

One header (doctor, date), one line per patient. Confirm creates one `lab.case` per
line under the day's visit — found or created — and can submit them all in the same tap.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class LabCaseQuickEntry(models.TransientModel):
    _name = 'lab.case.quick.entry'
    _description = 'Quick Case Entry'

    entry_date = fields.Date(default=fields.Date.context_today, required=True)
    executive_id = fields.Many2one(
        'res.users', default=lambda s: s.env.user, required=True,
        string='Executive')
    partner_id = fields.Many2one(
        'res.partner', string='Doctor / Clinic', required=True,
        domain="[]")
    submit_all = fields.Boolean(
        'Submit all for verification', default=True,
        help="Untick only when something still has to be checked before the slips go "
             "to the office.")
    backdate_reason = fields.Char(
        help="Required when entering cases later than the allowed window — the point "
             "of same-day entry is that it happens the same day.")
    line_ids = fields.One2many('lab.case.quick.entry.line', 'wizard_id',
                               string='Cases')

    can_edit_executive = fields.Boolean(compute='_compute_can_edit_executive')

    def _compute_can_edit_executive(self):
        allowed = self.env.user.has_group('lab_fieldwork.group_fieldwork_manager')
        for wizard in self:
            wizard.can_edit_executive = allowed

    @api.model
    def _backdate_days(self):
        try:
            return max(0, int(self.env['ir.config_parameter'].sudo().get_param(
                'lab_fieldwork.case_backdate_days', 0)))
        except (TypeError, ValueError):
            return 0

    def _find_or_create_visit(self):
        """The day's visit for this doctor, because a case cannot exist without one.

        Reusing an existing visit keeps the day honest: eight cases from one clinic are
        one visit, not eight.
        """
        self.ensure_one()
        Visit = self.env['lab.visit']
        visit = Visit.search([
            ('partner_id', '=', self.partner_id.id),
            ('user_id', '=', self.executive_id.id),
            ('date', '=', self.entry_date),
            ('state', '!=', 'cancel')], limit=1)
        if not visit:
            visit = Visit.create({
                'partner_id': self.partner_id.id,
                'user_id': self.executive_id.id,
                'date': self.entry_date,
                'purpose': 'order',
            })
        return visit

    def action_create_cases(self):
        self.ensure_one()
        if not self.line_ids:
            raise UserError(_("Add at least one case line."))

        limit = self._backdate_days()
        today = fields.Date.context_today(self)
        if (today - self.entry_date).days > limit and not self.backdate_reason:
            raise UserError(_(
                "This entry is dated %(d)s days back. Cases are entered the day they "
                "are collected — give the reason for the late entry.",
                d=(today - self.entry_date).days))

        visit = self._find_or_create_visit()
        Case = self.env['lab.case']
        cases = Case.browse()
        for line in self.line_ids:
            case = Case.create({
                'visit_id': visit.id,
                'patient': line.patient,
                'age': line.age,
                'gender': line.gender,
                'priority': line.priority,
                'note': line.remarks,
                'line_ids': [(0, 0, {
                    'product_id': line.product_id.id,
                    'ul': line.ul,
                    'quantity': line.quantity,
                })] if line.product_id else False,
            })
            if self.backdate_reason:
                case.message_post(body=_(
                    "Entered %(d)s day(s) late: %(why)s",
                    d=(today - self.entry_date).days, why=self.backdate_reason))
            cases |= case

        if self.submit_all:
            # The case model owns what "ready" means — reuse its own gate per slip, and
            # report the ones it refused rather than failing the whole batch.
            failed = []
            for case in cases:
                try:
                    # A savepoint: on a visit that is already finished, submitting
                    # also raises the order, and a refusal there must not leave
                    # the slip half-submitted with no order behind it.
                    with self.env.cr.savepoint():
                        case.action_submit()
                except UserError as exc:
                    failed.append('%s (%s)' % (case.patient, exc.args[0]))
            if failed:
                visit.message_post(body=_(
                    "Quick entry: %(n)s slip(s) could not be submitted and stay in "
                    "draft: %(who)s", n=len(failed), who='; '.join(failed)))

        return {
            'type': 'ir.actions.act_window',
            'name': _('Cases entered'),
            'res_model': 'lab.case',
            'view_mode': 'list,form',
            'domain': [('id', 'in', cases.ids)],
        }


class LabCaseQuickEntryLine(models.TransientModel):
    _name = 'lab.case.quick.entry.line'
    _description = 'Quick Case Entry Line'

    wizard_id = fields.Many2one('lab.case.quick.entry', required=True,
                                ondelete='cascade')
    patient = fields.Char(required=True)
    age = fields.Integer()
    gender = fields.Selection([('male', 'Male'), ('female', 'Female')])
    product_id = fields.Many2one(
        'product.product', string='Appliance',
        domain="[('sale_ok','=',True)]")
    ul = fields.Selection(
        [('upper', 'U'), ('lower', 'L'), ('ul', 'UL')], string='Jaw')
    quantity = fields.Float(default=1.0)
    priority = fields.Selection(
        [('low', 'Low'), ('normal', 'Normal'), ('urgent', 'Urgent'),
         ('emergency', 'Emergency')], default='normal', required=True)
    remarks = fields.Char()
    duplicate_hint = fields.Char(compute='_compute_duplicate_hint')

    @api.depends('patient', 'wizard_id.partner_id', 'wizard_id.entry_date')
    def _compute_duplicate_hint(self):
        """A warning, never a block (R1): same doctor, same patient, same day happens
        legitimately — two appliances for one child — and stopping the executive in the
        field over it is exactly what the requirement forbids.

        One query for the whole wizard rather than one per line: an executive punching
        in a clinic's worth of cases recomputes this on every keystroke, and a search
        per line turns a ten-case entry into ten round trips on a phone connection.
        """
        from ..models.lab_case import patient_key
        self.duplicate_hint = False
        candidates = self.filtered(lambda l: l.patient and l.wizard_id.partner_id)
        if not candidates:
            return

        # Group the lines by the (doctor, date) pair they belong to, then ask once per
        # pair — in practice a single pair, since a wizard has one header.
        by_header = {}
        for line in candidates:
            by_header.setdefault(
                (line.wizard_id.partner_id.id, line.wizard_id.entry_date), []
            ).append(line)

        for (partner_id, entry_date), lines in by_header.items():
            keys = {patient_key(l.patient) for l in lines}
            twins = self.env['lab.case'].search([
                ('partner_id', '=', partner_id),
                ('date', '=', entry_date),
                ('patient_key', 'in', list(keys)),
                ('state', '!=', 'cancel')])
            found = {}
            for twin in twins:
                found.setdefault(twin.patient_key, twin)
            for line in lines:
                twin = found.get(patient_key(line.patient))
                if twin:
                    line.duplicate_hint = _(
                        "Also entered today for this doctor (%s) — fine if it is a "
                        "second appliance.", twin.name)
