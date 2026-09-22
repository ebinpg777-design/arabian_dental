# -*- coding: utf-8 -*-
import base64
import math

from odoo import http, _
from odoo.exceptions import AccessError, MissingError
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal, pager as portal_pager


class LabCustomerPortal(CustomerPortal):
    """A doctor's own cases.

    Deliberately separate from `/my/orders`. A sale order lists prices, taxes and terms;
    a doctor opening the portal is looking for one child's appliance and where it has
    got to. Same records, a different question — so a different page rather than a
    quotation list with a status column bolted on.
    """

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if 'case_count' in counters:
            partner = request.env.user.partner_id
            values['case_count'] = request.env['sale.order'].search_count(
                self._case_domain(partner)) if partner else 0
        return values

    def _case_domain(self, partner):
        """Every case belonging to this doctor's practice.

        Matched on the COMMERCIAL partner: a clinic with three branch contacts is one
        practice, and a doctor who cannot see the case they sent from the other branch
        will ring the lab about it.
        """
        commercial = partner.commercial_partner_id or partner
        return [('partner_id', 'child_of', commercial.id),
                ('state', 'not in', ('draft', 'sent'))]

    # The stage a case is at is derived, not stored, so it cannot be a search domain.
    # These filters narrow on what IS in the database, and the stage chips below filter
    # the page in the browser — see the template.
    STAGE_CHIPS = ('in_lab', 'ready', 'sent', 'delivered')

    @http.route(['/my/cases', '/my/cases/page/<int:page>'], type='http',
                auth='user', website=True)
    def portal_my_cases(self, page=1, sortby='date', filterby='all', search='',
                        search_in='all', **kw):
        partner = request.env.user.partner_id
        Order = request.env['sale.order']
        domain = self._case_domain(partner)

        searchbar_sortings = {
            'date': {'label': _('Newest first'), 'order': 'date_order desc'},
            'patient': {'label': _('Patient'), 'order': 'patient, date_order desc'},
            'name': {'label': _('Reference'), 'order': 'name desc'},
        }
        # Filters a doctor actually wants: what is still coming, and what has arrived.
        searchbar_filters = {
            'all': {'label': _('All'), 'domain': []},
            'open': {'label': _('Still with the lab'),
                     'domain': [('state', 'not in', ('cancel',))]},
        }
        searchbar_inputs = {
            'all': {'input': 'all', 'label': _('Patient or reference')},
            'patient': {'input': 'patient', 'label': _('Patient')},
            'name': {'input': 'name', 'label': _('Reference')},
        }
        domain += searchbar_filters.get(filterby, searchbar_filters['all'])['domain']
        if search:
            if search_in == 'patient':
                domain += [('patient', 'ilike', search)]
            elif search_in == 'name':
                domain += [('name', 'ilike', search)]
            else:
                domain += ['|', ('name', 'ilike', search),
                           ('patient', 'ilike', search)]
        order = searchbar_sortings.get(sortby, searchbar_sortings['date'])['order']

        total = Order.search_count(domain)
        url_args = {'sortby': sortby, 'filterby': filterby,
                    'search': search, 'search_in': search_in}
        pager = portal_pager(url='/my/cases', url_args=url_args,
                             total=total, page=page, step=self._items_per_page)
        cases = Order.search(domain, order=order, limit=self._items_per_page,
                             offset=pager['offset'])
        request.session['my_cases_history'] = cases.ids[:100]

        return request.render('lab_portal.portal_my_cases', {
            'cases': cases,
            'page_name': 'case',
            'pager': pager,
            'default_url': '/my/cases',
            'searchbar_sortings': searchbar_sortings,
            'sortby': sortby,
            'searchbar_filters': searchbar_filters,
            'filterby': filterby,
            'searchbar_inputs': searchbar_inputs,
            'search_in': search_in,
            'search': search,
            'summary': self._case_summary(partner),
        })

    def _case_summary(self, partner):
        """Counts for the tiles at the top of the page.

        Computed over every case, not just the page being shown: a tile that says "3 in
        the lab" while meaning "3 on this page" is worse than no tile.
        """
        cases = request.env['sale.order'].search(self._case_domain(partner))
        counts = dict.fromkeys(self.STAGE_CHIPS, 0)
        for case in cases:
            stage = case.portal_stage
            if stage in counts:
                counts[stage] += 1
        counts['total'] = len(cases)
        return counts

    @http.route(['/my/cases/<int:case_id>'], type='http', auth='public', website=True)
    def portal_my_case(self, case_id, access_token=None, **kw):
        try:
            # The standard portal check: ownership, or a signed token for someone who
            # was sent the link. Rewriting either is how portals leak.
            case_sudo = self._document_check_access('sale.order', case_id, access_token)
        except (AccessError, MissingError):
            return request.redirect('/my')

        return request.render('lab_portal.portal_case_page', {
            'case': case_sudo,
            'lines': case_sudo.portal_case_lines(),
            'steps': case_sudo.portal_case_steps(),
            'productions': case_sudo.portal_productions(),
            'promise': case_sudo.portal_promise(),
            'page_name': 'case',
            'token': access_token,
        })

    # ------------------------------------------------------------------ Digital Rx
    def _rx_partner(self):
        """The practice a submission belongs to — the commercial partner, so a branch
        contact's request is filed under the clinic, not under the branch."""
        partner = request.env.user.partner_id
        return partner.commercial_partner_id or partner

    @http.route(['/my/rx'], type='http', auth='user', website=True)
    def portal_my_rx(self, **kw):
        requests = request.env['lab.case.request'].search(
            [('partner_id', 'child_of', self._rx_partner().id)])
        return request.render('lab_portal.portal_my_rx', {
            'rx_requests': requests, 'page_name': 'rx',
        })

    @http.route(['/my/rx/new'], type='http', auth='user', website=True,
                methods=['GET'])
    def portal_rx_form(self, **kw):
        return request.render('lab_portal.portal_rx_form', {
            'products': request.env['product.product'].sudo().search(
                [('sale_ok', '=', True)], order='name', limit=300),
            'page_name': 'rx',
            'error': kw.get('error'),
        })

    @http.route(['/my/rx/new'], type='http', auth='user', website=True,
                methods=['POST'], csrf=True)
    def portal_rx_submit(self, **post):
        """Accept a prescription from a doctor.

        sudo() to CREATE only: a portal user has no write access to the model (by
        design — this is outside input landing in the lab's system), but they must be
        able to send one in. The partner is taken from the SESSION, never from the
        form, so a doctor cannot file a case against somebody else's practice.
        """
        patient = (post.get('patient') or '').strip()
        product_ids = request.httprequest.form.getlist('product_id')
        if not patient or not any(product_ids):
            return request.redirect('/my/rx/new?error=1')

        lines, error = self._rx_lines(
            request.env, product_ids,
            request.httprequest.form.getlist('ul'),
            request.httprequest.form.getlist('quantity'))
        if error:
            return request.redirect('/my/rx/new?error=%s' % error)

        gender = post.get('gender')
        rx = request.env['lab.case.request'].sudo().create({
            'partner_id': self._rx_partner().id,
            'patient': patient,
            'age': int(post['age']) if (post.get('age') or '').isdigit() else 0,
            'gender': gender if gender in ('male', 'female') else False,
            'modification': post.get('modification') or False,
            'instruction': post.get('instruction') or False,
            'note': post.get('note') or False,
            'line_ids': lines,
        })
        self._rx_attach_scans(rx, request.httprequest.files.getlist('scan'))
        return request.redirect('/my/rx')

    def _rx_lines(self, env, product_ids, arches, qtys):
        """The submitted rows as line commands, or an error code for the form.

        Form input is outside input: a product id that is not a number, not a product,
        or not something the lab sells, and an arch that is not one of the three, used
        to reach the ORM and come back as a 500. They come back as the form, saying what
        was wrong. Returns ``(lines, error)``.
        """
        Line = env['lab.case.request.line']
        arch_values = {value for value, _label in Line._fields['ul']._description_selection(env)}
        rows = []
        for index, pid in enumerate(product_ids):
            pid = (pid or '').strip()
            if not pid:
                continue
            if not pid.isdigit():
                return [], 'product'
            arch = arches[index] if index < len(arches) and arches[index] else 'upper'
            if arch not in arch_values:
                return [], 'arch'
            try:
                qty = float(qtys[index]) if index < len(qtys) else 1.0
            except (TypeError, ValueError):
                qty = 1.0
            if not math.isfinite(qty):
                qty = 1.0
            rows.append((int(pid), arch, max(qty, 1.0)))
        if not rows:
            return [], '1'
        # sudo: a portal user cannot read products; the form offers exactly these.
        sellable = env['product.product'].sudo().search(
            [('id', 'in', [row[0] for row in rows]), ('sale_ok', '=', True)])
        if set(row[0] for row in rows) - set(sellable.ids):
            return [], 'product'
        return [(0, 0, {'product_id': product_id, 'ul': arch, 'quantity': qty})
                for product_id, arch, qty in rows], None

    def _rx_attach_scans(self, rx, uploads):
        """Scans / STL files, stored against the request AND listed on it.

        Setting res_model/res_id alone left them out of the request's Scans / Files,
        so nobody at the lab saw them and conversion had nothing to carry to the order.
        """
        attachments = rx.env['ir.attachment'].sudo()
        ids = []
        for upload in uploads:
            if not upload or not upload.filename:
                continue
            ids.append(attachments.create({
                'name': upload.filename,
                'datas': base64.b64encode(upload.read()),
                'res_model': 'lab.case.request',
                'res_id': rx.id,
            }).id)
        if ids:
            rx.sudo().write({'attachment_ids': [(4, att_id) for att_id in ids]})
        return ids
