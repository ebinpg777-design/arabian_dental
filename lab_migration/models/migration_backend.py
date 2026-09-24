# -*- coding: utf-8 -*-
import copy
import logging
import re
from collections import defaultdict

import psycopg2
import psycopg2.extras

from odoo import fields, models, _
from odoo.exceptions import AccessError, UserError

from .migration_spec import (ENTITY_SPECS, DISTRICT_ALIASES, FISCAL_POSITION_ALIASES,
                             UOM_ALIASES, UOM_CATEGORY_REFERENCE)

_logger = logging.getLogger(__name__)


class MigrationBackend(models.Model):
    _name = 'migration.backend'
    _description = 'Odoo 17 Migration Backend'

    name = fields.Char(required=True, default='Odoo 17 Source')
    db_host = fields.Char('Host / URL', required=True, default='127.0.0.1')
    db_port = fields.Integer('Port', required=True, default=5432)
    db_name = fields.Char('Database', required=True, default='adl_prod_v17')
    db_user = fields.Char('DB User', required=True, default='odoo')
    src_filestore = fields.Char(
        'Source Filestore',
        help="Path, on THIS server, of the source database's filestore directory "
             "(the folder holding the two-letter hash directories). Attachments "
             "and images are copied from it; leave empty to skip them.")
    # Only the people who may run a sync may see where it connects: the read-only
    # group exists so a failed run can be diagnosed, not to hand out the source
    # database's credentials.
    db_password = fields.Char(
        'DB Password', groups='lab_migration.group_lab_migration_manager')
    opening_date = fields.Date(
        'Opening Balance Date', required=True, default=fields.Date.context_today,
        help="Accounting and inventory opening balances are computed as of this date.")
    opening_move_date = fields.Date(
        'Opening Entry Date',
        help="Date to post the opening entry on. Leave empty to use the Opening "
             "Balance Date.\n\n"
             "Set both when documents exist ON the cut-over day: the Indian FY "
             "convention is an entry dated 1-Apr carrying the closing balances of "
             "31-Mar, so the 1-Apr documents themselves can still be migrated as "
             "documents instead of being absorbed into the opening.")
    opening_open_item_years = fields.Integer(
        'Itemise Open Items (years)', default=3,
        help="How far back the receivable/payable opening keeps ONE LINE PER "
             "UNSETTLED DOCUMENT, so a payment arriving after go-live can be "
             "reconciled against the invoice it actually settles.\n\n"
             "Anything older than this comes across as a single carry-forward line "
             "per partner, so every partner's opening total is unchanged either "
             "way — only the level of detail differs.\n\n"
             "0 = itemise the whole history. Odoo 17 was barely reconciled, so that "
             "is roughly 327,000 lines going back to 2017.")
    state = fields.Selection(
        [('draft', 'Not connected'), ('ok', 'Connected')], default='draft')
    log = fields.Text('Last Run Log', readonly=True)

    # ------------------------------------------------------------------ connection
    def _connect(self):
        self.ensure_one()
        try:
            return psycopg2.connect(
                host=self.db_host, port=self.db_port, dbname=self.db_name,
                user=self.db_user, password=self.db_password or None,
                connect_timeout=10)
        except Exception as e:
            raise UserError(_("Cannot connect to the Odoo 17 database:\n%s") % e)

    _MIGRATION_MANAGER_GROUP = 'lab_migration.group_lab_migration_manager'

    def _check_migration_manager(self):
        """Refuse anyone outside "Migration: Run Sync".

        Every action below switches to superuser before it touches data, so the
        model ACL (read-only for the viewer group) never gets a say: without this
        check a read-only user could press a button through RPC and start a full
        sync that deletes and re-posts the opening entries. Already-elevated code
        (the action re-entering ``_migration_env``, a shell as ``__system__``) is
        trusted; an RPC call can never arrive with superuser rights.
        """
        if self.env.su or self.env.user._is_superuser() \
                or self.env.user.has_group(self._MIGRATION_MANAGER_GROUP):
            return
        raise AccessError(_("Only members of \"Migration: Run Sync\" can connect to "
                            "the Odoo 17 database or run the migration."))

    def action_test_connection(self):
        self.ensure_one()
        self._check_migration_manager()
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT latest_version FROM ir_module_module WHERE name='base'")
            ver = cur.fetchone()[0]
        finally:
            conn.close()
        self.state = 'ok'
        return self._notify(_("Connected. Source Odoo version: %s") % ver)

    def _notify(self, message, sticky=False):
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'message': message, 'sticky': sticky, 'type': 'success'},
        }

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _coerce(value):
        # Odoo 16+ stores every translated field as jsonb ({'en_US': ..., 'en_IN':
        # ...}); psycopg2 hands it over as a dict, and writing a dict into a Char
        # raises. The English value is the one every screen showed.
        if isinstance(value, dict):
            return value.get('en_US') or next((v for v in value.values() if v), None)
        return bytes(value) if isinstance(value, memoryview) else value

    @staticmethod
    def _valid(Model, vals):
        """Drop keys that aren't real fields of the target model (handles v17→v19
        field drift, e.g. res.partner.mobile removed)."""
        return {k: v for k, v in vals.items() if k in Model._fields}

    def _fetch(self, cur, table, where=None, src_sql=None):
        # src_sql lets a spec read from a JOIN instead of a plain table — needed
        # where v17 split a record across tables that v19 merged (mrp.workcenter
        # _inherits resource.resource in v17; v19 has name/code/company_id on the
        # model itself).
        q = src_sql or ('SELECT * FROM "%s"' % table)
        if where:
            q += ' WHERE ' + where
        try:
            cur.execute(q)
            # unwrap jsonb translations / memoryviews once, for every reader
            return [{k: self._coerce(v) for k, v in row.items()} for row in cur.fetchall()]
        except psycopg2.Error:
            return None

    def _company_val(self, spec, row, cache):
        """Set the target record's company from its Odoo 17 company_id (all
        companies are synced). Accounts share across companies via company_ids;
        other company-specific models use company_id."""
        field = spec.get('company_field')
        if not field:
            return {}
        cid = self._resolve(cache, 'res.company', row.get('company_id')) or self.env.company.id
        if field == 'company_ids':
            return {'company_ids': [(4, cid)]}  # ADD (share), never remove
        return {field: cid}

    def _put(self, cache, model, o10_id, o19_id):
        """Record an Odoo17->Odoo19 mapping (persisted in migration.map, so it
        survives across runs and supports many v17 ids -> one v19 id)."""
        cache.setdefault(model, {})[o10_id] = o19_id
        Map = self.env['migration.map'].sudo()
        rec = Map.search([('dst_model', '=', model), ('src_id', '=', o10_id)], limit=1)
        if rec:
            if rec.dst_id != o19_id:
                rec.dst_id = o19_id
        else:
            Map.create({'dst_model': model, 'src_id': o10_id, 'dst_id': o19_id})

    def _resolve(self, cache, model, o10_id):
        """Odoo-17 id -> current Odoo-19 id via migration.map (cached), falling back
        to the record's x_src_id for records tagged before the map existed."""
        if not o10_id:
            return False
        mcache = cache.setdefault(model, {})
        if o10_id in mcache:
            return mcache[o10_id]
        rec = self.env['migration.map'].sudo().search(
            [('dst_model', '=', model), ('src_id', '=', o10_id)], limit=1)
        rid = rec.dst_id or False
        if not rid and 'x_src_id' in self.env[model]._fields:
            legacy = self.env[model].sudo().with_context(active_test=False).search(
                [('x_src_id', '=', o10_id)], limit=1)
            if legacy:
                rid = legacy.id
                self._put(cache, model, o10_id, rid)  # heal the map
        mcache[o10_id] = rid
        return rid

    def _main_company_id(self, cur):
        try:
            cur.execute("SELECT min(id) AS m FROM res_company")
            row = cur.fetchone()
            return (row['m'] if row else 1) or 1
        except psycopg2.Error:
            return 1

    def _load_m2m(self, cur, spec):
        out = {}
        for dst_field, (rel, this_col, other_col, other_model) in spec.get('m2m', {}).items():
            data = {}
            try:
                cur.execute('SELECT "%s","%s" FROM "%s"' % (this_col, other_col, rel))
                # Index by column NAME: the cursor is a RealDictCursor, so a row is
                # a dict and `for a, b in rows` would unpack its keys instead of its
                # values — leaving every m2m silently empty. That is how 2530
                # products ended up with the l10n_in default tax rather than the
                # 139 product/1061 supplier tax links the source actually has.
                for r in cur.fetchall():
                    data.setdefault(r[this_col], []).append(r[other_col])
            except psycopg2.Error:
                pass
            out[dst_field] = (data, other_model)
        return out

    def _relation_vals(self, spec, row, cache, m2m_data):
        """Resolve m2o + m2m to current v19 ids (best effort; unresolved skipped)."""
        vals = {}
        for dst_field, (src_col, other_model) in spec.get('m2o', {}).items():
            target = self._resolve(cache, other_model, row.get(src_col))
            if target:
                vals[dst_field] = target
        for dst_field, (data, other_model) in m2m_data.items():
            ids = [i for i in (self._resolve(cache, other_model, o)
                               for o in data.get(row['id'], [])) if i]
            if ids:
                vals[dst_field] = [(6, 0, ids)]
        return vals

    # ------------------------------------------------------------------ pass 1
    def _sync_pass1(self, cur, spec, cache, stats):
        if spec['key'] == 'company':
            return self._sync_companies(cur, spec, cache, stats)
        if spec['key'] == 'product_product':
            return self._sync_products(cur, spec, cache, stats)
        rows = self._fetch(cur, spec['src'], where=spec.get('where'),
                           src_sql=spec.get('src_sql'))  # ALL companies' rows
        if rows is None:
            stats.append("%-22s SKIPPED (no source table '%s')" % (spec['name'], spec['src']))
            return
        Model = self.env[spec['dst']].with_context(active_test=False)
        scalars, static = spec.get('scalars', {}), spec.get('static', {})
        match = spec.get('match', [])
        can_create = spec.get('create', True)
        hook = getattr(self, '_hook_%s' % spec['key'], None)
        m2m_data = self._load_m2m(cur, spec)
        c = {'created': 0, 'updated': 0, 'adopted': 0, 'skipped': 0, 'failed': 0}
        for row in rows:
            try:
                with self.env.cr.savepoint():
                    vals = {dst: self._coerce(row.get(src)) for dst, src in scalars.items()}
                    vals.update(static)
                    company_val = self._valid(Model, self._company_val(spec, row, cache))
                    # Models sharing via company_ids (account.account) are
                    # company-DEPENDENT: v19 keeps the account code in `code_store`,
                    # keyed by the active company. Create/search/write them with the
                    # target company in context, or the code lands on the wrong
                    # company and _check_account_code raises "The code must be set
                    # for every company to which this account belongs."
                    # Always create/write with the record's OWN company active.
                    # env.company follows allowed_company_ids order (Aligners first
                    # here), so any check_company=True default resolves against the
                    # WRONG company otherwise -> "company inconsistencies"
                    # (mrp.workcenter.resource_calendar_id) or a code written into
                    # the wrong company_dependent slot (account.account.code_store).
                    CModel, tgt_cid = Model, None
                    if spec.get('company_field') == 'company_ids' and company_val.get('company_ids'):
                        tgt_cid = company_val['company_ids'][0][1]
                    elif spec.get('company_field') == 'company_id' and company_val.get('company_id'):
                        tgt_cid = company_val['company_id']
                    if tgt_cid:
                        CModel = Model.with_company(tgt_cid)
                    # 'normalize' runs BEFORE matching: a hook that cleans a name
                    # or a code here decides what the record is matched on. Doing it
                    # after the match (the 'scalars' phase) created a second
                    # "THRISSUR" tag for "TRISSUR" and a second "A3" shade for "A3,".
                    if hook:
                        hook(row, vals, 'normalize', None, cache)
                    rid = self._resolve(cache, spec['dst'], row['id'])
                    rec = CModel.browse(rid) if rid else CModel
                    found = 'map' if rec else None
                    if not rec and match:
                        dom = [(f, '=', vals[f]) for f in match if vals.get(f) not in (None, False)]
                        # company-specific models must match within the SAME company
                        if spec.get('company_field') == 'company_id' and company_val.get('company_id'):
                            dom.append(('company_id', '=', company_val['company_id']))
                        elif tgt_cid:
                            # NEVER adopt another company's account. Account codes are
                            # per-company in v19, so the same code under a different
                            # company is a DIFFERENT account; adopting it and then
                            # adding our company to company_ids breaks the code
                            # constraint outright for asset_cash ("Bank & Cash accounts
                            # cannot be shared between companies") and silently drops
                            # the balance from the opening entry.
                            dom.append(('company_ids', 'in', tgt_cid))
                        if dom:
                            rec = CModel.search(dom, limit=1)
                            found = 'match' if rec else None
                    is_new = not rec
                    if hook:
                        hook(row, vals, 'scalars', is_new, cache)
                    vals.update(self._relation_vals(spec, row, cache, m2m_data))
                    # skip orphans: records whose REQUIRED relation didn't resolve
                    if any(not vals.get(f) for f in spec.get('require', [])):
                        c['skipped'] += 1
                        continue
                    vals = self._valid(Model, vals)
                    vals.update(company_val)
                    vals['x_src_id'] = row['id']
                    if rec and found == 'match':
                        # Adopt an existing built-in: tag it, and (accounts only) share
                        # the company via company_ids. Never reassign a single company_id.
                        tag = {'x_src_id': row['id']}
                        if 'company_ids' in company_val:
                            tag['company_ids'] = company_val['company_ids']
                        rec.write(tag)
                        c['adopted'] += 1
                    elif rec:
                        rec.write(vals)  # re-sync of a previously-imported record
                        c['updated'] += 1
                    elif can_create:
                        rec = CModel.create(vals)
                        c['created'] += 1
                    else:
                        c['skipped'] += 1
                        continue
                    self._put(cache, spec['dst'], row['id'], rec.id)
            except Exception as e:
                c['failed'] += 1
                _logger.warning("pass1 %s id=%s: %s", spec['dst'], row.get('id'), e)
        stats.append("%-22s created=%d updated=%d adopted=%d skipped=%d failed=%d"
                     % (spec['name'], c['created'], c['updated'], c['adopted'],
                        c['skipped'], c['failed']))

    # ------------------------------------------------------------------ pass 2
    def _sync_pass2(self, cur, spec, cache, stats):
        if spec['key'] == 'company':
            return self._sync_company_rel(cur, cache, stats)
        if spec['key'] == 'product_product':
            return
        if not spec.get('m2o') and not spec.get('m2m'):
            return
        rows = self._fetch(cur, spec['src'], where=spec.get('where'),
                           src_sql=spec.get('src_sql'))
        if rows is None:
            return
        Model = self.env[spec['dst']].with_context(active_test=False)
        hook = getattr(self, '_hook_%s' % spec['key'], None)
        m2m_data = self._load_m2m(cur, spec)
        errors = 0
        for row in rows:  # noqa: pass2 uses the same scoped rows as pass1
            rec_id = self._resolve(cache, spec['dst'], row['id'])
            if not rec_id:
                continue
            vals = self._relation_vals(spec, row, cache, m2m_data)
            if hook:
                hook(row, vals, 'relations', False, cache)
            vals = self._valid(Model, vals)
            if vals:
                try:
                    with self.env.cr.savepoint():
                        Model.browse(rec_id).write(vals)
                except Exception as e:
                    errors += 1
                    _logger.warning("pass2 %s id=%s: %s", spec['dst'], row['id'], e)
        if errors:
            stats.append("%-22s relation errors=%d" % (spec['name'], errors))

    # ------------------------------------------------------------------ special entities
    def _sync_companies(self, cur, spec, cache, stats):
        """Sync ALL Odoo 17 companies: the lowest-id one maps onto this database's
        existing company; each additional one is CREATED (with an l10n_in chart of
        accounts, like the main company)."""
        rows = sorted(self._fetch(cur, spec['src']) or [], key=lambda r: r['id'])
        if not rows:
            stats.append("Companies              SKIPPED")
            return
        india = self.env.ref('base.in', raise_if_not_found=False)
        created = mapped = 0
        for i, comp in enumerate(rows):
            try:
                with self.env.cr.savepoint():
                    existing = self._resolve(cache, 'res.company', comp['id'])
                    if existing:
                        company = self.env['res.company'].browse(existing)  # idempotent re-sync
                    elif i == 0:
                        company = self.env.company
                    else:
                        cvals = {'name': comp.get('name') or 'Company %d' % comp['id']}
                        if india:
                            cvals['country_id'] = india.id
                        company = self.env['res.company'].create(cvals)
                        self.env.user.sudo().write({'company_ids': [(4, company.id)]})
                        try:
                            self.env['account.chart.template'].sudo().try_loading(
                                'in', company, install_demo=False)
                        except Exception as e:
                            _logger.warning("CoA load for company %s: %s", company.name, e)
                        created += 1
                    # every non-main company needs its own warehouse (opening inventory)
                    if company != self.env.company and not self.env['stock.warehouse'].search(
                            [('company_id', '=', company.id)], limit=1):
                        try:
                            self.env['stock.warehouse'].create({
                                'name': company.name, 'code': 'WH%d' % company.id,
                                'company_id': company.id})
                        except Exception as e:
                            _logger.warning("warehouse for %s: %s", company.name, e)
                    self._write_company(cur, comp, company)
                    self._put(cache, 'res.company', comp['id'], company.id)
                    mapped += 1
            except Exception as e:
                _logger.warning("company %s: %s", comp.get('name'), e)
                stats.append("Company FAILED %s: %s" % (comp.get('name'), str(e)[:90]))
        stats.append("Companies              mapped=%d (new v19 companies created=%d)"
                     % (mapped, created))

    def _write_company(self, cur, comp, company):
        """Write name / address / contact / statutory / custom fields onto a v19 company."""
        vals = {}
        if comp.get('name'):
            vals['name'] = comp['name']
        for f in ('phone', 'email', 'mobile', 'company_registry', 'l10n_in_upi_id',
                  'report_header', 'report_footer', 'company_details',
                  'invoice_terms', 'fiscalyear_last_day', 'fiscalyear_last_month',
                  # inventory & purchase policy
                  'security_lead', 'po_lead', 'days_to_purchase', 'annual_inventory_day',
                  'annual_inventory_month', 'po_double_validation', 'po_lock',
                  'po_double_validation_amount'):
            if comp.get(f) is not None:
                vals[f] = self._coerce(comp[f])
        if comp.get('partner_id'):
            cur.execute('SELECT street, street2, city, zip, vat, website, phone, email, '
                        'mobile, l10n_in_pan, state_id '
                        'FROM res_partner WHERE id = %s', (comp['partner_id'],))
            prow = cur.fetchone() or {}
            for f in ('street', 'street2', 'city', 'zip', 'vat', 'website'):
                if prow.get(f):
                    vals[f] = prow[f]
            for f in ('phone', 'email', 'mobile'):
                if prow.get(f) and not vals.get(f):
                    vals[f] = prow[f]
            # the suite's own statutory fields, from l10n_in's
            if prow.get('vat'):
                vals['gst_number'] = prow['vat']
            if prow.get('l10n_in_pan'):
                vals['pan_number'] = prow['l10n_in_pan']
            if prow.get('state_id'):
                cur.execute('SELECT code, country_id FROM res_country_state WHERE id = %s',
                            (prow['state_id'],))
                srow = cur.fetchone()
                if srow:
                    state = self.env['res.country.state'].search(
                        [('code', '=', srow['code']),
                         ('country_id.code', '=', 'IN')], limit=1)
                    if state:
                        vals['state_id'] = state.id
        vals['x_src_id'] = comp['id']
        company.write(self._valid(company, vals))
        # The source keeps the company's bank details as plain text on the company
        # (sales_invoice_print). v19 prints bank details from res.partner.bank, so
        # they are turned into one, on the company's own partner, in _sync_banks.

    def _sync_company_rel(self, cur, cache, stats):
        """Per-company relations resolved after products exist (emergency product)."""
        for comp in self._fetch(cur, 'res_company') or []:
            cid = self._resolve(cache, 'res.company', comp['id'])
            prod = self._resolve(cache, 'product.product', comp.get('emergency_service_id'))
            if cid and prod:
                try:
                    with self.env.cr.savepoint():
                        self.env['res.company'].browse(cid).write({'emergency_service_id': prod})
                except Exception as e:
                    _logger.warning("company emergency product: %s", e)

    def _sync_products(self, cur, spec, cache, stats):
        """Adopt each template's variant instead of creating new product.product."""
        rows = self._fetch(cur, spec['src'])
        if rows is None:
            stats.append("%-22s SKIPPED" % spec['name'])
            return
        Product = self.env['product.product'].with_context(active_test=False)
        adopted = missing = 0
        for row in rows:
            tmpl_id = self._resolve(cache, 'product.template', row.get('product_tmpl_id'))
            if not tmpl_id:
                missing += 1
                continue
            tmpl = self.env['product.template'].with_context(active_test=False).browse(tmpl_id)
            variant = Product.search([('x_src_id', '=', row['id'])], limit=1)
            if not variant:
                variants = tmpl.with_context(active_test=False).product_variant_ids
                variant = variants.filtered(lambda v: v.default_code == row.get('default_code'))[:1] \
                    or variants[:1]
            if not variant:
                missing += 1
                continue
            wvals = {'x_src_id': row['id']}
            if row.get('barcode'):
                wvals['barcode'] = row['barcode']
            try:
                with self.env.cr.savepoint():
                    variant.write(wvals)
                self._put(cache, 'product.product', row['id'], variant.id)
                adopted += 1
            except Exception as e:
                missing += 1
                _logger.warning("variant adopt id=%s: %s", row['id'], e)
        stats.append("%-22s adopted=%d missing=%d" % (spec['name'], adopted, missing))

    # ------------------------------------------------------------------ product routes
    def _sync_product_routes(self, cur, cache, stats):
        """Replicate each v17 product's Manufacture / Make-To-Order / Buy routes.

        In Odoo 17 (as in 19) a Sale Order regenerates a Manufacturing Order only
        when the product carries the *Manufacture* route (to resolve a procurement
        into an MO) together with *Replenish on Order (MTO)* (so confirming the SO
        immediately triggers that procurement).  The master-data sync copies the
        products and their BoMs but not ``product.template.route_ids`` -- so this
        step maps the v17 route links (``stock_route_product.product_id`` is the
        product *template* id) onto the equivalent built-in v19 routes.
        """
        manufacture = self.env.ref('mrp.route_warehouse0_manufacture', raise_if_not_found=False)
        mto = self.env.ref('stock.route_warehouse0_mto', raise_if_not_found=False)
        buy = self.env.ref('purchase_stock.route_warehouse0_buy', raise_if_not_found=False)
        # The MTO route ships archived; activate it so its pull rules fire, and make
        # both routes product-selectable so they stay visible/toggleable on the form.
        if mto and not mto.active:
            mto.sudo().write({'active': True})
        for r in (manufacture, mto):
            if r and not r.product_selectable:
                r.sudo().write({'product_selectable': True})

        try:
            cur.execute("""
                SELECT sp.product_id AS tmpl, r.name AS rname
                FROM stock_route_product sp
                JOIN stock_route r ON r.id = sp.route_id
            """)
            links = cur.fetchall()
        except psycopg2.Error as e:
            stats.append("%-22s SKIPPED (%s)" % ("Product Routes", e))
            return

        wanted = {}  # v17 template id -> set(v19 route ids)
        for row in links:
            rname = (self._coerce(row['rname']) or '').lower()
            if 'manufacture' in rname:
                tgt = manufacture
            elif 'make to order' in rname or 'mto' in rname:
                tgt = mto
            elif 'buy' in rname:
                tgt = buy
            else:
                tgt = None  # receipt/ship/cross-dock rules are warehouse-defined in v19
            if tgt:
                wanted.setdefault(row['tmpl'], set()).add(tgt.id)

        Template = self.env['product.template'].with_context(active_test=False)
        applied = manufactured = skipped = 0
        for v10_tmpl, rids in wanted.items():
            tmpl_id = self._resolve(cache, 'product.template', v10_tmpl)
            tmpl = Template.browse(tmpl_id).exists() if tmpl_id else None
            if not tmpl:
                skipped += 1
                continue
            add = [rid for rid in rids if rid not in tmpl.route_ids.ids]
            if not add:
                continue
            try:
                with self.env.cr.savepoint():
                    tmpl.write({'route_ids': [(4, rid) for rid in add]})
                applied += 1
                if manufacture and manufacture.id in rids:
                    manufactured += 1
            except Exception as e:
                skipped += 1
                _logger.warning("route apply tmpl=%s: %s", v10_tmpl, e)
        stats.append("%-22s applied=%d (manufacture=%d) skipped=%d"
                     % ("Product Routes", applied, manufactured, skipped))

    # ------------------------------------------------------------------ invoice / bill numbering
    _SEQ_ANCHOR_REF = 'Odoo 17 numbering anchor - do not delete'

    def _sync_bom_operations(self, cur, cache, stats):
        """v17 mrp.routing -> v19 BoM operations.

        v19 REMOVED mrp.routing: operations hang off the BoM (mrp.routing.workcenter
        .bom_id). A v17 routing is shared by many BoMs (routing 5 -> 108 BoMs here),
        so one v17 routing line has to become one operation PER BoM that used it.
        Idempotency key is therefore (bom_id, x_src_id), not x_src_id alone.
        """
        Op = self.env['mrp.routing.workcenter'].with_context(active_test=False)
        rows = self._fetch(cur, 'mrp_routing_workcenter')
        if rows is None:
            stats.append("BoM Operations         SKIPPED (no mrp_routing_workcenter)")
            return
        ops_by_routing = {}
        for r in sorted(rows, key=lambda r: (r.get('sequence') or 0, r['id'])):
            ops_by_routing.setdefault(r['routing_id'], []).append(r)

        boms = self._fetch(cur, 'mrp_bom', where='routing_id IS NOT NULL') or []
        c = {'created': 0, 'updated': 0, 'skipped': 0, 'failed': 0}
        for b in boms:
            v19_bom = self._resolve(cache, 'mrp.bom', b['id'])
            if not v19_bom:
                c['skipped'] += 1
                continue
            for op in ops_by_routing.get(b['routing_id'], []):
                wc = self._resolve(cache, 'mrp.workcenter', op['workcenter_id'])
                if not wc:
                    c['skipped'] += 1
                    continue
                vals = {
                    'name': op.get('name') or 'Operation',
                    'bom_id': v19_bom, 'workcenter_id': wc,
                    'sequence': op.get('sequence') or 0,
                    'time_mode': op.get('time_mode') or 'manual',
                    'time_mode_batch': op.get('time_mode_batch') or 10,
                    'time_cycle_manual': op.get('time_cycle_manual') or 0.0,
                    'x_src_id': op['id'],
                }
                try:
                    with self.env.cr.savepoint():
                        rec = Op.search([('bom_id', '=', v19_bom),
                                         ('x_src_id', '=', op['id'])], limit=1)
                        if rec:
                            rec.write(self._valid(Op, vals))
                            c['updated'] += 1
                        else:
                            Op.create(self._valid(Op, vals))
                            c['created'] += 1
                except Exception as e:
                    c['failed'] += 1
                    _logger.warning("bom operation bom=%s op=%s: %s", b['id'], op['id'], e)
        stats.append("BoM Operations         created=%d updated=%d skipped=%d failed=%d"
                     % (c['created'], c['updated'], c['skipped'], c['failed']))

    def _seed_journal_number(self, journal, number, seed_date):
        """Post one zero-amount 'anchor' entry named ``number`` in ``journal`` so
        the NEXT invoice/bill continues from it. v19 derives the next number from
        the last move's name in the journal (no ir.sequence); a fixed number like
        ``OC213668`` yields ``OC213669`` and a yearly one like ``BILL/2026/0007``
        yields ``BILL/2026/0008`` (and resets per CALENDAR year, as v19 does)."""
        company = journal.company_id
        Move = self.env['account.move'].with_company(company).with_context(
            allowed_company_ids=(company | self.env.company).ids, active_test=False)
        if Move.search_count([('journal_id', '=', journal.id), ('name', '=', number)]):
            return 'exists'
        acc = journal.default_account_id or self.env['account.account'].with_context(
            active_test=False).search([('company_ids', 'in', company.id)], limit=1)
        if not acc:
            return 'skip'
        # Use the source invoice's own date: for a yearly number its CALENDAR year
        # must equal the name's year (v19 constraint); fixed numbers ignore date.
        d = seed_date or self.opening_date or fields.Date.context_today(self)
        try:
            with self.env.cr.savepoint():
                move = Move.create({
                    'move_type': 'entry', 'journal_id': journal.id, 'date': d,
                    'name': number, 'ref': self._SEQ_ANCHOR_REF,
                    'line_ids': [(0, 0, {'account_id': acc.id, 'debit': 0.0, 'credit': 0.0}),
                                 (0, 0, {'account_id': acc.id, 'debit': 0.0, 'credit': 0.0})],
                })
                move.action_post()
            return 'seeded'
        except Exception as e:
            _logger.warning("numbering anchor %s / %s: %s", journal.display_name, number, e)
            return 'skip'

    def _make_journal_default(self, journal):
        """Make `journal` the DEFAULT for its (company, type) by giving it the
        lowest `sequence` (account.journal._order = 'sequence, type, code'), so new
        invoices/bills use the Odoo-17 journal and therefore continue its numbering.
        Without this, new customer invoices would land on the l10n_in 'Sales'
        journal (INV/<fy>/xxxx) instead of the migrated 'Customer Invoices' (OC...)."""
        sibs = self.env['account.journal'].with_context(active_test=False).search(
            [('company_id', '=', journal.company_id.id), ('type', '=', journal.type),
             ('id', '!=', journal.id)])
        if sibs:
            low = min(sibs.mapped('sequence'))
            if journal.sequence >= low:
                journal.sequence = low - 1

    def _seed_invoice_numbers(self, cur, cache):
        """Continue Odoo 17 customer-invoice and vendor-bill numbering in Odoo 19
        by anchoring each journal with the highest Odoo 17 number it used, and
        making that journal the default so new documents use it."""
        # Aggregate in the database, not in Python. This needs one row per journal
        # — the highest number it ever issued — and `SELECT *` over every invoice
        # ever raised is hundreds of thousands of wide rows: enough to exhaust a
        # small server's memory before the migration has migrated anything.
        # COLLATE "C" so the "highest" number is chosen by codepoint, the same
        # ordering the rest of this module compares document numbers with.
        try:
            cur.execute("""
                SELECT DISTINCT ON (company_id, journal_id)
                       company_id, journal_id, name AS number, invoice_date AS date_invoice
                  FROM account_move
                 WHERE move_type IN ('out_invoice', 'in_invoice')
                   AND name IS NOT NULL AND name <> '/' AND state = 'posted'
                 ORDER BY company_id, journal_id, name COLLATE "C" DESC
            """)
            rows = cur.fetchall()
        except psycopg2.Error:
            return "Invoice numbering      : SKIPPED (no account_move in source)"
        best = {(r['company_id'], r['journal_id']): (r['number'], r['date_invoice'])
                for r in rows}
        seeded = existing = skipped = 0
        details = []
        for (_c, v10_journal), (number, inv_date) in best.items():
            journal_id = self._resolve(cache, 'account.journal', v10_journal)
            if not journal_id:
                skipped += 1
                continue
            journal = self.env['account.journal'].browse(journal_id)
            res = self._seed_journal_number(journal, number, inv_date)
            self._make_journal_default(journal)  # new invoices/bills use this journal
            if res == 'seeded':
                seeded += 1
                details.append("%s[%s] -> next after %s" % (journal.name, journal.company_id.name, number))
            elif res == 'exists':
                existing += 1
            else:
                skipped += 1
        out = "Invoice numbering      : %d seeded, %d already set, %d skipped" % (seeded, existing, skipped)
        for d in details:
            out += "\n    " + d
        return out

    def _seed_mo_numbers(self, cur, cache):
        """Continue Odoo 17 Manufacturing Order numbering. Unlike invoices, v19
        numbers MOs from each manufacturing operation type's ir.sequence
        (``stock.picking.type.sequence_id.next_by_id()``), NOT the sequence.mixin
        and NOT the ``mrp.production`` code sequence. So we set that sequence's
        prefix + number_next from the last Odoo 17 MO name of the same company."""
        try:
            cur.execute("""
                SELECT DISTINCT ON (company_id) company_id, name
                FROM mrp_production WHERE name IS NOT NULL
                ORDER BY company_id, id DESC
            """)
            rows = cur.fetchall()
        except psycopg2.Error as e:
            return "MO numbering           : SKIPPED (%s)" % e
        seeded = skipped = 0
        details = []
        for r in rows:
            comp_id = self._resolve(cache, 'res.company', r['company_id'])
            m = re.match(r'^(.*?)(\d+)\s*$', r['name'] or '')
            if not comp_id or not m:
                skipped += 1
                continue
            prefix, num = m.group(1), int(m.group(2))
            ptypes = self.env['stock.picking.type'].with_context(active_test=False).search(
                [('code', '=', 'mrp_operation'), ('company_id', '=', comp_id)])
            done = False
            for pt in ptypes:
                seq = pt.sequence_id
                if not seq:
                    continue
                try:
                    with self.env.cr.savepoint():
                        seq.write({'prefix': prefix, 'number_next': num + 1})
                    done = True
                    details.append("%s[%s] -> next %s%0*d"
                                   % (pt.name, pt.company_id.name, prefix, seq.padding, num + 1))
                except Exception as e:
                    _logger.warning("MO seq %s: %s", seq.id, e)
            seeded += 1 if done else 0
            skipped += 0 if done else 1
        out = "MO numbering           : %d company sequence(s) set, %d skipped" % (seeded, skipped)
        for d in details:
            out += "\n    " + d
        return out

    def action_continue_invoice_numbering(self):
        """Dedicated button: continue Odoo 17 invoice/bill AND MO numbering."""
        self = self._migration_env()
        conn = self._connect()
        conn.autocommit = True
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cache = {'_main_company_': self._main_company_id(cur)}
        try:
            msg = self._seed_invoice_numbers(cur, cache)
            msg += "\n" + self._seed_mo_numbers(cur, cache)
        finally:
            conn.close()
        self.log = "INVOICE / BILL / MO NUMBERING\n" + msg
        self.state = 'ok'
        return self._notify(
            _("Invoice, bill and manufacturing numbering continued from Odoo 17. See the log."),
            sticky=True)

    # ------------------------------------------------------------------ users & access rights
    # System / template users that must never be recreated or altered by login.
    _USER_SKIP_LOGINS = ('default', 'public', 'portaltemplate', '__system__', 'root', 'OdooBot')

    def _sync_users(self, cur, cache, stats):
        """Create/adopt every Odoo 17 internal user in Odoo 19 with the SAME
        companies and the SAME access rights. Groups are matched across versions
        by their XML-id (module.name), which is stable for the app groups
        (base/account/stock/mrp/purchase/sales_team/hr/crm). v17 groups whose
        xml-id no longer exists in v19 (renamed feature toggles) are skipped and
        logged. Original passwords are preserved by copying the pbkdf2 hash."""
        # v17 group id -> 'module.name'
        try:
            cur.execute("SELECT res_id AS gid, module||'.'||name AS xmlid "
                        "FROM ir_model_data WHERE model='res.groups'")
            g_xmlid = {r['gid']: r['xmlid'] for r in cur.fetchall()}
            cur.execute("SELECT uid, gid FROM res_groups_users_rel")
            u_groups = {}
            for r in cur.fetchall():
                u_groups.setdefault(r['uid'], []).append(r['gid'])
            cur.execute("SELECT user_id, cid FROM res_company_users_rel")
            u_comps = {}
            for r in cur.fetchall():
                u_comps.setdefault(r['user_id'], []).append(r['cid'])
            cur.execute("""
                SELECT u.id, u.login, u.active, u.company_id, u.password AS password_crypt,
                       u.signature, u.notification_type,
                       p.name, p.email, p.lang, p.tz, p.phone
                FROM res_users u JOIN res_partner p ON p.id = u.partner_id
                WHERE u.share = false AND u.login NOT IN %s
            """, (self._USER_SKIP_LOGINS,))
            rows = cur.fetchall()
        except psycopg2.Error as e:
            stats.append("%-22s SKIPPED (%s)" % ("Users", e))
            return

        Users = self.env['res.users'].with_context(
            active_test=False, no_reset_password=True,
            mail_create_nosubscribe=True, mail_notrack=True)
        base_user = self.env.ref('base.group_user')
        ref_cache, missing = {}, set()

        # v17 group xml-ids that moved module between 17 and 19
        _RENAMED_GROUPS = {
            'product.group_discount_per_so_line': 'sale.group_discount_per_so_line',
        }

        def v19_group(xmlid):
            xmlid = _RENAMED_GROUPS.get(xmlid, xmlid)
            if xmlid not in ref_cache:
                ref_cache[xmlid] = self.env.ref(xmlid, raise_if_not_found=False)
            return ref_cache[xmlid]

        has_x10 = 'x_src_id' in Users._fields
        created = adopted = skipped = 0
        pw_updates = []
        for r in rows:
            login = (r['login'] or '').strip()
            if not login:
                skipped += 1
                continue
            gids = []
            for v10g in u_groups.get(r['id'], []):
                xmlid = g_xmlid.get(v10g)
                if not xmlid:
                    continue
                grp = v19_group(xmlid)
                if grp:
                    gids.append(grp.id)
                else:
                    missing.add(xmlid)
            if base_user.id not in gids:
                gids.append(base_user.id)  # keep them internal users
            gids = list(dict.fromkeys(gids))
            # companies: mapped allowed companies + default company
            comp_ids = [c for c in (self._resolve(cache, 'res.company', c)
                                    for c in u_comps.get(r['id'], [])) if c]
            main_c = self._resolve(cache, 'res.company', r['company_id'])
            if main_c and main_c not in comp_ids:
                comp_ids.append(main_c)
            if not comp_ids:
                main_c = main_c or self.env.company.id
                comp_ids = [main_c]
            is_active = bool(r['active'])
            base_vals = {'name': r['name'] or login}
            for f in ('email', 'signature', 'lang', 'tz', 'phone', 'notification_type'):
                if r.get(f):
                    base_vals[f] = r[f]
            try:
                with self.env.cr.savepoint():
                    user = Users.search([('login', '=', login)], limit=1)
                    if not user and has_x10:
                        user = Users.search([('x_src_id', '=', r['id'])], limit=1)
                    if user:
                        # ADOPT: never strip existing access; only add groups/companies
                        wv = dict(base_vals)
                        wv['group_ids'] = [(4, g) for g in gids]
                        wv['company_ids'] = [(4, c) for c in comp_ids]
                        if has_x10:
                            wv['x_src_id'] = r['id']
                        user.write(self._valid(Users, wv))
                        adopted += 1
                    else:
                        # Always CREATE active: creating with active=False raises
                        # "cannot archive a contact linked to an active user"
                        # (the auto-created partner). Archive the user afterwards
                        # (res.users.active is independent of its partner.active).
                        cv = dict(base_vals)
                        cv['login'] = login
                        cv['active'] = True
                        cv['group_ids'] = [(6, 0, gids)]
                        cv['company_ids'] = [(6, 0, comp_ids)]
                        cv['company_id'] = main_c or comp_ids[0]
                        if has_x10:
                            cv['x_src_id'] = r['id']
                        user = Users.create(self._valid(Users, cv))
                        created += 1
                        if not is_active:
                            user.write({'active': False})
                self._put(cache, 'res.users', r['id'], user.id)
                if r.get('password_crypt'):
                    pw_updates.append((user.id, r['password_crypt']))
            except Exception as e:
                skipped += 1
                _logger.warning("user %s: %s", login, e)
        # Preserve original logins: write the pbkdf2 hash straight into the column
        # (the ORM `password` setter would re-hash it). v19 verifies pbkdf2_sha512.
        for uid, h in pw_updates:
            try:
                self.env.cr.execute("UPDATE res_users SET password=%s WHERE id=%s", (h, uid))
            except Exception as e:
                _logger.warning("user password uid=%s: %s", uid, e)
        if missing:
            _logger.info("users: %d v17 group xml-ids absent in v19 (skipped): %s",
                         len(missing), ', '.join(sorted(missing)))
        stats.append("%-22s created=%d adopted=%d skipped=%d (groups by xml-id; %d v17 groups absent in v19)"
                     % ("Users", created, adopted, skipped, len(missing)))

    # ------------------------------------------------------------------ bank accounts
    def _sync_banks(self, cur, cache, stats):
        """Sync res.bank + res.partner.bank. The company bank account must attach to
        the v19 COMPANY's partner (company.partner_id) so it shows in the invoice
        footer's bank-details table; the generic partner sync would otherwise map the
        v17 company partner to a duplicate res.partner, not company.partner_id."""
        Bank = self.env['res.bank'].with_context(active_test=False)
        n_bank = 0
        for r in self._fetch(cur, 'res_bank') or []:
            if not r.get('name'):
                continue
            vals = {k: self._coerce(r.get(k)) for k in
                    ('name', 'bic', 'street', 'street2', 'city', 'zip', 'phone', 'email')
                    if r.get(k) is not None}
            if r.get('active') is not None:
                vals['active'] = bool(r['active'])
            vals['x_src_id'] = r['id']
            try:
                with self.env.cr.savepoint():
                    rec = Bank.search([('x_src_id', '=', r['id'])], limit=1)
                    if not rec and r.get('bic'):
                        rec = Bank.search([('bic', '=', r['bic'])], limit=1)
                    if not rec:
                        rec = Bank.search([('name', '=', r['name'])], limit=1)
                    if rec:
                        rec.write(vals)
                    else:
                        rec = Bank.create(vals)
                self._put(cache, 'res.bank', r['id'], rec.id)
                n_bank += 1
            except Exception as e:
                _logger.warning("res.bank %s: %s", r.get('name'), e)

        # v17 company-partner id -> v19 company.partner_id
        comp_partner = {}
        for r in self._fetch(cur, 'res_company') or []:
            v19c = self._resolve(cache, 'res.company', r['id'])
            if v19c and r.get('partner_id'):
                comp_partner[r['partner_id']] = self.env['res.company'].browse(v19c).partner_id.id

        PB = self.env['res.partner.bank'].with_context(active_test=False)
        n_pb = skipped = 0
        # The company's own account, kept as text on res_company in the source.
        for r in self._fetch(cur, 'res_company') or []:
            acc = (r.get('acc_number') or '').strip()
            v19c = self._resolve(cache, 'res.company', r['id'])
            if not acc or not v19c:
                continue
            company = self.env['res.company'].browse(v19c)
            try:
                with self.env.cr.savepoint():
                    bank = False
                    if r.get('acc_bank_name'):
                        bank = Bank.search([('name', '=', r['acc_bank_name'])], limit=1) \
                            or Bank.create({'name': r['acc_bank_name'],
                                            'bic': r.get('swift_code') or False,
                                            'street': r.get('bank_branch') or False})
                    pb = PB.search([('acc_number', '=', acc),
                                    ('partner_id', '=', company.partner_id.id)], limit=1)
                    pvals = {'acc_number': acc, 'partner_id': company.partner_id.id,
                             'company_id': company.id}
                    if bank:
                        pvals['bank_id'] = bank.id
                    if pb:
                        pb.write(pvals)
                    else:
                        PB.create(pvals)
                    n_pb += 1
            except Exception as e:
                _logger.warning("company bank account %s: %s", acc, e)
        for r in self._fetch(cur, 'res_partner_bank') or []:
            acc = r.get('acc_number')
            partner_id = comp_partner.get(r.get('partner_id')) \
                or self._resolve(cache, 'res.partner', r.get('partner_id'))
            if not acc or not partner_id:
                skipped += 1
                continue
            vals = {'acc_number': acc, 'partner_id': partner_id, 'x_src_id': r['id']}
            bank_id = self._resolve(cache, 'res.bank', r.get('bank_id'))
            if bank_id:
                vals['bank_id'] = bank_id
            if r.get('sequence') is not None:
                vals['sequence'] = r['sequence']
            try:
                with self.env.cr.savepoint():
                    rec = PB.search([('x_src_id', '=', r['id'])], limit=1) \
                        or PB.search([('acc_number', '=', acc), ('partner_id', '=', partner_id)], limit=1)
                    if rec:
                        rec.write(vals)
                    else:
                        rec = PB.create(vals)
                self._put(cache, 'res.partner.bank', r['id'], rec.id)
                n_pb += 1
            except Exception as e:
                skipped += 1
                _logger.warning("res.partner.bank %s: %s", acc, e)
        stats.append("Bank accounts          : %d banks, %d partner accounts, %d skipped"
                     % (n_bank, n_pb, skipped))

    # ------------------------------------------------------------------ orchestration
    def _migration_env(self):
        """Rebind this record to run the migration as SUPERUSER across ALL companies.

        The sync reads/writes records of every company. If it runs with only the
        company currently selected in the web UI (``allowed_company_ids``), the
        multi-company record rules raise AccessError on the other company's records
        (e.g. the 2400+ Arabian Dental Lab products when only Aligners is selected),
        which aborts the transaction ("cursor already closed"). sudo() bypasses the
        record rules and the all-companies context keeps company-dependent logic
        correct.

        It also marks the whole run as a REPLAY OF HISTORY, which suppresses the
        side effects a first-time document is supposed to have. Posting an invoice
        normally notifies the doctor — migrating four months of invoices must not
        message anybody four months late, and rendering a PDF per invoice to attach
        to that message costs more than the migration itself (it took the invoice
        phase from ~800/min to ~25/min). Chatter is disabled for the same reason:
        nobody wants 20,000 "Invoice created" notifications, and the tracking rows
        cost more storage than the invoices.
        """
        self.ensure_one()
        self._check_migration_manager()
        company_ids = self.env['res.company'].sudo().search([]).ids
        return self.sudo().with_context(
            allowed_company_ids=company_ids,
            migration_replay=True,          # honoured by lab_whatsapp
            tracking_disable=True,
            mail_create_nolog=True,
            mail_notrack=True,
            mail_auto_subscribe_no_notify=True,
        )

    def _run(self, specs):
        self = self._migration_env()
        conn = self._connect()
        conn.autocommit = True
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cache, stats = {}, []
        cache['_main_company_'] = self._main_company_id(cur)
        try:
            # payment-term lines are needed while the terms are being created
            cache['_pt_lines_'] = {}
            for ln in self._fetch(cur, 'account_payment_term_line') or []:
                cache['_pt_lines_'].setdefault(ln['payment_id'], []).append(ln)
            # unit categories, for the reference each created unit is relative to
            cache['_uom_categ_'] = {r['id']: (r.get('name') or '')
                                    for r in self._fetch(cur, 'uom_category') or []}
            # Users first: employees, teams and partners point at them. The user
            # sync is independent of every spec (it matches groups by xml-id).
            self._sync_users(cur, cache, stats)
            for spec in specs:
                self._sync_pass1(cur, spec, cache, stats)
            for spec in specs:
                self._sync_pass2(cur, spec, cache, stats)
            self._sync_doctor_contacts(cur, cache, stats)
            self._sync_partner_properties(cur, cache, stats)
            self._sync_product_costs(cur, cache, stats)
            self._sync_product_routes(cur, cache, stats)
            self._sync_bom_operations(cur, cache, stats)
            self._sync_banks(cur, cache, stats)
        finally:
            conn.close()
        return cache, stats

    # ------------------------------------------------------------------ dental_sale partner fields
    def _sync_doctor_contacts(self, cur, cache, stats):
        """dental_sale kept the doctor as two text fields on the clinic (doctor_name,
        doctor_contact_no). The suite models a doctor as a CONTACT of the clinic —
        it is what the portal login, the WhatsApp desk and the visit screens key
        on — so each named doctor becomes a child contact flagged is_doctor.

        Idempotent by (clinic, doctor name): the child has no source id of its own.
        """
        try:
            cur.execute("""SELECT id, doctor_name, doctor_contact_no FROM res_partner
                            WHERE doctor_name IS NOT NULL AND trim(doctor_name) <> ''""")
            rows = cur.fetchall()
        except psycopg2.Error as e:
            stats.append("Doctor contacts        SKIPPED (%s)" % e)
            return
        Partner = self.env['res.partner'].with_context(active_test=False)
        created = updated = skipped = 0
        for r in rows:
            clinic_id = self._resolve(cache, 'res.partner', r['id'])
            name = ' '.join((r['doctor_name'] or '').split())
            if not clinic_id or not name:
                skipped += 1
                continue
            clinic = Partner.browse(clinic_id)
            if clinic.name.strip().upper() == name.upper():
                # a solo doctor whose clinic record IS the doctor: no child needed
                if not clinic.is_doctor:
                    clinic.write({'is_doctor': True})
                skipped += 1
                continue
            vals = {'name': name, 'parent_id': clinic_id, 'type': 'contact',
                    'is_doctor': True, 'is_clinic': False, 'company_id': False,
                    'customer_rank': 0}
            phone = str(r['doctor_contact_no']) if r.get('doctor_contact_no') else ''
            if phone and phone not in ('0', 'None'):
                vals['phone'] = phone
            try:
                with self.env.cr.savepoint():
                    child = Partner.search([('parent_id', '=', clinic_id),
                                            ('name', '=ilike', name)], limit=1)
                    if child:
                        child.write(self._valid(Partner, vals))
                        updated += 1
                    else:
                        Partner.create(self._valid(Partner, vals))
                        created += 1
            except Exception as e:
                skipped += 1
                _logger.warning("doctor contact for partner %s: %s", r['id'], e)
        stats.append("Doctor contacts        created=%d updated=%d skipped=%d"
                     % (created, updated, skipped))

    def _sync_partner_properties(self, cur, cache, stats):
        """Company-dependent partner settings, which v17 keeps in ir_property and
        v19 as columns: the payment term and the fiscal position (GST: Within
        Kerala / Inter State) each clinic is billed under."""
        try:
            cur.execute("""SELECT name, res_id, value_reference FROM ir_property
                            WHERE res_id LIKE 'res.partner,%%'
                              AND name IN ('property_payment_term_id',
                                           'property_account_position_id',
                                           'property_supplier_payment_term_id')
                              AND value_reference IS NOT NULL""")
            rows = cur.fetchall()
        except psycopg2.Error as e:
            stats.append("Partner properties     SKIPPED (%s)" % e)
            return
        Partner = self.env['res.partner'].with_context(active_test=False)
        models_ = {'property_payment_term_id': 'account.payment.term',
                   'property_supplier_payment_term_id': 'account.payment.term',
                   'property_account_position_id': 'account.fiscal.position'}
        n = skipped = 0
        by_partner = {}
        for r in rows:
            try:
                src_partner = int(r['res_id'].split(',')[1])
                src_value = int(r['value_reference'].split(',')[1])
            except (IndexError, ValueError):
                continue
            partner_id = self._resolve(cache, 'res.partner', src_partner)
            value_id = self._resolve(cache, models_[r['name']], src_value)
            if partner_id and value_id:
                by_partner.setdefault(partner_id, {})[r['name']] = value_id
            else:
                skipped += 1
        for partner_id, vals in by_partner.items():
            try:
                with self.env.cr.savepoint():
                    Partner.browse(partner_id).write(vals)
                n += 1
            except Exception as e:
                skipped += 1
                _logger.warning("partner properties %s: %s", partner_id, e)
        stats.append("Partner properties     partners=%d skipped=%d" % (n, skipped))

    def action_sync_master(self):
        self = self._migration_env()
        cache, stats = self._run(ENTITY_SPECS)
        self.log = "MASTER DATA SYNC\n" + "\n".join(stats)
        self.state = 'ok'
        return self._notify(_("Master data synced. See the log."), sticky=True)

    def action_sync_configuration(self):
        self = self._migration_env()
        stats = self._sync_configuration()
        self.log = "CONFIGURATION SYNC\n" + "\n".join(stats)
        self.state = 'ok'
        return self._notify(_("Configuration synced. See the log."), sticky=True)

    def action_sync_all(self):
        self = self._migration_env()
        cache, stats = self._run(ENTITY_SPECS)
        stats.append("")
        stats += self._sync_configuration()
        stats.append("")
        stats += self._opening_accounting()
        stats += self._opening_inventory()
        stats.append("")
        # continue Odoo 17 invoice/bill numbering (uses the cache from _run)
        conn = self._connect()
        conn.autocommit = True
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            stats.append(self._seed_invoice_numbers(cur, cache))
            stats.append(self._seed_mo_numbers(cur, cache))
            # Odoo stamps create_date/write_date itself and ignores anything passed
            # to create(), so the source values can only be put back at the end of
            # the run. Left to the manual button, every record silently claims to
            # have been created on migration day.
            stats.append("")
            total, ts_stats = self._sync_timestamps(cur)
            stats.append("SOURCE TIMESTAMPS RESTORED")
            stats += ts_stats
            stats.append("  %-24s %7d" % ('TOTAL', total))
        finally:
            conn.close()
        self.log = "FULL SYNC\n" + "\n".join(stats)
        self.state = 'ok'
        return self._notify(_("Full sync + configuration + opening balances done. See the log."), sticky=True)

    # ------------------------------------------------------------------ configuration
    # Technical / DB-specific parameters that must NOT be copied v17 -> v19.
    _PARAM_DENY_PREFIX = ('database.', 'web.base')
    _PARAM_DENY_KEYS = {'report.url'}

    def _sync_configuration(self):
        """Sync system-level configuration: number sequences (continue the v17
        numbering), system parameters (safe subset) and decimal precision."""
        self.ensure_one()
        conn = self._connect()
        conn.autocommit = True
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        main = self._main_company_id(cur)
        out = []
        try:
            # 1) Number sequences — continue the v17 series (prefix/padding/next).
            #    Skip account.* so v19/l10n_in GST-compliant invoice naming is preserved.
            Seq = self.env['ir.sequence'].sudo()
            by_code = {}
            for r in self._fetch(cur, 'ir_sequence') or []:
                code = r.get('code')
                if code and r.get('company_id') in (main, None):
                    by_code.setdefault(code, r)
            n_seq = 0
            for code, r in by_code.items():
                if code.startswith('account.'):
                    continue
                targets = Seq.search([('code', '=', code)])
                if not targets:
                    continue
                number_next = r.get('number_next') or 1
                if r.get('implementation') == 'standard':
                    # number_next is stale on a 'standard' sequence: the live
                    # counter is a PostgreSQL sequence named after the row.
                    try:
                        cur.execute('SELECT last_value, is_called FROM ir_sequence_%03d'
                                    % r['id'])
                        srow = cur.fetchone()
                        if srow:
                            number_next = srow['last_value'] + (1 if srow['is_called'] else 0)
                    except psycopg2.Error:
                        pass
                vals = {'number_next': number_next}
                for f in ('prefix', 'suffix', 'padding', 'number_increment'):
                    if r.get(f) is not None:
                        vals[f] = r[f]
                try:
                    with self.env.cr.savepoint():
                        targets.write(vals)
                    n_seq += len(targets)
                except Exception as e:
                    _logger.warning("sequence %s: %s", code, e)
            out.append("Sequences (numbering)  : %d updated" % n_seq)

            # 2) System parameters — safe business subset.
            ICP = self.env['ir.config_parameter'].sudo()
            n_p = 0
            for r in self._fetch(cur, 'ir_config_parameter') or []:
                key = r.get('key')
                if (not key or key in self._PARAM_DENY_KEYS
                        or any(key.startswith(p) for p in self._PARAM_DENY_PREFIX)):
                    continue
                try:
                    with self.env.cr.savepoint():
                        ICP.set_param(key, r.get('value'))
                    n_p += 1
                except Exception as e:
                    _logger.warning("config param %s: %s", key, e)
            out.append("System parameters      : %d set" % n_p)

            # 3) Decimal precision by name (some were renamed in v19, e.g.
            #    "Product Unit of Measure" -> "Product Unit"; keep quantities at the
            #    v17 precision so line quantities print as "1.000" not "1.00").
            DP = self.env['decimal.precision'].sudo()
            _DP_RENAME = {'Product Unit of Measure': 'Product Unit'}
            n_dp = 0
            for r in self._fetch(cur, 'decimal_precision') or []:
                name = _DP_RENAME.get(r.get('name'), r.get('name'))
                rec = DP.search([('name', '=', name)], limit=1)
                if rec and r.get('digits') is not None:
                    try:
                        with self.env.cr.savepoint():
                            rec.write({'digits': r['digits']})
                        n_dp += 1
                    except Exception as e:
                        _logger.warning("decimal precision %s: %s", r.get('name'), e)
            out.append("Decimal precision      : %d updated" % n_dp)

            # 4) Currencies — activate every currency active in Odoo 17.
            out.append(self._sync_currencies(cur))
            # 5) Company financials — currency, fiscal year, tax policy, lock dates
            #    for EVERY company (not just the one the sync runs in).
            cache = {'_main_company_': main}
            out += self._sync_company_financials(cur, cache)
            # 6) Journals — every v17 journal, per company (adopt the l10n_in
            #    built-ins, create the rest with a unique code + default account).
            out.append(self._sync_journals(cur, cache))
            # 7) Keep only primary journals on the (kanban) dashboard for speed.
            out.append(self._prune_journal_dashboard())
        finally:
            conn.close()
        return out

    # ------------------------------------------------------------------ config: currencies / company / journals
    def _sync_currencies(self, cur):
        """Activate in Odoo 19 every currency that is active in Odoo 17 (matched by
        ISO name). Currency *rates* are copied too when present."""
        Currency = self.env['res.currency'].with_context(active_test=False)
        rows = self._fetch(cur, 'res_currency', where='active = true') or []
        activated = 0
        for r in rows:
            rec = Currency.search([('name', '=', r.get('name'))], limit=1)
            if not rec:
                continue
            vals = {}
            if not rec.active:
                vals['active'] = True
            # Match the v17 display format (Indian INR uses symbol AFTER the amount,
            # e.g. "1,500.00 ₹"; v19 defaults to 'before').
            for f in ('symbol', 'position'):
                if r.get(f) and r.get(f) != rec[f]:
                    vals[f] = r[f]
            if not vals:
                continue
            try:
                with self.env.cr.savepoint():
                    rec.write(vals)
                if 'active' in vals:
                    activated += 1
            except Exception as e:
                _logger.warning("currency %s: %s", r.get('name'), e)
        # rates (v17 has none in this DB, but keep it faithful/idempotent)
        rates = self._fetch(cur, 'res_currency_rate') or []
        cur_name = {c['id']: c.get('name') for c in self._fetch(cur, 'res_currency') or []}
        Rate = self.env['res.currency.rate'].sudo()
        n_rate = 0
        for r in rates:
            rec = Currency.search([('name', '=', cur_name.get(r.get('currency_id')))], limit=1)
            if not rec or not r.get('name'):
                continue
            exists = Rate.search([('currency_id', '=', rec.id), ('name', '=', r['name'])], limit=1)
            if exists:
                continue
            try:
                with self.env.cr.savepoint():
                    Rate.create({'currency_id': rec.id, 'name': r['name'],
                                 'rate': r.get('rate') or 1.0})
                n_rate += 1
            except Exception as e:
                _logger.warning("currency rate %s: %s", r.get('name'), e)
        return "Currencies             : %d activated, %d rates" % (activated, n_rate)

    def _set_company_currency(self, company, currency):
        """Set a company's currency, working around the 'journal items already
        exist' guard by dropping the migration's own opening moves first (they are
        re-posted afterwards by _opening_accounting during a full sync)."""
        if not currency or company.currency_id == currency:
            return
        try:
            with self.env.cr.savepoint():
                company.currency_id = currency.id
            return
        except Exception:
            pass
        moves = self.env['account.move'].sudo().with_context(active_test=False).search(
            [('company_id', '=', company.id), ('ref', '=', self._OPENING_REF)])
        if moves:
            moves.filtered(lambda m: m.state == 'posted').button_draft()
            moves.unlink()
        try:
            with self.env.cr.savepoint():
                company.currency_id = currency.id
        except Exception as e:
            _logger.warning("currency change %s -> %s: %s", company.name, currency.name, e)

    def _sync_company_financials(self, cur, cache):
        """Push Odoo 17 company-level financial settings onto EVERY mapped Odoo 19
        company: currency, fiscal-year end, tax rounding, anglo-saxon, lock dates.
        Unknown-in-v19 fields are dropped by _valid (version drift safe)."""
        Currency = self.env['res.currency'].with_context(active_test=False)
        cur_name = {c['id']: c.get('name') for c in self._fetch(cur, 'res_currency') or []}
        n = 0
        for r in self._fetch(cur, 'res_company') or []:
            comp_id = self._resolve(cache, 'res.company', r['id'])
            if not comp_id:
                continue
            company = self.env['res.company'].browse(comp_id)
            currency = Currency.search([('name', '=', cur_name.get(r.get('currency_id')))], limit=1)
            if currency:
                if not currency.active:
                    currency.active = True
                self._set_company_currency(company, currency)
            vals = {}
            if r.get('fiscalyear_last_day'):
                vals['fiscalyear_last_day'] = int(r['fiscalyear_last_day'])
            if r.get('fiscalyear_last_month'):
                vals['fiscalyear_last_month'] = str(r['fiscalyear_last_month'])
            if r.get('tax_calculation_rounding_method'):
                vals['tax_calculation_rounding_method'] = r['tax_calculation_rounding_method']
            if r.get('anglo_saxon_accounting') is not None:
                vals['anglo_saxon_accounting'] = bool(r['anglo_saxon_accounting'])
            for f in ('fiscalyear_lock_date', 'period_lock_date'):
                if r.get(f):
                    vals[f] = r[f]
            vals = self._valid(company, vals)
            if vals:
                try:
                    with self.env.cr.savepoint():
                        company.write(vals)
                    n += 1
                except Exception as e:
                    _logger.warning("company financials %s: %s", company.name, e)
        return ["Company financials     : %d companies (currency, fiscal year, tax policy)" % n]

    def _unique_journal_code(self, Journal, company_id, base):
        """A journal code (<=5 chars) unique within the company; v17 reused codes
        across journals but v19 enforces code uniqueness per company."""
        base = re.sub(r'\s+', '', (base or 'JRN'))[:5] or 'JRN'
        code, n = base, 1
        while Journal.search_count([('company_id', '=', company_id), ('code', '=', code)]):
            suffix = str(n)
            code = (base[:5 - len(suffix)] or 'J') + suffix
            n += 1
            if n > 99999:
                break
        return code

    def _sync_journals(self, cur, cache):
        """Sync every Odoo 17 journal into its mapped company. Adopt the matching
        l10n_in built-in journal when one exists (by type+name); otherwise create
        it with a unique code and, for sale/purchase/misc, its default account."""
        rows = self._fetch(cur, 'account_journal')
        if rows is None:
            return "Journals               : SKIPPED (no source table)"
        Journal = self.env['account.journal'].with_context(active_test=False)
        _TYPES = {'sale', 'purchase', 'cash', 'bank', 'general'}
        created = adopted = updated = skipped = 0
        for r in rows:
            comp_id = self._resolve(cache, 'res.company', r.get('company_id'))
            if not comp_id:
                skipped += 1
                continue
            jtype = r.get('type') if r.get('type') in _TYPES else 'general'
            name = r.get('name') or 'Journal'
            v10code = (r.get('code') or '').strip()[:5]
            acc = (self._resolve(cache, 'account.account', r.get('default_account_id'))
                   or self._resolve(cache, 'account.account', r.get('default_debit_account_id'))
                   or self._resolve(cache, 'account.account', r.get('default_credit_account_id')))
            can_default = jtype in ('sale', 'purchase', 'general')
            bank_acc = self._resolve(cache, 'res.partner.bank', r.get('bank_account_id'))
            try:
                with self.env.cr.savepoint():
                    rec = Journal.search([('x_src_id', '=', r['id'])], limit=1)
                    if rec:
                        if acc and can_default and not rec.default_account_id:
                            rec.default_account_id = acc
                            updated += 1
                    else:
                        # Same code and type = the same journal (BNK1, BILL, MISC,
                        # STJ, EXCH, CABA are the chart's own on both sides); it
                        # takes the lab's name, since that is what everyone calls it.
                        rec = Journal.search([('company_id', '=', comp_id), ('type', '=', jtype),
                                              ('code', '=', v10code), ('x_src_id', '=', False)],
                                             limit=1) if v10code else Journal
                        if not rec:
                            rec = Journal.search([('company_id', '=', comp_id),
                                                  ('type', '=', jtype), ('name', '=', name)], limit=1)
                        if rec:
                            wv = {'x_src_id': r['id'], 'name': name}
                            if acc and can_default and not rec.default_account_id:
                                wv['default_account_id'] = acc
                            if bank_acc and jtype == 'bank' and not rec.bank_account_id:
                                wv['bank_account_id'] = bank_acc
                            rec.write(wv)
                            adopted += 1
                        else:
                            cvals = {'name': name, 'type': jtype, 'company_id': comp_id,
                                     'code': self._unique_journal_code(Journal, comp_id, v10code or name),
                                     'x_src_id': r['id']}
                            if acc and can_default:
                                cvals['default_account_id'] = acc
                            if bank_acc and jtype == 'bank':
                                cvals['bank_account_id'] = bank_acc
                            if r.get('active') is False:
                                cvals['active'] = False
                            rec = Journal.create(cvals)
                            created += 1
                    self._put(cache, 'account.journal', r['id'], rec.id)
            except Exception as e:
                skipped += 1
                _logger.warning("journal %s (%s) company=%s: %s", name, v10code, comp_id, e)
        return ("Journals               : created=%d adopted=%d updated=%d skipped=%d"
                % (created, adopted, updated, skipped))

    def _prune_journal_dashboard(self):
        """Keep the Accounting dashboard fast. The migration brings dozens of
        per-branch bank/cash books; rendering a kanban card (with a balance graph)
        for every one makes the dashboard slow to load. So we show only the PRIMARY
        journals -- the standard chart-of-accounts journals (those carrying an
        ir.model.data XML-id) plus one main cash journal per company -- and take the
        rest off the dashboard. Every hidden journal stays fully usable; users can
        re-add any of them via its 'Show on Dashboard' toggle."""
        Journal = self.env['account.journal'].with_context(active_test=False)
        journals = Journal.search([])
        if not journals:
            return "Dashboard journals     : none"
        # standard journals created from data files carry an XML-id
        data = self.env['ir.model.data'].sudo().search(
            [('model', '=', 'account.journal'), ('res_id', 'in', journals.ids)])
        keep = set(data.mapped('res_id'))
        # + one main cash journal per company (l10n_in creates no cash journal here)
        for company in journals.mapped('company_id'):
            cash = journals.filtered(lambda j: j.type == 'cash' and j.company_id == company)
            if cash:
                main = (cash.filtered(lambda j: (j.name or '').strip().upper() == 'CASH IN HAND')
                        or cash.filtered(lambda j: 'CASH IN HAND' in (j.name or '').upper())
                        or cash.filtered(lambda j: (j.name or '').strip().lower() == 'cash')
                        or cash.sorted('id'))
                keep.add(main[:1].id)
        on = journals.filtered(lambda j: j.id in keep)
        off = journals - on
        on.filtered(lambda j: not j.show_on_dashboard).write({'show_on_dashboard': True})
        off.filtered(lambda j: j.show_on_dashboard).write({'show_on_dashboard': False})
        return "Dashboard journals     : %d shown, %d hidden (dashboard performance)" % (len(on), len(off))

    # ------------------------------------------------------------------ per-entity hooks
    def _hook_account(self, row, vals, phase, is_new, cache):
        if phase == 'normalize':
            # v19 account codes must be alphanumeric + dots — and the match is on
            # the cleaned code, or the same account is created twice.
            code = vals.get('code') or ''
            clean = re.sub(r'[^A-Za-z0-9.]', '', code)
            if clean:
                vals['code'] = clean
            return
        if phase != 'scalars':
            return
        if is_new:
            at = vals.get('account_type') or 'asset_current'
            vals['reconcile'] = (True if at in ('asset_receivable', 'liability_payable')
                                 else bool(row.get('reconcile')))
        else:
            # On re-sync, never overwrite the account_type/reconcile of an existing
            # (possibly adopted l10n_in) account — that could violate the
            # receivable/payable-must-reconcile constraint.
            vals.pop('account_type', None)
            vals.pop('reconcile', None)

    def _hook_uom(self, row, vals, phase, is_new, cache):
        Uom = self.env['uom.uom'].with_context(active_test=False)
        if phase == 'normalize':
            name = (vals.get('name') or '').strip()
            name = UOM_ALIASES.get(name.upper(), name)
            # the built-in's exact spelling, so the match finds it
            existing = Uom.search([('name', '=ilike', name)], limit=1)
            vals['name'] = existing.name if existing else name
        elif phase == 'scalars' and is_new:
            # v17 factor: 1 reference = factor x this unit; v19 relative_factor:
            # 1 this unit = relative_factor x the relative unit.
            factor = float(row.get('factor') or 1.0) or 1.0
            category = cache.get('_uom_categ_', {}).get(row.get('category_id'), '')
            ref_name = UOM_CATEGORY_REFERENCE.get(category, 'Units')
            ref = Uom.search([('name', '=', ref_name)], limit=1)
            if ref and ref.name != vals.get('name'):
                vals['relative_uom_id'] = ref.id
                vals['relative_factor'] = 1.0 / factor

    def _hook_picking_type(self, row, vals, phase, is_new, cache):
        if phase == 'normalize':
            wh = self._resolve(cache, 'stock.warehouse', row.get('warehouse_id'))
            if wh:
                vals['warehouse_id'] = wh
        elif phase == 'scalars' and not is_new:
            # adopted: keep v19's own sequence and locations, take the lab's name
            for f in ('sequence_code', 'code', 'sequence', 'barcode'):
                vals.pop(f, None)

    def _sync_product_costs(self, cur, cache, stats):
        """Cost prices and valuation policy, which Odoo 17 keeps as company-
        dependent properties in ir_property rather than on the record.

        Without this every product costs 0 here: stock valuation, margin reports
        and the material requests' estimated cost would all be blank.
        """
        try:
            cur.execute("""SELECT name, res_id, value_float, value_text FROM ir_property
                            WHERE name IN ('standard_price', 'property_cost_method',
                                           'property_valuation')
                              AND res_id IS NOT NULL""")
            rows = cur.fetchall()
        except psycopg2.Error as e:
            stats.append("Product costs          SKIPPED (%s)" % e)
            return
        Product = self.env['product.product'].with_context(active_test=False)
        Category = self.env['product.category']
        costs = categories = skipped = 0
        by_category = {}
        for r in rows:
            try:
                model, src_id = r['res_id'].split(',')
                src_id = int(src_id)
            except (ValueError, AttributeError):
                continue
            if r['name'] == 'standard_price':
                if model == 'product.product':
                    target = self._resolve(cache, 'product.product', src_id)
                elif model == 'product.template':
                    tmpl = self._resolve(cache, 'product.template', src_id)
                    target = tmpl and self.env['product.template'].with_context(
                        active_test=False).browse(tmpl).product_variant_id.id
                else:
                    target = None
                if not target or not r['value_float']:
                    skipped += 1
                    continue
                try:
                    with self.env.cr.savepoint():
                        Product.browse(target).with_company(self.env.company).write(
                            {'standard_price': r['value_float']})
                    costs += 1
                except Exception as e:
                    skipped += 1
                    _logger.warning("standard_price product=%s: %s", target, e)
            elif model == 'product.category' and r.get('value_text'):
                categ = self._resolve(cache, 'product.category', src_id)
                if categ:
                    by_category.setdefault(categ, {})[r['name']] = r['value_text']
        for categ, vals in by_category.items():
            try:
                with self.env.cr.savepoint():
                    Category.browse(categ).with_company(self.env.company).write(
                        self._valid(Category, vals))
                categories += 1
            except Exception as e:
                skipped += 1
                _logger.warning("category valuation %s: %s", categ, e)
        stats.append("Product costs          products=%d categories=%d skipped=%d"
                     % (costs, categories, skipped))

    def _hook_district(self, row, vals, phase, is_new, cache):
        if phase == 'normalize':
            name = (vals.get('name') or '').strip().upper()
            vals['name'] = DISTRICT_ALIASES.get(name, name)

    def _hook_fiscal_position(self, row, vals, phase, is_new, cache):
        if phase == 'normalize':
            name = (vals.get('name') or '').strip()
            vals['name'] = FISCAL_POSITION_ALIASES.get(name.upper(), name)

    def _hook_colour(self, row, vals, phase, is_new, cache):
        if phase == 'normalize':
            # "A3," and "A3" are one shade; so are "2L2.5 2m3" and "2L2.5 2M3".
            vals['name'] = re.sub(r'[\s,]+$', '', (vals.get('name') or '').strip()).upper()

    def _hook_payment_term(self, row, vals, phase, is_new, cache):
        """A NEW term gets the source's lines; an adopted one keeps its own.

        v19 puts one default 100% line on every new term, and adding the source's
        lines on top would sum past 100% and be refused. The lines are read once in
        _run and kept on the cache.
        """
        if phase != 'scalars' or not is_new:
            return
        lines = cache.get('_pt_lines_', {}).get(row['id']) or []
        if lines:
            vals['line_ids'] = [(5, 0, 0)] + [
                (0, 0, {'value': ln.get('value') or 'percent',
                        'value_amount': ln.get('value_amount') or 0.0,
                        'nb_days': ln.get('nb_days') or 0,
                        'delay_type': ln.get('delay_type') or 'days_after',
                        'days_next_month': ln.get('days_next_month') or '0'})
                for ln in lines]

    def _hook_tax_group(self, row, vals, phase, is_new, cache):
        if phase == 'scalars' and is_new:
            vals['country_id'] = (self.env.company.account_fiscal_country_id.id
                                  or self.env.ref('base.in').id)

    def _hook_tax(self, row, vals, phase, is_new, cache):
        if phase == 'scalars':
            # v19 turned price_include into a COMPUTED field driven by
            # price_include_override; writing the v17 boolean is silently lost and
            # the tax then behaves as tax-excluded. 12 of the 121 v17 taxes are
            # inclusive, and on an inclusive tax that mistake does not just mislabel
            # the invoice — it adds the tax on top of a price that already contained
            # it, inflating the total.
            vals.pop('price_include', None)
            vals['price_include_override'] = \
                'tax_included' if row.get('price_include') else 'tax_excluded'
            if is_new:
                vals['country_id'] = (self.env.company.account_fiscal_country_id.id
                                      or self.env.ref('base.in').id)
                grp = self._resolve(cache, 'account.tax.group', row.get('tax_group_id')) \
                    or self.env['account.tax.group'].search([], limit=1).id
                vals['tax_group_id'] = grp

    def _hook_partner(self, row, vals, phase, is_new, cache):
        if phase == 'scalars':
            # v19 dropped res.partner.mobile; the number must not be lost with it.
            if not vals.get('phone') and row.get('mobile'):
                vals['phone'] = row['mobile']
            elif row.get('mobile') and row['mobile'] != vals.get('phone'):
                # both filled and different: keep the mobile in the notes
                note = 'Mobile: %s' % row['mobile']
                if note not in (vals.get('comment') or ''):
                    vals['comment'] = ((vals.get('comment') or '') + '\n' + note).strip()
            # dental_sale's own customer flags are the source of truth over the
            # ranks, which core only bumps on the first invoice.
            if row.get('is_customer') and not vals.get('customer_rank'):
                vals['customer_rank'] = 1
            if row.get('is_supplier') and not vals.get('supplier_rank'):
                vals['supplier_rank'] = 1
            # smbg_contact's "Partner ID" is a reference the clinic was quoted.
            if not vals.get('ref') and row.get('customer_id'):
                vals['ref'] = row['customer_id']
            # Who the customer is, in the suite's terms: every billed customer is a
            # CLINIC (the account the work is invoiced to); one whose name is a
            # doctor's is a doctor as well. Solo practices are both.
            name = (vals.get('name') or '').strip().upper()
            is_customer = bool(vals.get('customer_rank')) or bool(row.get('is_customer'))
            if is_customer:
                vals['is_clinic'] = True
                if re.match(r'^DR\b', name):
                    vals['is_doctor'] = True
            # sales_invoice_print kept clinic-specific invoice terms on the partner
            if row.get('terms_and_condition'):
                note = row['terms_and_condition'].strip()
                if note and note not in (vals.get('comment') or ''):
                    vals['comment'] = ((vals.get('comment') or '') + '\nTerms: ' + note).strip()
        elif phase == 'relations':
            tag = self._resolve(cache, 'res.partner.category', row.get('district_id'))
            if tag:
                ids = list(vals.get('category_id', [(6, 0, [])])[0][2]) \
                    if vals.get('category_id') else []
                if tag not in ids:
                    ids.append(tag)
                vals['category_id'] = [(6, 0, ids)]

    def _hook_product_template(self, row, vals, phase, is_new, cache):
        if phase == 'scalars':
            # v17's detailed_type (product/consu/service) -> v19's type + is_storable
            src_type = row.get('detailed_type') or row.get('type')
            if src_type == 'product':
                vals['type'], vals['is_storable'] = 'consu', True
            elif src_type == 'service':
                vals['type'] = 'service'
            else:
                vals['type'], vals['is_storable'] = 'consu', False
        elif phase == 'relations':
            # Force the tax lists to EXACTLY the v17 set, empty included.
            # _relation_vals only emits a field when something resolved, so a
            # product the source taxes nowhere would silently keep the l10n_in
            # default (5% GST) that v19 puts on every new product — and that
            # default would then be applied to invoices raised from now on.
            for f in ('taxes_id', 'supplier_taxes_id'):
                vals.setdefault(f, [(5, 0, 0)])

    # ------------------------------------------------------------------ source timestamps
    # v17 tables the transaction phases read. They have no ENTITY_SPEC, so they are
    # listed here. The third element can restrict the v19 side (unused for v17,
    # where invoices and entries share one account_move table).
    _TXN_TIMESTAMP_SOURCES = [
        ('sale.order', 'sale_order', None),
        ('sale.order.line', 'sale_order_line', None),
        ('account.move', 'account_move', None),
        ('mrp.production', 'mrp_production', None),
        ('stock.picking', 'stock_picking', None),
        ('stock.move', 'stock_move', None),
        ('stock.move.line', 'stock_move_line', None),
        ('purchase.order', 'purchase_order', None),
        ('purchase.order.line', 'purchase_order_line', None),
    ]

    def _timestamp_sources(self):
        """[(v19 model, v17 table, extra v19 condition)] — specs first, then documents."""
        seen, out = set(), []
        for spec in ENTITY_SPECS:
            key = (spec['dst'], spec['src'])
            if key in seen:
                continue
            seen.add(key)
            out.append((spec['dst'], spec['src'], None))
        for entry in self._TXN_TIMESTAMP_SOURCES:
            if (entry[0], entry[1]) not in seen:
                seen.add((entry[0], entry[1]))
                out.append(entry)
        return out

    def _stamp_timestamps(self, cur, model, table, cond=None):
        """Restore create_date / write_date from the Odoo 17 row.

        Odoo stamps both itself on create() and ignores whatever you pass in, so
        the source values can only be put back afterwards, by SQL. Left alone,
        every migrated record claims to have been created on the day the migration
        ran: "new customers this month" returns the whole customer list, the
        oldest-first sorts are meaningless, and nothing can be audited against the
        old system.
        """
        if model not in self.env:
            return 0
        Model = self.env[model]
        if not (Model._auto and Model._log_access):
            return 0
        try:
            cur.execute('SELECT id, create_date, write_date FROM "%s"' % table)
            src = {r['id']: (r['create_date'], r['write_date']) for r in cur.fetchall()}
        except psycopg2.Error:
            return 0          # table absent in this source (version drift)
        if not src:
            return 0
        q = ('SELECT mm.src_id, mm.dst_id FROM migration_map mm '
             'JOIN "%s" t ON t.id = mm.dst_id WHERE mm.dst_model = %%s' % Model._table)
        if cond:
            q += ' AND ' + cond
        self.env.cr.execute(q, (model,))
        ids, cds, wds = [], [], []
        for o10, o19 in self.env.cr.fetchall():
            ts = src.get(o10)
            if not ts or not ts[0]:
                continue
            ids.append(o19)
            cds.append(ts[0])
            wds.append(ts[1] or ts[0])
        if not ids:
            return 0
        # One statement per model rather than one per row: these run over ~100k
        # records and the ORM cannot write these columns at all.
        self.env.cr.execute("""
            UPDATE "%s" AS t
               SET create_date = v.cd, write_date = v.wd
              FROM (SELECT unnest(%%s::int[])       AS id,
                           unnest(%%s::timestamp[]) AS cd,
                           unnest(%%s::timestamp[]) AS wd) v
             WHERE t.id = v.id
               AND (t.create_date IS DISTINCT FROM v.cd
                 OR t.write_date  IS DISTINCT FROM v.wd)
        """ % Model._table, (ids, cds, wds))
        return self.env.cr.rowcount

    # Child rows the ORM creates as part of their parent's create(), through a
    # one2many. They never get a migration_map entry of their own, so
    # _stamp_timestamps — which joins the map — cannot reach them, and they kept
    # the migration run date while their parent document was correctly restored.
    # Their timestamp is the parent's by definition: an invoice line was written
    # when its invoice was.
    #
    # Each rule is matched against migration_map on the ANCESTOR, so a document
    # created natively in v19 after go-live is never rewritten. Order matters:
    # stock.move.line reads stock.move, so stock.move has to be stamped first.
    _CHILD_TIMESTAMP_SOURCES = [
        ('account.move.line', """
            UPDATE account_move_line t
               SET create_date = p.create_date, write_date = p.write_date
              FROM account_move p
              JOIN migration_map mm
                ON mm.dst_model = 'account.move' AND mm.dst_id = p.id
             WHERE t.move_id = p.id
               AND (t.create_date IS DISTINCT FROM p.create_date
                 OR t.write_date  IS DISTINCT FROM p.write_date)
        """),
        ('purchase.order.line', """
            UPDATE purchase_order_line t
               SET create_date = p.create_date, write_date = p.write_date
              FROM purchase_order p
              JOIN migration_map mm
                ON mm.dst_model = 'purchase.order' AND mm.dst_id = p.id
             WHERE t.order_id = p.id
               AND (t.create_date IS DISTINCT FROM p.create_date
                 OR t.write_date  IS DISTINCT FROM p.write_date)
        """),
        ('stock.move (picking)', """
            UPDATE stock_move t
               SET create_date = p.create_date, write_date = p.write_date
              FROM stock_picking p
              JOIN migration_map mm
                ON mm.dst_model = 'stock.picking' AND mm.dst_id = p.id
             WHERE t.picking_id = p.id
               AND (t.create_date IS DISTINCT FROM p.create_date
                 OR t.write_date  IS DISTINCT FROM p.write_date)
        """),
        ('stock.move (MO finished)', """
            UPDATE stock_move t
               SET create_date = p.create_date, write_date = p.write_date
              FROM mrp_production p
              JOIN migration_map mm
                ON mm.dst_model = 'mrp.production' AND mm.dst_id = p.id
             WHERE t.production_id = p.id
               AND (t.create_date IS DISTINCT FROM p.create_date
                 OR t.write_date  IS DISTINCT FROM p.write_date)
        """),
        ('stock.move (MO raw)', """
            UPDATE stock_move t
               SET create_date = p.create_date, write_date = p.write_date
              FROM mrp_production p
              JOIN migration_map mm
                ON mm.dst_model = 'mrp.production' AND mm.dst_id = p.id
             WHERE t.raw_material_production_id = p.id
               AND (t.create_date IS DISTINCT FROM p.create_date
                 OR t.write_date  IS DISTINCT FROM p.write_date)
        """),
        ('stock.move.line', """
            UPDATE stock_move_line t
               SET create_date = sm.create_date, write_date = sm.write_date
              FROM stock_move sm
             WHERE t.move_id = sm.id
               AND EXISTS (
                     SELECT 1 FROM migration_map mm
                      WHERE (mm.dst_model = 'stock.picking'
                             AND mm.dst_id = sm.picking_id)
                         OR (mm.dst_model = 'mrp.production'
                             AND mm.dst_id IN (sm.production_id,
                                                  sm.raw_material_production_id)))
               AND (t.create_date IS DISTINCT FROM sm.create_date
                 OR t.write_date  IS DISTINCT FROM sm.write_date)
        """),
    ]

    def _stamp_child_timestamps(self):
        """Carry each restored document's timestamps down to its own lines."""
        stats, total = [], 0
        for label, sql in self._CHILD_TIMESTAMP_SOURCES:
            self.env.cr.execute(sql)
            n = self.env.cr.rowcount
            total += n
            if n:
                stats.append("  %-24s %7d" % (label, n))
        return total, stats

    def _sync_timestamps(self, cur):
        stats, total = [], 0
        for model, table, cond in self._timestamp_sources():
            n = self._stamp_timestamps(cur, model, table, cond)
            total += n
            if n:
                stats.append("  %-24s %7d" % (model, n))
        # After the parents, never before: the children copy what was just restored.
        n, child_stats = self._stamp_child_timestamps()
        total += n
        stats += child_stats
        self.env.invalidate_all()      # the ORM cached the values we just replaced
        return total, stats

    def action_sync_timestamps(self):
        """Backfill create_date/write_date on everything already migrated."""
        self.ensure_one()
        # Raw UPDATEs and a commit, all before the log write the ACL would refuse.
        self._check_migration_manager()
        conn = self._connect()
        conn.autocommit = True
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            total, stats = self._sync_timestamps(cur)
        finally:
            conn.close()
        self.env.cr.commit()
        log = "SOURCE TIMESTAMPS RESTORED\n%s\n  %-24s %7d" % (
            "\n".join(stats), 'TOTAL', total)
        self.txn_log = log
        return log

    # ------------------------------------------------------------------ opening balances
    _OPENING_REF = 'Opening Balance (Odoo 17 migration)'

    # Unsettled receivable/payable documents as of the cut-over.
    #
    # "Unsettled AS OF the cut" is not the same as Odoo 17's own `reconciled` flag,
    # which reflects today. An invoice paid in June was still open on 31-Mar, so the
    # matched amount only counts when the COUNTERPART line is itself dated on or
    # before the cut — hence the join back to the other side of every partial.
    _OPEN_ITEM_SQL = """
        WITH win AS (
            SELECT aml.id, aml.account_id, aml.partner_id, aml.date, aml.date_maturity,
                   aml.debit - aml.credit AS bal,
                   COALESCE(NULLIF(am.name, ''), NULLIF(aml.ref, '')) AS doc
              FROM account_move_line aml
              JOIN account_move am  ON am.id = aml.move_id AND am.state = 'posted'
              JOIN account_account aa ON aa.id = aml.account_id
             WHERE aml.date <= %(cut)s AND aml.date > %(frm)s
               AND am.company_id = %(cid)s
               AND aa.account_type IN ('asset_receivable', 'liability_payable')
        ), matched AS (
            SELECT w.id,
                   COALESCE(sum(apr.amount) FILTER (WHERE apr.debit_move_id  = w.id), 0)
                 - COALESCE(sum(apr.amount) FILTER (WHERE apr.credit_move_id = w.id), 0)
                   AS amt
              FROM win w
              JOIN account_partial_reconcile apr
                ON apr.debit_move_id = w.id OR apr.credit_move_id = w.id
              JOIN account_move_line other
                ON other.id = CASE WHEN apr.debit_move_id = w.id
                                   THEN apr.credit_move_id ELSE apr.debit_move_id END
             WHERE other.date <= %(cut)s
             GROUP BY w.id
        )
        SELECT w.id AS src_id, w.account_id, w.partner_id, w.date, w.date_maturity, w.doc,
               w.bal - COALESCE(m.amt, 0) AS residual
          FROM win w
          LEFT JOIN matched m ON m.id = w.id
         ORDER BY w.partner_id, w.account_id, w.date, w.id
    """

    # Roughly how many move lines to create and post per transaction.
    _OPENING_LINE_CHUNK = 4000

    @staticmethod
    def _chunk_by_lines(batch, budget):
        """Group move payloads into transactions of about ``budget`` lines each.

        An entry never straddles two chunks, so one oversized partner simply gets
        a chunk to itself rather than being split into an unbalanced pair.
        """
        chunk, count = [], 0
        for vals in batch:
            n = len(vals['line_ids'])
            if chunk and count + n > budget:
                yield chunk
                chunk, count = [], 0
            chunk.append(vals)
            count += n
        if chunk:
            yield chunk

    def _opening_journal(self, company):
        journal = self.env['account.journal'].search(
            [('type', '=', 'general'), ('company_id', '=', company.id)], limit=1)
        if not journal:
            journal = self.env['account.journal'].create({
                'name': 'Opening', 'code': 'OPEN', 'type': 'general', 'company_id': company.id})
        return journal

    def _opening_equity_account(self, company):
        # with_company: account.code is company-dependent (code_store) in v19.
        Account = self.env['account.account'].with_company(company)
        acc = Account.search(
            [('account_type', '=', 'equity'), ('company_ids', 'in', company.id)], limit=1)
        if not acc:
            acc = Account.create({
                'name': 'Opening Balance', 'code': 'OB9999', 'account_type': 'equity',
                'company_ids': [(6, 0, company.ids)]})
        return acc

    def _opening_accounting(self):
        """Post the opening balances PER company.

        Receivable and payable land as ONE ENTRY PER PARTNER, not as 3,000 lines
        of a single company-wide entry. Those are the balances that get settled
        later: a payment arriving after the cut-over has to be matched against
        that partner's own opening item, and inside one shared entry nobody's
        opening can be corrected without resetting everybody else's to draft and
        breaking every reconciliation already made against it.

        Inside that entry the balance is ITEMISED: one line per document still
        unsettled at the cut, carrying its number and its due date. A lump sum can
        only ever be reconciled as a lump sum — the cash clerk can see that a doctor
        owes 29,650 but not which four invoices make it up, so a payment for one of
        them cannot be matched and the ageing report shows the whole amount in a
        single bucket. ``opening_open_item_years`` bounds how far back that detail
        goes; older history collapses into one carry-forward line per partner.

        Every other balance-sheet account has no partner to reconcile against and
        stays in one general opening entry per company.
        """
        self.ensure_one()
        # Balances are cut at opening_date; the entry itself may be dated later
        # (see opening_move_date) so that cut-over-day documents stay documents.
        date = self.opening_date
        move_date = self.opening_move_date or date
        years = self.opening_open_item_years or 0
        # 0 = itemise everything. Date.min rather than None keeps one SQL shape.
        detail_from = fields.Date.subtract(date, years=years) if years > 0 \
            else fields.Date.to_date('1900-01-01')
        # Idempotent: drop all previous migration opening moves (any company).
        # Nothing below commits: the deletion and every new entry stand or fall
        # together with the caller's transaction, so a failure part-way leaves the
        # previous opening in place instead of half of a new one.
        old = self.env['account.move'].search([('ref', '=', self._OPENING_REF)])
        if old:
            # A re-run after go-live can hit opening items that have already been
            # settled; unlink refuses while the reconciliation stands.
            matched = old.line_ids.filtered(
                lambda l: l.matched_debit_ids or l.matched_credit_ids)
            if matched:
                matched.remove_move_reconcile()
            old.filtered(lambda m: m.state == 'posted').button_draft()
            old.unlink()
        conn = self._connect()
        conn.autocommit = True
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cache = {}
        out = []
        try:
            for comp in self._fetch(cur, 'res_company') or []:
                v19_cid = self._resolve(cache, 'res.company', comp['id'])
                if not v19_cid:
                    continue
                company = self.env['res.company'].browse(v19_cid)
                cur.execute("""
                    SELECT aml.account_id, aml.partner_id,
                           COALESCE(sum(aml.debit),0) d, COALESCE(sum(aml.credit),0) c
                    FROM account_move_line aml
                    JOIN account_move am ON am.id = aml.move_id AND am.state = 'posted'
                    JOIN account_account aa ON aa.id = aml.account_id
                    WHERE aml.date <= %s AND am.company_id = %s
                      AND aa.account_type IN ('asset_receivable','liability_payable')
                    GROUP BY aml.account_id, aml.partner_id
                """, (date, comp['id']))
                partner_rows = cur.fetchall()
                cur.execute("""
                    SELECT aml.account_id, COALESCE(sum(aml.debit),0) d, COALESCE(sum(aml.credit),0) c
                    FROM account_move_line aml
                    JOIN account_move am ON am.id = aml.move_id AND am.state = 'posted'
                    JOIN account_account aa ON aa.id = aml.account_id
                    WHERE aml.date <= %s AND am.company_id = %s
                      AND aa.include_initial_balance = TRUE
                      AND aa.account_type NOT IN ('asset_receivable','liability_payable')
                    GROUP BY aml.account_id
                """, (date, comp['id']))
                acc_rows = cur.fetchall()

                journal_id = self._opening_journal(company).id
                equity_id = self._opening_equity_account(company).id
                label = _("Opening balance as of %s") % fields.Date.to_string(date)

                def _line(acc_id, bal, partner_id=False, name=None, maturity=None,
                          src=False):
                    v19_acc = self._resolve(cache, 'account.account', acc_id)
                    if not v19_acc or abs(bal) < 0.005:
                        return None
                    v = {'account_id': v19_acc, 'name': name or label,
                         'debit': round(bal, 2) if bal > 0 else 0.0,
                         'credit': round(-bal, 2) if bal < 0 else 0.0}
                    if src:
                        # Which document in the old ledger this line stands for.
                        v['x_src_id'] = src
                    if partner_id:
                        v['partner_id'] = partner_id
                    if maturity:
                        # Without it every opening item ages into the same bucket
                        # and the aged-partner reports read as one big "current".
                        v['date_maturity'] = maturity
                    return v

                def _balanced(lines, partner_id=False):
                    """Close a set of lines against the opening equity account."""
                    total = round(sum(l['debit'] - l['credit'] for l in lines), 2)
                    if abs(total) >= 0.005:
                        lines.append({'account_id': equity_id, 'name': label,
                                      'debit': -total if total < 0 else 0.0,
                                      'credit': total if total > 0 else 0.0})
                    vals = {'journal_id': journal_id, 'date': move_date,
                            'ref': self._OPENING_REF,
                            'line_ids': [(0, 0, l) for l in lines]}
                    # The header partner carries check_company, which the line does
                    # not: 82 doctors are owned by one company but carry a balance in
                    # the other's ledger. Naming them on the entry would be rejected,
                    # so those entries keep the partner on the line only.
                    if partner_id and p_company.get(partner_id) in (None, False, company.id):
                        vals['partner_id'] = partner_id
                    return vals

                # --- one entry per partner, itemised down to the open document
                cur.execute(self._OPEN_ITEM_SQL,
                            {'cut': date, 'frm': detail_from, 'cid': comp['id']})
                detail_lines, carry_lines = {}, {}
                # What the itemised lines account for, per (account, partner), in the
                # rounded amounts actually posted — so the carry-forward below closes
                # the gap to the cent instead of leaving a rounding tail.
                itemised = defaultdict(float)
                for r in cur.fetchall():
                    v19_partner = self._resolve(cache, 'res.partner', r['partner_id'])
                    if not v19_partner:
                        continue      # balance without a partner falls to the general entry
                    line = _line(r['account_id'], float(r['residual']), v19_partner,
                                 name=r['doc'] or label,
                                 maturity=r['date_maturity'] or r['date'],
                                 src=r.get('src_id'))
                    if line:
                        detail_lines.setdefault(v19_partner, []).append(line)
                        itemised[(r['account_id'], r['partner_id'])] += \
                            line['debit'] - line['credit']

                # --- everything the detail does not cover: history older than the
                # itemised window, and the handful of v17 reconciliations that were
                # made across two different partners (so one side's document is
                # settled while the group still carries a balance). Derived from the
                # authoritative per-account total, which is why each partner's
                # opening sums to exactly the balance the old ledger closed on.
                carry_label = _("Balance carried forward (before %s)") % \
                    fields.Date.to_string(fields.Date.add(detail_from, days=1))
                for r in partner_rows:
                    v19_partner = self._resolve(cache, 'res.partner', r['partner_id'])
                    if not v19_partner:
                        continue
                    carry = (float(r['d']) - float(r['c'])
                             - itemised.get((r['account_id'], r['partner_id']), 0.0))
                    line = _line(r['account_id'], carry, v19_partner, name=carry_label)
                    if line:
                        carry_lines.setdefault(v19_partner, []).append(line)

                # Oldest first: the carry-forward, then the documents by date.
                by_partner = {}
                for pid in set(detail_lines) | set(carry_lines):
                    by_partner[pid] = carry_lines.get(pid, []) + detail_lines.get(pid, [])

                # --- balances with no partner to reconcile against: one general entry
                general = [l for l in (_line(r['account_id'], r['d'] - r['c'])
                                       for r in acc_rows) if l]
                unpartnered = [l for l in (_line(r['account_id'], r['d'] - r['c'])
                                           for r in partner_rows
                                           if not self._resolve(cache, 'res.partner',
                                                                r['partner_id'])) if l]
                general.extend(unpartnered)

                p_company = {}
                if by_partner:
                    self.env.cr.execute(
                        "SELECT id, company_id FROM res_partner WHERE id = ANY(%s)",
                        (list(by_partner),))
                    p_company = dict(self.env.cr.fetchall())

                batch = [_balanced(lines, pid) for pid, lines in by_partner.items()]
                if general:
                    batch.append(_balanced(general))
                if not batch:
                    out.append("Opening acct [%s]: nothing" % company.name)
                    continue

                Move = self.env['account.move'].with_company(company)
                made = 0
                # Chunked by LINE count, not entry count: itemising turns a
                # 2-line-per-partner opening into one that runs to hundreds of lines
                # for a busy doctor, and it is the lines that cost time and memory to
                # create and post.
                #
                # Chunks used to commit one by one and a failing chunk was only
                # logged, so a re-run could delete the old opening and commit most of
                # a new one — some partners' balances simply missing. A failure now
                # aborts the whole run; flushing and clearing the cache per chunk
                # keeps memory bounded the way the commit used to.
                for chunk in self._chunk_by_lines(batch, self._OPENING_LINE_CHUNK):
                    try:
                        with self.env.cr.savepoint():
                            # a copy, so _opening_failure can replay the chunk
                            # from the payload as it was built
                            moves = Move.create(copy.deepcopy(chunk))
                            moves.action_post()
                    except Exception as e:
                        raise UserError(self._opening_failure(Move, company, chunk, e)) from e
                    made += len(moves)
                    self.env.flush_all()
                    self.env.invalidate_all()
                n_detail = sum(len(v) for v in detail_lines.values())
                n_carry = sum(len(v) for v in carry_lines.values())
                out.append("Opening acct [%s]: %d entries (%d per-partner, %d general), "
                           "%d lines (%d open items + %d carried forward)"
                           % (company.name, made, len(by_partner), 1 if general else 0,
                              sum(len(v['line_ids']) for v in batch), n_detail, n_carry))
        finally:
            conn.close()
        return out

    def _opening_failure(self, Move, company, chunk, error):
        """Name the entry that sank a failed opening chunk.

        A chunk holds hundreds of partners; "chunk failed" tells nobody what to fix.
        Each entry is retried alone inside a savepoint that is always rolled back,
        so finding the culprit writes nothing.
        """
        class _Probe(Exception):
            pass

        culprit, reason = None, error
        for vals in chunk:
            try:
                with self.env.cr.savepoint():
                    # a copy: create() may rework the command lists it is given
                    Move.create([copy.deepcopy(vals)]).action_post()
                    raise _Probe()
            except _Probe:
                continue
            except Exception as e:
                culprit, reason = vals, e
                break
        partner_id = culprit and (culprit.get('partner_id') or next(
            (cmd[2]['partner_id'] for cmd in culprit['line_ids']
             if cmd[2].get('partner_id')), False))
        if partner_id:
            partner = self.env['res.partner'].browse(partner_id)
            who = "%s (id %s)" % (partner.display_name, partner_id)
        elif culprit:
            who = _("the general entry (balances without a partner)")
        else:
            who = _("a chunk of %s entries (no single entry fails on its own)") % len(chunk)
        _logger.error("opening balance [%s] failed on %s: %s", company.name, who, reason)
        return _("Opening balances were NOT posted for %(company)s: the entry for "
                 "%(who)s failed.\n\n%(error)s\n\nThe whole run was rolled back, so "
                 "the previous opening entries are still in place.",
                 company=company.name, who=who, error=reason)

    # ------------------------------------------------------------------ opening refresh
    def action_refresh_opening_balances(self):
        """Bring the opening balances in line with edits made in Odoo 17 since.

        The full sync rebuilds every opening entry, which after go-live means
        breaking every reconciliation made against them. This does the small thing
        instead: it asks the old ledger for its open items again and settles the
        difference item by item, matched by the id of the line they came from -
        the amount that changed, the document that was added, the one that is no
        longer open. Only the partners whose opening actually differs are touched,
        and a line already reconciled here is never rewritten - it is named in the
        log for a person to decide. (client, 2026-09-18)
        """
        self = self._migration_env()
        stats = self._refresh_opening()
        self.log = "OPENING BALANCE REFRESH\n" + "\n".join(stats)
        self.state = 'ok'
        return self._notify(_("Opening balances refreshed. See the log."), sticky=True)

    def _refresh_opening(self):
        self.ensure_one()
        date = self.opening_date
        years = self.opening_open_item_years or 0
        detail_from = fields.Date.subtract(date, years=years) if years > 0 \
            else fields.Date.to_date('1900-01-01')
        carry_label = _("Balance carried forward (before %s)") % \
            fields.Date.to_string(fields.Date.add(detail_from, days=1))
        label = _("Opening balance as of %s") % fields.Date.to_string(date)
        conn = self._connect()
        conn.autocommit = True
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cache = {}
        out = []
        try:
            for comp in self._fetch(cur, 'res_company') or []:
                v19_cid = self._resolve(cache, 'res.company', comp['id'])
                if not v19_cid:
                    continue
                company = self.env['res.company'].browse(v19_cid)
                out.append(self._refresh_opening_company(
                    cur, cache, company, comp['id'], date, detail_from,
                    label, carry_label))
        finally:
            conn.close()
        return out

    def _refresh_opening_company(self, cur, cache, company, src_company_id, date,
                                 detail_from, label, carry_label):
        """One company's opening, item by item. Returns the line for the log."""
        Move = self.env['account.move'].with_company(company)
        moves = Move.search([('ref', '=', self._OPENING_REF),
                             ('company_id', '=', company.id)])
        if not moves:
            return "Opening refresh [%s]: nothing migrated yet - run the full opening" \
                % company.name

        wanted, totals = self._source_open_items(cur, cache, date, detail_from,
                                                 src_company_id, label)
        lines = moves.line_ids.filtered(lambda l: l.partner_id)
        stamped = self._stamp_opening_lines(lines, wanted)
        here = {l.x_src_id: l for l in lines if l.x_src_id}
        equity = self._opening_equity_account(company)
        carry_wanted = self._opening_carry(wanted, totals)
        carry_here = {(l.partner_id.id, l.account_id.id): l
                      for l in lines if l.name == carry_label}
        # One entry per partner is the whole point of how the opening is built; a
        # partner with several keeps the first for anything new.
        entry_of = {}
        for move in moves:
            for partner in move.line_ids.mapped('partner_id'):
                entry_of.setdefault(partner.id, move)

        def blocked(line):
            return bool(line.reconciled or line.matched_debit_ids or line.matched_credit_ids)

        plans, locked = {}, 0

        def plan(move):
            return plans.setdefault(move, {'change': [], 'drop': [], 'add': []})

        fresh = defaultdict(list)          # partner -> items with no entry to join
        for src, want in wanted.items():
            line = here.get(src)
            if line is None:
                move = entry_of.get(want['partner'])
                if move is None:
                    # A doctor who has open items in the old ledger and no opening
                    # entry here at all: give them one, rather than name them in a
                    # log nobody acts on. (client, 2026-09-18)
                    fresh[want['partner']].append((src, want))
                    continue
                plan(move)['add'].append((src, want))
            elif not self._opening_line_matches(line, want):
                if blocked(line):
                    locked += 1
                else:
                    plan(line.move_id)['change'].append((line, self._opening_vals(want)))
        for src, line in here.items():
            if src in wanted:
                continue
            if blocked(line):
                locked += 1
            else:
                plan(line.move_id)['drop'].append(line)
        for key, amount in carry_wanted.items():
            partner_id, account_id = key
            line = carry_here.get(key)
            if line is not None:
                if abs(round(line.debit - line.credit, 2) - amount) < 0.005:
                    continue
                if blocked(line):
                    locked += 1
                elif abs(amount) < 0.005:
                    plan(line.move_id)['drop'].append(line)
                else:
                    plan(line.move_id)['change'].append((line, {
                        'debit': amount if amount > 0 else 0.0,
                        'credit': -amount if amount < 0 else 0.0}))
            elif abs(amount) >= 0.005:
                carry_item = (False, {'partner': partner_id, 'account': account_id,
                                      'name': carry_label, 'maturity': False,
                                      'balance': amount})
                if entry_of.get(partner_id):
                    plan(entry_of[partner_id])['add'].append(carry_item)
                elif partner_id in fresh:
                    fresh[partner_id].append(carry_item)

        if not plans and not fresh:
            note = ("Opening refresh [%s]: nothing to change (%d items checked)"
                    % (company.name, len(wanted)))
            if stamped:
                note += ", %d matched to their old line" % stamped
            if locked:
                note += ", %d differ but are already reconciled here" % locked
            return note

        changed = added = removed = 0
        opened = self._open_new_entries(Move, company, equity, label, fresh)
        touched = Move.browse(list({m.id for m in plans}))
        touched.button_draft()
        for move, todo in plans.items():
            drop = {line.id for line in todo['drop']}
            change = {line.id: vals for line, vals in todo['change']}
            commands, running = [], 0.0
            counter = None
            for line in move.line_ids:
                # The line that closes the entry. It carries the partner too - Odoo
                # copies the entry's partner onto every line that has none - so it
                # is recognised by its account alone. (2026-09-18)
                if line.account_id == equity:
                    counter = line
                    continue
                if line.id in drop:
                    commands.append((2, line.id))
                    removed += 1
                    continue
                if line.id in change:
                    vals = change[line.id]
                    commands.append((1, line.id, vals))
                    changed += 1
                    running += vals.get('debit', 0.0) - vals.get('credit', 0.0)
                    continue
                running += line.debit - line.credit
            for src, want in todo['add']:
                vals = dict(self._opening_vals(want), account_id=want['account'],
                            partner_id=want['partner'])
                if src:
                    vals['x_src_id'] = src
                commands.append((0, 0, vals))
                added += 1
                running += vals['debit'] - vals['credit']
            # The entry closes against opening equity in the SAME write: an
            # unbalanced intermediate state is refused, so this cannot be a
            # second one. (2026-09-18)
            total = round(running, 2)
            if counter is not None:
                if abs(total) < 0.005:
                    commands.append((2, counter.id))
                else:
                    commands.append((1, counter.id, {
                        'debit': -total if total < 0 else 0.0,
                        'credit': total if total > 0 else 0.0}))
            elif abs(total) >= 0.005:
                commands.append((0, 0, {
                    'account_id': equity.id, 'name': label,
                    'debit': -total if total < 0 else 0.0,
                    'credit': total if total > 0 else 0.0}))
            move.write({'line_ids': commands})
        # A doctor whose old balance went to nothing is left with an empty entry;
        # an empty entry cannot be posted and says nothing, so it goes.
        emptied = touched.filtered(lambda m: not m.line_ids)
        if emptied:
            touched -= emptied
            emptied.unlink()
        touched.action_post()

        note = "Opening refresh [%s]: %d changed, %d added, %d removed, %d entries" \
               % (company.name, changed + opened['changed'], added + opened['added'],
                  removed, len(touched) + opened['entries'])
        if opened['entries']:
            note += " (%d of them new: %s)" % (opened['entries'], opened['who'])
        if stamped:
            note += ", %d matched to their old line" % stamped
        if locked:
            note += ", %d left alone (already reconciled here)" % locked
        return note

    def _open_new_entries(self, Move, company, equity, label, fresh):
        """An opening entry for a partner who has open items in the old ledger and
        none here - built the way the first migration builds them."""
        out = {'entries': 0, 'added': 0, 'changed': 0, 'who': ''}
        if not fresh:
            return out
        journal = self._opening_journal(company)
        move_date = self.opening_move_date or self.opening_date
        partners = self.env['res.partner'].browse(list(fresh)).exists()
        made = Move.browse()
        for partner in partners:
            lines, running = [], 0.0
            for src, want in fresh[partner.id]:
                vals = dict(self._opening_vals(want), account_id=want['account'],
                            partner_id=partner.id)
                if src:
                    vals['x_src_id'] = src
                lines.append((0, 0, vals))
                running += vals['debit'] - vals['credit']
            total = round(running, 2)
            if abs(total) < 0.005:
                continue
            lines.append((0, 0, {'account_id': equity.id, 'name': label,
                                 'debit': -total if total < 0 else 0.0,
                                 'credit': total if total > 0 else 0.0}))
            vals = {'journal_id': journal.id, 'date': move_date,
                    'ref': self._OPENING_REF, 'line_ids': lines}
            # Same rule as the first migration: the header partner carries
            # check_company, the line does not.
            if partner.company_id in (False, company):
                vals['partner_id'] = partner.id
            move = Move.create(vals)
            move.action_post()
            made |= move
            out['entries'] += 1
            out['added'] += len(lines) - 1
        out['who'] = ", ".join(partners[:4].mapped('display_name'))
        if len(partners) > 4:
            out['who'] += " and %d more" % (len(partners) - 4)
        return out

    def _source_open_items(self, cur, cache, date, detail_from, src_company_id, label):
        """The old ledger's open items (by its own line id) and its per-partner
        account totals, both mapped to this database's ids."""
        cur.execute(self._OPEN_ITEM_SQL,
                    {'cut': date, 'frm': detail_from, 'cid': src_company_id})
        items = cur.fetchall()
        # The map can point at a record somebody has since deleted here. Checked
        # once, in bulk, rather than discovered by a MissingError halfway through
        # a run. (2026-09-18)
        alive = self._alive_ids(cache, items)
        wanted, totals = {}, defaultdict(float)
        for r in items:
            partner = self._resolve(cache, 'res.partner', r['partner_id'])
            account = self._resolve(cache, 'account.account', r['account_id'])
            if not (partner in alive['res.partner'] and account in alive['account.account']
                    and r.get('src_id')):
                continue
            wanted[r['src_id']] = {
                'partner': partner, 'account': account,
                'name': r['doc'] or label,
                'maturity': r['date_maturity'] or r['date'],
                'balance': round(float(r['residual']), 2),
            }
        cur.execute("""
            SELECT aml.account_id, aml.partner_id,
                   COALESCE(sum(aml.debit),0) d, COALESCE(sum(aml.credit),0) c
            FROM account_move_line aml
            JOIN account_move am ON am.id = aml.move_id AND am.state = 'posted'
            JOIN account_account aa ON aa.id = aml.account_id
            WHERE aml.date <= %s AND am.company_id = %s
              AND aa.account_type IN ('asset_receivable','liability_payable')
            GROUP BY aml.account_id, aml.partner_id
        """, (date, src_company_id))
        rows = cur.fetchall()
        alive = self._alive_ids(cache, rows, alive)
        for r in rows:
            partner = self._resolve(cache, 'res.partner', r['partner_id'])
            account = self._resolve(cache, 'account.account', r['account_id'])
            if partner in alive['res.partner'] and account in alive['account.account']:
                totals[(partner, account)] += round(float(r['d']) - float(r['c']), 2)
        return wanted, totals

    def _alive_ids(self, cache, rows, alive=None):
        """{model: set of ids that still exist} for the partners and accounts these
        source rows resolve to."""
        alive = alive or {'res.partner': set(), 'account.account': set()}
        wanted = {'res.partner': set(), 'account.account': set()}
        for r in rows:
            for model, key in (('res.partner', 'partner_id'),
                               ('account.account', 'account_id')):
                rid = self._resolve(cache, model, r.get(key))
                if rid and rid not in alive[model]:
                    wanted[model].add(rid)
        for model, ids in wanted.items():
            if ids:
                alive[model] |= set(self.env[model].browse(ids).exists().ids)
        return alive

    @staticmethod
    def _opening_vals(want):
        amount = want['balance']
        return {'name': want['name'], 'date_maturity': want['maturity'],
                'debit': amount if amount > 0 else 0.0,
                'credit': -amount if amount < 0 else 0.0}

    @staticmethod
    def _opening_line_matches(line, want):
        return (abs(round(line.debit - line.credit, 2) - want['balance']) < 0.005
                and (line.name or '') == (want['name'] or '')
                and line.date_maturity == want['maturity']
                and line.account_id.id == want['account'])

    @staticmethod
    def _opening_carry(wanted, totals):
        """What each partner's carry-forward line has to be for their opening to
        equal the old ledger: the account total less everything itemised."""
        itemised = defaultdict(float)
        for want in wanted.values():
            itemised[(want['partner'], want['account'])] += want['balance']
        carry = {}
        for key, total in totals.items():
            carry[key] = round(total - itemised.get(key, 0.0), 2)
        for key, amount in itemised.items():
            carry.setdefault(key, round(-amount + totals.get(key, 0.0), 2))
        return carry

    def _stamp_opening_lines(self, lines, wanted):
        """Match the opening lines of a first migration - which carry no old id -
        to the items they were made from, by partner, account and document number.

        Only where it is beyond doubt: one line, one item. Anything ambiguous is
        left unstamped and simply not touched by the refresh.
        """
        blanks = lines.filtered(lambda l: not l.x_src_id and l.name)
        if not blanks:
            return 0
        by_key = defaultdict(list)
        for src, want in wanted.items():
            by_key[(want['partner'], want['account'], want['name'])].append(src)
        taken = set(lines.mapped('x_src_id'))
        pairs = []
        for line in blanks:
            key = (line.partner_id.id, line.account_id.id, line.name)
            candidates = [s for s in by_key.get(key, []) if s not in taken]
            if len(candidates) != 1:
                continue
            pairs.append((line.id, candidates[0]))
            taken.add(candidates[0])
        if not pairs:
            return 0
        # In one statement, not 168,000 writes: this is a back-fill of a column
        # nothing computes from, and an ORM write per line took the action from
        # seconds to many minutes. Flushed FIRST - anything the ORM still holds for
        # these rows would otherwise be written over the stamp a moment later, and
        # the refresh would then see none of them. (2026-09-18)
        self.env.flush_all()
        psycopg2.extras.execute_values(
            self.env.cr._obj,
            "UPDATE account_move_line SET x_src_id = v.src "
            "FROM (VALUES %s) AS v(id, src) WHERE account_move_line.id = v.id",
            pairs, page_size=5000)
        self.env.invalidate_all()
        return len(pairs)

    def _opening_inventory(self):
        """Set opening on-hand PER company (all Odoo 17 companies)."""
        self.ensure_one()
        # inventory_as_of lets this be re-run for a later position once the
        # post-opening transfers are in (see action_refresh_inventory).
        date = self.inventory_as_of or self.opening_date
        conn = self._connect()
        conn.autocommit = True
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cache = {}
        out = []
        try:
            for comp in self._fetch(cur, 'res_company') or []:
                v19_cid = self._resolve(cache, 'res.company', comp['id'])
                if not v19_cid:
                    continue
                company = self.env['res.company'].browse(v19_cid)
                warehouse = self.env['stock.warehouse'].search([('company_id', '=', company.id)], limit=1)
                if not warehouse:
                    out.append("Opening inv [%s]: no warehouse" % company.name)
                    continue
                location = warehouse.lot_stock_id
                # product_qty, not product_uom_qty: the latter is in the MOVE's unit
                # (a box of 12 counts as 1), the former in the product's own unit,
                # which is the unit a quant's quantity is kept in.
                # Per LOCATION: the lab keeps stock in department stores (Z- Ceramic
                # Department...) and those were migrated as locations; a store that
                # did not map falls back to the warehouse's main stock.
                cur.execute("""
                    SELECT product_id, location_id, sum(qty) qty FROM (
                        SELECT sm.product_id, sm.location_dest_id AS location_id,
                               sm.product_qty AS qty
                          FROM stock_move sm JOIN stock_location d ON d.id = sm.location_dest_id
                         WHERE sm.state = 'done' AND d.usage = 'internal'
                           AND sm.date <= %s AND sm.company_id = %s
                        UNION ALL
                        SELECT sm.product_id, sm.location_id, -sm.product_qty
                          FROM stock_move sm JOIN stock_location s ON s.id = sm.location_id
                         WHERE sm.state = 'done' AND s.usage = 'internal'
                           AND sm.date <= %s AND sm.company_id = %s
                    ) t GROUP BY product_id, location_id
                """, (date, comp['id'], date, comp['id']))
                # No `HAVING sum(qty) <> 0`: a product whose source balance is zero
                # still has to be written when this runs as a REFRESH, or a product
                # that sold out since the opening keeps its stale opening quantity
                # forever. Zero rows are cheap and land as an adjustment to 0.
                rows = cur.fetchall()
                Quant = self.env['stock.quant'].with_company(company).with_context(inventory_mode=True)
                applied = missing = skipped = 0
                for r in rows:
                    prod_id = self._resolve(cache, 'product.product', r['product_id'])
                    if not prod_id:
                        missing += 1
                        continue
                    product = self.env['product.product'].browse(prod_id)
                    if product.type != 'consu' or not product.is_storable:
                        skipped += 1
                        continue
                    loc_id = self._resolve(cache, 'stock.location', r.get('location_id')) \
                        or location.id
                    try:
                        with self.env.cr.savepoint():
                            quant = Quant.create({'product_id': prod_id, 'location_id': loc_id,
                                                  'inventory_quantity': r['qty']})
                            quant.action_apply_inventory()
                        applied += 1
                    except Exception as e:
                        missing += 1
                        _logger.warning("opening inventory product=%s: %s", prod_id, e)
                out.append("Opening inv [%s]: %d set, %d unmapped, %d non-stockable"
                           % (company.name, applied, missing, skipped))
        finally:
            conn.close()
        return out
