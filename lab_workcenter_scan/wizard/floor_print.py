# -*- coding: utf-8 -*-
"""The batch sheets: work chosen by a filter rather than by selecting records.

Each of these answers a question somebody currently answers by walking the racks, and
each is filtered to work that is genuinely still in the lab — see `report.lab.floor`
for why that filter is not optional here.
"""
from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import UserError

CALLOVER_ROWS = 60
# 2,791 open pickings render in 8.7s; the cap is a backstop against a runaway sheet,
# not a working limit, and per-route totals below are counted from the FULL domain so a
# truncated block can never be signed off as a complete handover.
MANIFEST_ROWS = 3000
# Measured ceiling for the PDF stage: past this, wkhtmltopdf dies of its own memory
# limit and returns nothing at all, which reads to the user as "the button is broken".
CLOSEOUT_MAX = 400
CLINIC_BLOCKS = 40


class LabCalloverWizard(models.TransientModel):
    _name = 'lab.callover.wizard'
    _description = 'Aged Case Callover'

    older_than = fields.Integer(
        'Older than (days)', default=90, required=True,
        help="Days since the case was ordered. There is no promised date anywhere in "
             "this database, so this is age — not lateness.")
    team_ids = fields.Many2many('crm.team', string='Sales Routes',
                                help="Leave empty for every route.")
    limit = fields.Integer('Most aged', default=CALLOVER_ROWS, required=True,
                           help="The oldest N cases. A huddle works a page, not a book.")

    def action_print(self):
        self.ensure_one()
        return self.env.ref(
            'lab_workcenter_scan.action_report_callover').report_action(self)

    def action_open_list(self):
        """The same cases on screen, for anyone who would rather click than chase."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Aged Cases'),
            'res_model': 'mrp.production',
            'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': [('id', 'in', self._cases().ids)],
        }

    def _domain(self):
        """Built once and used for BOTH the rows and the headline count.

        Rebuilding it for the count is how a route huddle ends up holding a sheet that
        says 867 next to a subtitle naming one route: the rows were filtered and the
        number was not.
        """
        Floor = self.env['report.lab.floor']
        domain = Floor._live_domain() + [
            ('date_start', '<', Floor._age_cutoff(self.older_than))]
        if self.team_ids:
            domain.append(('sale_id.team_id', 'in', self.team_ids.ids))
        return domain

    def _cases(self):
        return self.env['mrp.production'].search(
            self._domain(), order='date_start, id', limit=max(1, self.limit))

    def callover_data(self):
        """Oldest first, with the step each case is actually waiting at."""
        self.ensure_one()
        Floor = self.env['report.lab.floor']
        cases = self._cases()
        steps = Floor._first_open_step(cases)
        archs = Floor._arch_labels()
        rows = []
        for mo in cases:
            order = mo.sale_id
            step = steps.get(mo.id) or {}
            rows.append({
                'mo': mo,
                'days': Floor._days_in_lab(mo),
                'patient': Floor._patient_label(order, mo.name),
                'clinic': order.partner_id.display_name or '',
                'route': Floor._route_label(order.team_id),
                'order': order.name or '',
                'appliance': mo.product_id.display_name or '',
                'arch': Floor._arch_label(mo, archs),
                'station': (step.get('workcenter_id') or [0, ''])[1],
                'step': Floor._step_note((step.get('workcenter_id') or [0, ''])[1],
                                         step.get('name')),
                'rework': bool(order.is_rework) if 'is_rework' in order._fields else False,
            })
        total = self.env['mrp.production'].search_count(self._domain())
        return {'rows': rows, 'total': total, 'shown': len(rows),
                'routes': ', '.join(self.team_ids.mapped('name')),
                'rework': sum(1 for r in rows if r['rework']),
                'stamp': Floor._printed_stamp()}


class LabPackingManifestWizard(models.TransientModel):
    _name = 'lab.packing.manifest.wizard'
    _description = 'Route Packing Manifest'

    scope = fields.Selection(
        # "Reserved", not "picked": on this database `assigned` means stock was
        # reserved, and every one of the 154 assigned pickings has picked=False on
        # every move and no move lines at all. Calling that "packed" tells a packing
        # table the work is already done. (measured 2026-08-26)
        [('ready', 'Ready — stock reserved, waiting for a van'),
         ('all', 'Everything still open, including the backlog')],
        default='ready', required=True, string='Which boxes',
        help="Ready is what a packing table can actually work through today. The "
             "backlog is much larger and is a planning list, not a packing list.")
    team_ids = fields.Many2many('crm.team', string='Sales Routes',
                                help="Leave empty for every route.")

    def action_print(self):
        self.ensure_one()
        return self.env.ref(
            'lab_workcenter_scan.action_report_manifest').report_action(self)

    def manifest_data(self):
        """One block per route: what goes in that van, and a line to sign it out."""
        self.ensure_one()
        Floor = self.env['report.lab.floor']
        domain = Floor._live_picking_domain()
        if self.scope == 'ready':
            domain = domain + [('state', '=', 'assigned')]
        if self.team_ids:
            domain = domain + [('sale_id.team_id', 'in', self.team_ids.ids)]
        pickings = self.env['stock.picking'].search(
            domain, order='id', limit=MANIFEST_ROWS)

        # The TRUE size of each route, counted over the WHOLE domain rather than over
        # whatever survived the cap. A driver signing a block headed "31 boxes" is
        # signing for the route, so that number has to be the route's, not the page's.
        # Two queries, not one per route: the ids and their orders, then the teams.
        every = self.env['stock.picking'].search_read(domain, ['sale_id'])
        order_ids = [row['sale_id'][0] for row in every if row['sale_id']]
        teams = {order.id: order.team_id
                 for order in self.env['sale.order'].browse(set(order_ids))}
        totals, labels = defaultdict(int), {}
        for row in every:
            team = teams.get(row['sale_id'][0]) if row['sale_id'] else None
            key = Floor._route_key(team) or '~'
            labels.setdefault(key, Floor._route_label(team) or _('No route'))
            totals[key] += 1

        by_route = defaultdict(list)
        for picking in pickings:
            order = picking.sale_id
            key = Floor._route_key(order.team_id) or '~'
            labels.setdefault(key, Floor._route_label(order.team_id) or _('No route'))
            by_route[key].append({
                'picking': picking,
                'order': order.name or picking.origin or '',
                'clinic': picking.partner_id.display_name or '',
                'patient': Floor._patient_label(order),
                'items': len(picking.move_ids),
                'ready': picking.state == 'assigned',
            })
        routes = [{'name': labels[key], 'rows': rows, 'count': len(rows),
                   'total': totals.get(key, len(rows)),
                   'partial': totals.get(key, len(rows)) > len(rows)}
                  for key, rows in sorted(by_route.items(),
                                          key=lambda kv: labels[kv[0]])]

        return {'routes': routes,
                'total': self.env['stock.picking'].search_count(domain),
                'shown': len(pickings), 'stamp': Floor._printed_stamp(),
                # Only worth flagging when the sheet mixes ready and not-yet-picked;
                # on a ready-only manifest every row would carry it and say nothing.
                'show_ready': self.scope != 'ready',
                'scope': dict(self._fields['scope'].selection)[self.scope]}


class LabCloseoutWizard(models.TransientModel):
    _name = 'lab.closeout.wizard'
    _description = 'Close-out Worklist'

    limit = fields.Integer('How many', default=100, required=True)
    team_ids = fields.Many2many('crm.team', string='Sales Routes')

    def _domain(self):
        Floor = self.env['report.lab.floor']
        domain = Floor._closeout_domain()
        if self.team_ids:
            domain = domain + [('sale_id.team_id', 'in', self.team_ids.ids)]
        return domain

    def _cases(self):
        # Clamped where it is USED, not trusted from the field: the wizard's own alert
        # quotes 21,099, and typing that number would ask wkhtmltopdf for a 21,000-row
        # PDF that dies without producing a file.
        limit = max(1, min(self.limit or 1, CLOSEOUT_MAX))
        return self.env['mrp.production'].search(
            self._domain(), order='date_start, id', limit=limit)

    def action_print(self):
        self.ensure_one()
        return self.env.ref(
            'lab_workcenter_scan.action_report_closeout').report_action(self)

    def action_open_list(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Delivered but still open'),
            'res_model': 'mrp.production',
            'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': [('id', 'in', self._cases().ids)],
        }

    # Measured over a real 200-order batch on this database: 344.7s, so 1.7s a job.
    # Used only to tell the truth about how long the backlog will take.
    SECONDS_PER_JOB = 1.7

    def action_start_closing(self):
        """Switch on the scheduled action that closes the delivered backlog.

        Not "close them now": at 1.3 seconds a job the 20,978 waiting here are seven and
        a half hours of work, and a web request is dead after two minutes. So the button
        starts the job rather than pretending to do it, and says how long it will take.
        """
        self.ensure_one()
        cron = self.env.ref('lab_workcenter_scan.cron_close_delivered_backlog',
                            raise_if_not_found=False)
        if not cron:
            raise UserError(_("The scheduled action is missing. Reinstall the module."))
        # The cron closes the whole backlog; quoting this wizard's route-filtered count
        # would promise a small job and start a large one.
        total = self.env['mrp.production'].search_count(
            self.env['report.lab.floor']._closeout_domain())
        batch = int(self.env['ir.config_parameter'].sudo().get_param(
            'lab_workcenter_scan.close_batch', 200))
        try:
            cron.sudo().write({'active': True, 'nextcall': fields.Datetime.now()})
        except UserError:
            # ir.cron.write takes a row lock and refuses while a batch is running, so
            # pressing this twice would otherwise show a raw "record cannot be modified"
            # dialog. It is already doing exactly what the button asks for.
            return {
                'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': _("Already running"), 'type': 'info',
                           'message': _("A batch is being closed right now. Nothing "
                                        "more to start."), 'sticky': False},
            }
        runs = -(-total // max(1, batch))
        # Wall clock, not machine time: the runs are spaced by the schedule, so quoting
        # the hours of WORK would promise it finishes in half the time it really will.
        minutes_apart = {'minutes': 1, 'hours': 60, 'days': 1440}.get(
            cron.interval_type, 1) * cron.interval_number
        hours = max(runs * minutes_apart / 60.0,
                    total * self.SECONDS_PER_JOB / 3600.0)
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {
                'title': _("Closing has started"),
                'message': _(
                    "%(total)s manufacturing orders will be closed %(batch)s at a "
                    "time, about %(runs)s runs — roughly %(hours).0f hours from now, "
                    "because the runs are spaced out so the lab keeps working. "
                    "Deliveries validated from now on close their own jobs immediately. "
                    "To stop it, untick the scheduled action under Settings > Technical "
                    "> Scheduled Actions (between batches — it refuses while one is "
                    "running).",
                    total=total, batch=batch, runs=runs, hours=hours),
                'type': 'success', 'sticky': True,
            },
        }

    def closeout_data(self):
        self.ensure_one()
        Floor = self.env['report.lab.floor']
        cases = self._cases()
        rows = []
        for mo in cases:
            order = mo.sale_id
            shipped = order.picking_ids.filtered(
                lambda p: p.picking_type_id.code == 'outgoing' and p.state == 'done')
            last = max(shipped.mapped('date_done') or [False]) if shipped else False
            rows.append({
                'mo': mo,
                'order': order.name or '',
                'clinic': order.partner_id.display_name or '',
                'patient': Floor._patient_label(order, mo.name),
                'route': Floor._route_label(order.team_id),
                'delivered': self.env['lab.station']._lab_time(
                    last).strftime('%d/%m/%Y') if last else '',
                'days': Floor._days_in_lab(mo),
            })
        total = self.env['mrp.production'].search_count(self._domain())
        open_total = self.env['mrp.production'].search_count(
            [('state', 'not in', ('done', 'cancel'))])
        return {'rows': rows, 'total': total, 'open_total': open_total,
                'shown': len(rows), 'capped': total > CLOSEOUT_MAX,
                'routes': ', '.join(self.team_ids.mapped('name')),
                'stamp': Floor._printed_stamp()}


class LabClinicWorkWizard(models.TransientModel):
    _name = 'lab.clinic.work.wizard'
    _description = 'Work in Hand for a Clinic'

    partner_ids = fields.Many2many(
        'res.partner', string='Clinics',
        help="Leave empty for every clinic that has work with us right now.")
    team_ids = fields.Many2many('crm.team', string='Sales Routes')

    def action_print(self):
        self.ensure_one()
        return self.env.ref(
            'lab_workcenter_scan.action_report_clinic_work').report_action(self)

    def clinic_data(self):
        """One block per clinic — and only clinics that genuinely have live work.

        This is the one sheet here that leaves the building. Printing a case the doctor
        already has on their own shelf is not a saved phone call, it is a phone call
        caused, in front of a customer. Hence the live filter and the suppression of
        empty blocks: of 2,120 clinics only 807 have anything live. (client, 2026-08-26)
        """
        self.ensure_one()
        Floor = self.env['report.lab.floor']
        # A sheet addressed to a clinic must be asked for by clinic. Left to its default
        # this printed all 807 clinics with live work as 807 forced page breaks — an
        # 810-page PDF that killed wkhtmltopdf outright and produced nothing at all.
        # (measured 2026-08-26)
        if not self.partner_ids and not self.team_ids:
            raise UserError(_(
                "Choose the clinics, or a route, before printing.\n\n"
                "This sheet is addressed to a doctor, so it is printed for the ones you "
                "mean to send it to. Every clinic at once is 807 pages and no use to "
                "anybody."))
        domain = Floor._live_domain()
        if self.partner_ids:
            domain = domain + [('sale_id.partner_id', 'child_of',
                                self.partner_ids.mapped('commercial_partner_id').ids)]
        if self.team_ids:
            domain = domain + [('sale_id.team_id', 'in', self.team_ids.ids)]
        cases = self.env['mrp.production'].search(domain, order='date_start, id')
        if not cases:
            raise UserError(_(
                "Nothing is live for that selection. Every case for those clinics has "
                "either been delivered or is not in production."))

        steps = Floor._first_open_step(cases)
        by_clinic = defaultdict(list)
        for mo in cases:
            order = mo.sale_id
            step = steps.get(mo.id) or {}
            by_clinic[order.partner_id].append({
                'mo': mo,
                'route': Floor._route_label(order.team_id),
                'order': order.name or '',
                'patient': Floor._patient_label(order, mo.name),
                'appliance': mo.product_id.display_name or '',
                'days': Floor._days_in_lab(mo),
                'station': (step.get('workcenter_id') or [0, ''])[1],
                'packed': bool(order.picking_ids.filtered(
                    lambda p: p.picking_type_id.code == 'outgoing'
                    and p.state == 'assigned')),
            })
        clinics = []
        for partner, rows in by_clinic.items():
            # The route these CASES travel on, not the clinic's own default team: the
            # two disagree, and the sheet lists cases.
            case_routes = sorted({r['route'] for r in rows if r['route']})
            # Packed first: "it is on the van tonight" is the half of this sheet the
            # doctor actually wants, and burying it under work in progress wastes it.
            rows.sort(key=lambda r: (not r['packed'], -r['days']))
            clinics.append({
                'partner': partner, 'rows': rows, 'count': len(rows),
                'route': ', '.join(case_routes),
                'packed': sum(1 for r in rows if r['packed']),
            })
        clinics.sort(key=lambda c: c['partner'].display_name or '')
        shown = clinics[:CLINIC_BLOCKS]
        return {'clinics': shown, 'total_clinics': len(clinics),
                'shown_clinics': len(shown), 'stamp': Floor._printed_stamp()}


# --------------------------------------------------------------------- report models
class ReportCallover(models.AbstractModel):
    _name = 'report.lab_workcenter_scan.report_callover'
    _description = 'Aged Case Callover Report'

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env['lab.callover.wizard'].browse(docids)
        return {'doc_ids': docids, 'doc_model': 'lab.callover.wizard', 'docs': docs,
                'payload': {d.id: d.callover_data() for d in docs}}


class ReportManifest(models.AbstractModel):
    _name = 'report.lab_workcenter_scan.report_manifest'
    _description = 'Route Packing Manifest Report'

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env['lab.packing.manifest.wizard'].browse(docids)
        return {'doc_ids': docids,
                'doc_model': 'lab.packing.manifest.wizard', 'docs': docs,
                'payload': {d.id: d.manifest_data() for d in docs}}


class ReportCloseout(models.AbstractModel):
    _name = 'report.lab_workcenter_scan.report_closeout'
    _description = 'Close-out Worklist Report'

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env['lab.closeout.wizard'].browse(docids)
        return {'doc_ids': docids, 'doc_model': 'lab.closeout.wizard', 'docs': docs,
                'payload': {d.id: d.closeout_data() for d in docs}}


class ReportClinicWork(models.AbstractModel):
    _name = 'report.lab_workcenter_scan.report_clinic_work'
    _description = 'Clinic Work in Hand Report'

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env['lab.clinic.work.wizard'].browse(docids)
        return {'doc_ids': docids, 'doc_model': 'lab.clinic.work.wizard', 'docs': docs,
                'payload': {d.id: d.clinic_data() for d in docs}}


# ------------------------------------------------- sheets printed from a record list
class ReportStationQueue(models.AbstractModel):
    _name = 'report.lab_workcenter_scan.report_station_queue'
    _description = 'Station Queue Sheet'

    @api.model
    def _get_report_values(self, docids, data=None):
        Floor = self.env['report.lab.floor']
        workcenters = self.env['mrp.workcenter'].browse(docids)
        live = self.env['mrp.production'].search(Floor._live_domain())
        steps = Floor._first_open_step(live)

        # Index the live cases by the station they are actually waiting at.
        waiting = defaultdict(list)
        for mo in live:
            step = steps.get(mo.id)
            if not step or not step.get('workcenter_id'):
                continue
            waiting[step['workcenter_id'][0]].append((mo, step))

        archs = Floor._arch_labels()
        payload = {}
        for workcenter in workcenters:
            here = sorted(waiting.get(workcenter.id, []),
                          key=lambda pair: pair[0].date_start or fields.Datetime.now())
            rows = []
            for mo, step in here[:40]:
                order = mo.sale_id
                rows.append({
                    'mo': mo,
                    'step': step.get('name') or '',
                    'patient': Floor._patient_label(order, mo.name),
                    'clinic': order.partner_id.display_name or '',
                    'route': Floor._route_label(order.team_id),
                    'order': order.name or '',
                    'appliance': mo.product_id.display_name or '',
                    'arch': Floor._arch_label(mo, archs),
                    'days': Floor._days_in_lab(mo),
                    'rework': bool(order.is_rework)
                              if 'is_rework' in order._fields else False,
                })
            payload[workcenter.id] = {
                'rows': rows, 'total': len(here), 'stamp': Floor._printed_stamp(),
                'oldest': max((r['days'] for r in rows), default=0),
                # Has this bench ever actually accepted or handed on a job? Until it
                # has, the order of steps is the routing's and the sheet must say so
                # rather than imply it knows where each case physically is.
                'handovers': bool(self.env['mrp.workorder'].search_count([
                    ('workcenter_id', '=', workcenter.id),
                    '|', ('accepted_at', '!=', False),
                         ('handed_over_at', '!=', False)])),
                # 82% of the work genuinely still in this lab is a remake. Printing the
                # chip without the proportion makes a rework look like the exception it
                # very much is not. (measured 2026-08-26)
                'rework': sum(1 for r in rows if r['rework']),
            }
        return {'doc_ids': docids, 'doc_model': 'mrp.workcenter',
                'docs': workcenters, 'payload': payload}


class ReportRequisition(models.AbstractModel):
    _name = 'report.lab_workcenter_scan.report_requisition'
    _description = 'Component Requisition'

    @api.model
    def _get_report_values(self, docids, data=None):
        """What to fetch from the store for a batch of cases, added up.

        Aggregated on a folded product name, not on the product id: the master carries
        'Monomor' and 'monomor' as two products, and a slip that lists the same tin twice
        gets one of them ignored. (measured 2026-08-26)
        """
        Floor = self.env['report.lab.floor']
        productions = self.env['mrp.production'].browse(docids)
        totals = defaultdict(lambda: {'qty': 0.0, 'uom': '', 'names': set()})
        for mo in productions:
            for move in mo.move_raw_ids:
                product = move.product_id
                key = (' '.join((product.name or '').split()).lower(),
                       move.product_uom.id)
                bucket = totals[key]
                bucket['qty'] += move.product_uom_qty
                bucket['uom'] = move.product_uom.name or ''
                bucket['names'].add(product.display_name)
        lines = [{
            'name': sorted(v['names'])[0] if v['names'] else k[0],
            'aka': sorted(v['names'])[1:],
            'qty': round(v['qty'], 2),
            'uom': v['uom'],
        } for k, v in totals.items()]
        lines.sort(key=lambda line: line['name'].lower())
        return {
            'doc_ids': docids, 'doc_model': 'mrp.production', 'docs': productions,
            'lines': lines,
            'stamp': Floor._printed_stamp(),
            'cases': len(productions),
            'no_bom': len(productions.filtered(lambda m: not m.move_raw_ids)),
        }


class ReportTraveller(models.AbstractModel):
    _name = 'report.lab_workcenter_scan.report_traveller'
    _description = 'Case Traveller'

    @api.model
    def _get_report_values(self, docids, data=None):
        Floor = self.env['report.lab.floor']
        productions = self.env['mrp.production'].browse(docids)
        payload = {}
        for mo in productions:
            order = mo.sale_id
            payload[mo.id] = {
                'patient': Floor._patient_label(order, mo.name),
                'clinic': order.partner_id.display_name or '',
                'route': Floor._route_label(order.team_id),
                'days': Floor._days_in_lab(mo),
                'steps': mo.workorder_ids.sorted(lambda w: (w.sequence, w.id)),
                'stamp': Floor._printed_stamp(),
            }
        return {'doc_ids': docids, 'doc_model': 'mrp.production',
                'docs': productions, 'payload': payload}


class ReportHoldTag(models.AbstractModel):
    _name = 'report.lab_workcenter_scan.report_hold_tag'
    _description = 'Hold Tag'

    @api.model
    def _get_report_values(self, docids, data=None):
        Floor = self.env['report.lab.floor']
        productions = self.env['mrp.production'].browse(docids)
        payload = {}
        for mo in productions:
            order = mo.sale_id
            payload[mo.id] = {
                'patient': Floor._patient_label(order, mo.name),
                'clinic': order.partner_id.display_name or '',
                'stamp': Floor._printed_stamp(),
            }
        return {'doc_ids': docids, 'doc_model': 'mrp.production',
                'docs': productions, 'payload': payload}
