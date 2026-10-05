# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
import pytz

from odoo import api, fields, models
from odoo.exceptions import ValidationError

DEFAULT_AGING_BUCKETS = '30,60,90,180'
DEFAULT_ABC = '80,95'
DEFAULT_XYZ = '0.5,1.0'


class ResCompany(models.Model):
    _inherit = 'res.company'

    asr_report_tz = fields.Selection(
        selection='_asr_tz_selection', string='Reporting Timezone',
        compute='_compute_asr_report_tz', store=True, readonly=False,
        help="Day boundaries of every stock report. Odoo stores move dates in UTC; "
             "a move done at 01:00 in this timezone belongs to that day, not to the day before.")
    asr_aging_buckets = fields.Char(
        string='Aging Buckets (days)', default=DEFAULT_AGING_BUCKETS,
        help="Upper bounds of the aging buckets in days, separated by commas. "
             "'30,60,90,180' gives 0-30, 31-60, 61-90, 91-180 and over 180.")
    asr_slow_days = fields.Integer(
        string='Slow Moving After (days)', default=90,
        help="A product with stock and no issue for this many days is slow moving; "
             "with no issue at all in twice this period it is non-moving.")
    asr_abc_thresholds = fields.Char(
        string='ABC Thresholds (%)', default=DEFAULT_ABC,
        help="Cumulative share of issue value that closes class A and class B. '80,95' means "
             "A covers the first 80%, B the next 15%, C the rest.")
    asr_xyz_thresholds = fields.Char(
        string='XYZ Thresholds', default=DEFAULT_XYZ,
        help="Coefficients of variation of the monthly issue quantity that close class X and class Y. "
             "'0.5,1.0' means X below 0.5, Y up to 1.0, Z above.")
    asr_otif_basis = fields.Selection([
        ('first', 'First delivery of the line'),
        ('last', 'Last delivery of the line'),
    ], string='OTIF Delivery Date', default='first', required=True,
        help="Which delivery date counts as 'delivered' when an order line ships in several parts.")
    asr_otif_tolerance = fields.Float(
        string='OTIF In-Full Tolerance (%)', default=0.0,
        help="A line is in full when the delivered quantity is at least the ordered quantity "
             "minus this percentage.")
    asr_cover_days = fields.Integer(
        string='Days of Cover Window', default=90,
        help="Average daily issue is computed over this many days back from the report date.")
    asr_pdf_row_cap = fields.Integer(
        string='PDF Row Cap', default=2000,
        help="A PDF stops after this many rows and says so; use Excel for the full list.")
    asr_india_gst = fields.Boolean(
        string='India GST Reports', default=True,
        help="Show the GST stock register and the monthly production account.")
    asr_wip_component_price = fields.Selection([
        ('standard', "Today's standard price (as Odoo's WIP entry posts it)"),
        ('avg_cost', "Average cost of the product on the report date"),
    ], string='WIP Component Pricing', default='standard', required=True)
    asr_engine_state = fields.Selection([
        ('empty', 'Not built'),
        ('rebuilding', 'Rebuilding'),
        ('ready', 'Ready'),
    ], string='Stock Summary State', default='empty', readonly=True, copy=False)
    asr_engine_last_run = fields.Datetime(string='Stock Summary Last Run', readonly=True, copy=False)

    @api.model
    def _asr_tz_selection(self):
        return [(tz, tz) for tz in pytz.all_timezones]

    @api.depends('partner_id.tz')
    def _compute_asr_report_tz(self):
        for company in self:
            if not company.asr_report_tz:
                company.asr_report_tz = company.partner_id.tz or 'UTC'

    @api.constrains('asr_aging_buckets', 'asr_abc_thresholds', 'asr_xyz_thresholds')
    def _check_asr_lists(self):
        for company in self:
            buckets = company._asr_parse_list('asr_aging_buckets', int)
            if buckets != sorted(buckets) or any(b <= 0 for b in buckets):
                raise ValidationError(self.env._("Aging buckets must be positive and increasing, e.g. 30,60,90,180."))
            abc = company._asr_parse_list('asr_abc_thresholds', float)
            if len(abc) != 2 or not 0 < abc[0] < abc[1] <= 100:
                raise ValidationError(self.env._("ABC thresholds need two increasing percentages, e.g. 80,95."))
            xyz = company._asr_parse_list('asr_xyz_thresholds', float)
            if len(xyz) != 2 or not 0 < xyz[0] < xyz[1]:
                raise ValidationError(self.env._("XYZ thresholds need two increasing coefficients, e.g. 0.5,1.0."))

    def _asr_parse_list(self, field_name, cast):
        self.ensure_one()
        raw = self[field_name] or ''
        try:
            return [cast(part.strip()) for part in raw.split(',') if part.strip()]
        except ValueError:
            raise ValidationError(self.env._("'%(value)s' is not a valid list of numbers.", value=raw))

    def _asr_tz(self):
        self.ensure_one()
        try:
            return pytz.timezone(self.asr_report_tz or 'UTC')
        except pytz.UnknownTimeZoneError:
            return pytz.utc

    def _asr_day_end_utc(self, day):
        """The last instant (naive UTC datetime) of ``day`` in the company's reporting timezone.
        This is exactly the ``to_date`` the Stock report needs to show the closing of that day."""
        self.ensure_one()
        tz = self._asr_tz()
        local = tz.localize(fields.Datetime.to_datetime(day).replace(hour=23, minute=59, second=59, microsecond=999999))
        return local.astimezone(pytz.utc).replace(tzinfo=None)

    def _asr_day_start_utc(self, day):
        self.ensure_one()
        tz = self._asr_tz()
        local = tz.localize(fields.Datetime.to_datetime(day).replace(hour=0, minute=0, second=0, microsecond=0))
        return local.astimezone(pytz.utc).replace(tzinfo=None)

    def _asr_local_day(self, dt):
        """The reporting-timezone day of a naive UTC datetime."""
        self.ensure_one()
        if not dt:
            return False
        return pytz.utc.localize(dt).astimezone(self._asr_tz()).date()

    def write(self, vals):
        tz_changed = self.filtered(lambda c: 'asr_report_tz' in vals and vals['asr_report_tz'] != c.asr_report_tz)
        cost_changed = self.filtered(lambda c: 'cost_method' in vals and vals['cost_method'] != c.cost_method)
        res = super().write(vals)
        if tz_changed or cost_changed:
            self.env['asr.stock.dirty']._mark_full_rebuild(tz_changed | cost_changed)
        return res
