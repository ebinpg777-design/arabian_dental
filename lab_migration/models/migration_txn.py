# -*- coding: utf-8 -*-
"""Documents dated on/after the cut-over — the gap named in runbook §8.

``ENTITY_SPECS`` stops at master data plus a balance-forward opening. This file
adds the documents themselves for the period AFTER the opening cut: sale orders,
customer/vendor invoices and the remaining journal entries.

Two rules run through all of it.

**Documents are written; workflows are not run.** A migrated sale order gets its
state by a direct write, never through ``action_confirm()`` — confirming would
re-explode procurement and manufacture a second set of MOs and transfers beside
the ones the source already has. Invoices are the one exception: they are posted,
because posting is what produces the journal entry, and the v17 invoice's own
journal entry is deliberately NOT imported (see ``_txn_journal_entries``).

**Everything is idempotent.** Every document carries ``x_src_id`` plus a
``migration.map`` row, so a second run updates in place instead of duplicating.

v17 -> v19 differences that bite here, all handled below:
  sale.order.line.product_uom -> product_uom_id ;  tax_id -> tax_ids
  purchase.order.line.product_uom -> product_uom_id
  invoices and entries share account_move (move_type tells them apart)
  translated columns are jsonb (unwrapped by _coerce)

dental_sale's fields land on the suite's own:
  order.patient_name / line.patient_name -> sale.order.patient
  order.shade_name (res.shade)           -> line.color_scheme (product.colour)
  order.material_name                    -> order.instruction ("Material: VITA")
  order.sale_priority                    -> order.priority
  order.technitian_name (m2m)            -> order.technician_ids
  line.jaw                               -> line.ul
  line.quad1..4 / t_no / quarter         -> line.teeth (FDI numbers)
  the same on invoice lines              -> account.move.line.patient / ul / teeth
"""
import logging
import mimetypes
import os
import re

import psycopg2
import psycopg2.extras

from odoo import fields, models, _

from .migration_spec import JAW_MAP, PRIORITY_MAP, teeth_from_quadrants

_logger = logging.getLogger(__name__)


class MigrationBackend(models.Model):
    _inherit = 'migration.backend'

    txn_from_date = fields.Date(
        'Migrate Documents From',
        help="Documents dated on or after this date are migrated as documents.\n\n"
             "Set it to the day AFTER the opening cut (opening_date), so that no "
             "value is carried twice: opening_date 31-Mar + txn_from_date 1-Apr "
             "means the opening holds every balance up to 31-Mar and 1-Apr onwards "
             "arrives as real orders, invoices and entries.")
    txn_log = fields.Text('Last Transaction Run Log', readonly=True)
    inventory_as_of = fields.Date(
        'On-hand As Of',
        help="Date the on-hand quantities are computed as of. Empty = the opening "
             "date. Set it to the dump date once the transfers are in, so stock "
             "shows today's position rather than the opening one.")
    txn_only_new = fields.Boolean(
        'Resume Mode', default=False,
        help="Skip documents that already carry a mapping instead of rewriting "
             "them. Turn this on to re-run after fixing a failure: the run then "
             "costs only the documents that are still missing.")
    txn_limit = fields.Integer(
        'Smoke-test Limit', default=0,
        help="Migrate at most this many documents per phase. 0 = all. Use a small "
             "number to prove a run end-to-end before committing to the full set.")

    # Commit every N documents: a 20k-invoice run must not depend on one
    # transaction surviving to the end, and the map rows are what make a
    # resumed run idempotent.
    _TXN_BATCH = 200

    # ------------------------------------------------------------------ helpers
    def _txn_prune_map(self, models_):
        """Drop mapping rows whose target record no longer exists.

        A row can be left pointing at nothing when a create succeeds and something
        later in the same savepoint fails: the database rolls back, the Python list
        does not. Resume mode then treats the document as already migrated and skips
        it forever. The batch bookkeeping now unwinds on failure, but earlier runs
        (and anything deleted by hand since) can still leave orphans, so every run
        sweeps them first.
        """
        Map = self.env['migration.map'].sudo()
        pruned = 0
        for model in models_:
            if model not in self.env:
                continue
            rows = Map.search([('dst_model', '=', model)])
            if not rows:
                continue
            alive = set(self.env[model].sudo().with_context(active_test=False)
                        .browse(rows.mapped('dst_id')).exists().ids)
            dead = rows.filtered(lambda r: r.dst_id not in alive)
            if dead:
                pruned += len(dead)
                dead.unlink()
        return pruned

    def _txn_preload(self, cache, models_):
        """Bulk-load migration.map for the given models into the resolve cache.

        ``_resolve`` does one search per unmapped id. At ~250k resolutions per run
        that alone is the difference between minutes and hours, so the whole map is
        read up front in a single query per model.
        """
        self.env.cr.execute("""
            SELECT dst_model, src_id, dst_id FROM migration_map
             WHERE dst_model IN %s
        """, (tuple(models_),))
        for model, o10, o19 in self.env.cr.fetchall():
            cache.setdefault(model, {})[o10] = o19
        return cache

    def _txn_put_many(self, cache, model, pairs):
        """Record many Odoo17->Odoo19 mappings at once.

        Upsert, not insert. migration.map is unique on (dst_model, src_id), and
        a re-run legitimately produces a NEW v19 id for an old key — the sale-order
        update path deletes and re-creates its lines, for one. A plain create would
        either raise (aborting the whole phase, since this runs outside the per-row
        savepoint) or leave the map pointing at an unlinked record.
        """
        if not pairs:
            return
        Map = self.env['migration.map'].sudo()
        pairs = dict(pairs)                      # last write wins within a batch
        existing = Map.search([('dst_model', '=', model), ('src_id', 'in', list(pairs))])
        for rec in existing:
            new_id = pairs.pop(rec.src_id, None)
            if new_id and rec.dst_id != new_id:
                rec.dst_id = new_id
        if pairs:
            Map.create([{'dst_model': model, 'src_id': o10, 'dst_id': o19}
                        for o10, o19 in pairs.items()])
        mc = cache.setdefault(model, {})
        mc.update(pairs)
        for rec in existing:
            mc[rec.src_id] = rec.dst_id

    @staticmethod
    def _group(rows, key):
        out = {}
        for r in rows:
            out.setdefault(r[key], []).append(r)
        return out

    @staticmethod
    def _rel(cur, table, this_col, other_col, where=''):
        """Load an m2m relation table into {this_id: [other_id, ...]}."""
        out = {}
        try:
            cur.execute('SELECT "%s","%s" FROM "%s" %s' % (this_col, other_col, table, where))
            # Rows are dicts (RealDictCursor) — index by column name, never unpack.
            for r in cur.fetchall():
                out.setdefault(r[this_col], []).append(r[other_col])
        except psycopg2.Error:
            pass
        return out

    def _txn_company(self, cache, row):
        # No fallback to '_main_company_': that key holds an Odoo 17 company id,
        # which would be meaningless — and silently wrong — as a v19 company_id.
        return self._resolve(cache, 'res.company', row.get('company_id')) \
            or self.env.company.id

    def _txn_warehouse(self, cache, company_id):
        wh = cache.setdefault('_wh_', {})
        if company_id not in wh:
            rec = self.env['stock.warehouse'].sudo().search(
                [('company_id', '=', company_id)], limit=1)
            wh[company_id] = rec.id or False
        return wh[company_id]

    def _txn_dates(self):
        """(from_date, sql_from_date) — falls back to the day after the opening."""
        self.ensure_one()
        d = self.txn_from_date
        if not d and self.opening_date:
            d = fields.Date.add(self.opening_date, days=1)
        if not d:
            from odoo.exceptions import UserError
            raise UserError(_("Set 'Migrate Documents From' before migrating transactions."))
        return d

    def _txn_tail(self):
        """SQL tail applying the smoke-test limit (empty when unlimited)."""
        return ' LIMIT %d' % self.txn_limit if self.txn_limit else ''

    # ------------------------------------------------------------------ re-run safety
    # Context key that lets a run rewrite invoices and journal entries it already
    # migrated. Off by default: see _txn_keep_move.
    _TXN_REWRITE_CTX = 'migration_rewrite_documents'

    def _txn_linked_lines(self, lines):
        """The order lines something downstream points at.

        An invoice line (sale_line_ids / purchase_line_id), a stock move
        (sale_line_id / purchase_line_id) or a manufacturing order (sale_line_id)
        loses its link the moment the line is deleted, and nothing puts it back.
        """
        if not lines:
            return lines
        linked = lines.filtered(
            lambda l: l.invoice_lines or ('move_ids' in l._fields and l.move_ids))
        MO = self.env['mrp.production']
        if lines._name == 'sale.order.line' and 'sale_line_id' in MO._fields:
            linked |= MO.search([('sale_line_id', 'in', lines.ids)]).sale_line_id
        return linked

    def _txn_adopt_lines(self, lines, line_vals):
        """Key order lines created before they carried an ``x_src_id``.

        Only when the pairing is certain: the same number of lines, in the order
        they were created from the source, each on the same product. Anything
        less and the lines are left unkeyed — and so untouched by the merge.
        """
        ordered = lines.sorted(lambda l: (l.sequence, l.id))
        if len(ordered) != len(line_vals) or any(
                line.product_id.id != cmd[2].get('product_id') or not cmd[2].get('x_src_id')
                for line, cmd in zip(ordered, line_vals)):
            return False
        for line, cmd in zip(ordered, line_vals):
            line.x_src_id = cmd[2]['x_src_id']
        return True

    def _txn_merge_lines(self, lines, line_vals):
        """x2many commands that bring existing order lines in line with the source.

        A re-run used to delete every line and create it again, which cut every
        invoice line, stock move and MO off the sale/purchase line it came from.
        Now a line matched by ``x_src_id`` is updated in place and keeps its id;
        a source line with no match is created. An existing keyed line the source
        no longer has is removed only when nothing points at it. Unkeyed lines —
        added in Odoo 19 after the cut-over — are never touched.
        """
        by_key = {}
        for line in lines.sorted('id'):
            if line.x_src_id and line.x_src_id not in by_key:
                by_key[line.x_src_id] = line
        commands, matched = [], set()
        for cmd in line_vals:
            lv = cmd[2]
            line = by_key.get(lv.get('x_src_id'))
            if line and line.id not in matched:
                matched.add(line.id)
                commands.append((1, line.id, lv))
            else:
                commands.append((0, 0, lv))
        stale = lines.filtered(lambda l: l.x_src_id and l.id not in matched)
        commands += [(2, line.id) for line in stale - self._txn_linked_lines(stale)]
        return commands

    def _txn_rewrite_lines(self, order, vals, line_vals):
        """Write an existing order's header and merge its lines (see above).

        Returns False when the order's lines could not be paired with the source
        (unkeyed and not adoptable): the header is still updated, the lines are
        left exactly as they are rather than doubled.
        """
        lines = order.order_line
        if lines and not any(lines.mapped('x_src_id')) \
                and not self._txn_adopt_lines(lines, line_vals):
            order.write(vals)
            return False
        order.write(dict(vals, order_line=self._txn_merge_lines(order.order_line, line_vals)))
        return True

    def _txn_keep_move(self, move_id, counts, row_id):
        """True when an already-migrated invoice or journal entry is left alone.

        Rewriting one replaces its lines, and in Odoo 19 deleting a journal item
        first unreconciles every payment matched against it and cascades into the
        bank clearance ticks — after go-live that is real work thrown away. So an
        existing entry is kept unless the run is explicitly asked to rewrite
        (context ``migration_rewrite_documents``), and even then an entry whose
        lines are reconciled or cleared is refused.
        """
        if self.txn_only_new or not self.env.context.get(self._TXN_REWRITE_CTX):
            counts['kept'] += 1
            return True
        lines = self.env['account.move'].browse(move_id).line_ids
        busy = lines.filtered(
            lambda l: l.matched_debit_ids or l.matched_credit_ids or l.full_reconcile_id)
        if not busy and 'bank.clearance' in self.env:
            busy = self.env['bank.clearance'].sudo().search_count(
                [('move_line_id', 'in', lines.ids)], limit=1)
        if busy:
            counts['protected'] += 1
            _logger.warning("txn account.move id=%s: not rewritten, its lines are "
                            "reconciled or bank-cleared", row_id)
            return True
        return False

    def _txn_picking_type(self, cache, company_id, code):
        """v19 operation type of the given code for a company.

        The v17 types are the six stock defaults per warehouse, so they are matched
        by (company, code) rather than migrated — v19 creates its own set with its
        own sequences, and adopting those keeps new documents numbering correctly.
        """
        key = (company_id, code)
        types = cache.setdefault('_pt_', {})
        if key not in types:
            rec = self.env['stock.picking.type'].sudo().search(
                [('company_id', '=', company_id), ('code', '=', code)], limit=1)
            types[key] = rec.id or False
        return types[key]

    # ------------------------------------------------------------------ shared records
    def _txn_share_records(self, cur, cache, stats):
        """Share the records that v17 used across companies, before any document
        references them.

        v17 let one company sell another company's product and bill another
        company's doctor; v19 refuses outright ("no company crossover is allowed")
        and the whole document is lost. In the 2026-08-08 source this costs 314
        sale orders, 300 invoices and 65 journal entries — the same defect class
        the runbook records for BoMs in §7.3.

        Clearing ``company_id`` makes a record shared, which is what a single
        product catalogue and a single doctor list used by both companies should
        have been all along. Only records actually referenced across companies are
        touched, never the whole catalogue.
        """
        self._txn_share_products(cur, cache, stats)
        self._txn_share_partners(cur, cache, stats)

    def _txn_share_products(self, cur, cache, stats):
        d = self._txn_dates()
        cur.execute("""
            SELECT DISTINCT pt.id AS tmpl_id
              FROM sale_order_line sol
              JOIN sale_order so ON so.id = sol.order_id
              JOIN product_product pp ON pp.id = sol.product_id
              JOIN product_template pt ON pt.id = pp.product_tmpl_id
             WHERE so.date_order >= %(d)s
               AND pt.company_id IS NOT NULL AND pt.company_id <> so.company_id
             UNION
            SELECT DISTINCT pt.id
              FROM account_move_line il
              JOIN account_move i ON i.id = il.move_id
              JOIN product_product pp ON pp.id = il.product_id
              JOIN product_template pt ON pt.id = pp.product_tmpl_id
             WHERE i.date >= %(d)s
               AND pt.company_id IS NOT NULL AND pt.company_id <> i.company_id
        """, {'d': d})
        tmpl_ids = [self._resolve(cache, 'product.template', r['tmpl_id'])
                    for r in cur.fetchall()]
        tmpl_ids = [t for t in tmpl_ids if t]
        if not tmpl_ids:
            stats.append("Shared Products        none needed")
            return
        shared = self.env['product.template'].sudo().with_context(
            active_test=False).browse(tmpl_ids).filtered('company_id')
        n = len(shared)
        if n:
            shared.write({'company_id': False})
            self.env.cr.commit()
        stats.append("Shared Products        %d product(s) made company-shared "
                     "(v17 cross-company usage)" % n)

    def _txn_share_partners(self, cur, cache, stats):
        """Same treatment for contacts.

        Every v17 partner carries a company_id, so v19 scopes all 7055 of them to
        one company — while the lab plainly bills the same doctors from both. A
        contact is global in Odoo unless deliberately restricted, so the ones used
        across companies are unscoped here.
        """
        d = self._txn_dates()
        cur.execute("""
            SELECT DISTINCT p.id
              FROM sale_order so JOIN res_partner p ON p.id = so.partner_id
             WHERE so.date_order >= %(d)s
               AND p.company_id IS NOT NULL AND p.company_id <> so.company_id
             UNION
            SELECT DISTINCT p.id
              FROM account_move_line aml
              JOIN account_move m ON m.id = aml.move_id AND m.state = 'posted'
              JOIN res_partner p ON p.id = aml.partner_id
             WHERE m.date >= %(d)s
               AND p.company_id IS NOT NULL AND p.company_id <> m.company_id
        """, {'d': d})
        ids = [self._resolve(cache, 'res.partner', r['id']) for r in cur.fetchall()]
        ids = [i for i in ids if i]
        if not ids:
            stats.append("Shared Partners        none needed")
            return
        Partner = self.env['res.partner'].sudo().with_context(active_test=False)
        # A child contact inherits the parent's company, so unscope the whole
        # commercial entity rather than leaving a company-scoped parent behind.
        recs = Partner.browse(ids)
        recs |= recs.mapped('commercial_partner_id')
        shared = recs.filtered('company_id')
        n = len(shared)
        if n:
            shared.write({'company_id': False})
            self.env.cr.commit()
        stats.append("Shared Partners        %d contact(s) made company-shared "
                     "(v17 cross-company usage)" % n)

    # ------------------------------------------------------------------ sale orders
    # v17 state -> v19 state. sale_custom drops 'done' from the selection, so a
    # v17 'done' order lands as a plain confirmed order.
    _SO_STATE = {'draft': 'draft', 'sent': 'sent', 'sale': 'sale',
                 'done': 'sale', 'cancel': 'cancel'}

    _SO_SCALARS = (
        'client_order_ref', 'origin', 'note', 'picking_policy', 'validity_date',
        'commitment_date', 'reference',
    )

    _SOL_SCALARS = ('name', 'sequence', 'product_uom_qty', 'price_unit', 'discount',
                    'customer_lead', 'display_type')

    @staticmethod
    def _dental_patient(row, lines):
        """The patient of an order: dental_sale kept it on the LINE (97% filled)
        and only sometimes on the order (2%). One order is one patient in 99.7% of
        cases; the rare multi-patient order lists them all."""
        names = []
        for value in [row.get('patient_name')] + [ln.get('patient_name') for ln in lines]:
            value = ' '.join((value or '').split())
            if value and value.upper() not in [n.upper() for n in names]:
                names.append(value)
        return ' / '.join(names)

    @staticmethod
    def _dental_line_extras(ln):
        """jaw -> ul, quadrants -> teeth, for a sale or invoice line."""
        extras = {}
        ul = JAW_MAP.get(ln.get('jaw'))
        if ul:
            extras['ul'] = ul
        teeth = teeth_from_quadrants(ln)
        if teeth:
            extras['teeth'] = teeth
        return extras

    def _txn_sale_orders(self, cur, cache, stats):
        d = self._txn_dates()
        SO = self.env['sale.order'].sudo().with_context(
            active_test=False, mail_create_nolog=True, mail_notrack=True,
            tracking_disable=True)
        cur.execute("SELECT * FROM sale_order WHERE date_order >= %s ORDER BY id"
                    + self._txn_tail(), (d,))
        orders = cur.fetchall()
        if not orders:
            stats.append("Sale Orders            nothing to migrate")
            return
        ids = tuple(o['id'] for o in orders)
        cur.execute("SELECT * FROM sale_order_line WHERE order_id IN %s ORDER BY order_id, sequence, id",
                    (ids,))
        lines_by_order = self._group(cur.fetchall(), 'order_id')
        line_taxes = self._rel(cur, 'account_tax_sale_order_line_rel',
                               'sale_order_line_id', 'account_tax_id')
        technicians = self._rel(cur, 'res_partner_sale_order_rel',
                                'sale_order_id', 'res_partner_id')
        cur.execute("SELECT id, name FROM res_material")
        materials = {r['id']: r['name'] for r in cur.fetchall()}

        counts = {'created': 0, 'updated': 0, 'failed': 0, 'skipped': 0, 'lines_kept': 0}
        pending, batch, processed = [], [], 0
        for row in orders:
            processed += 1
            # A savepoint rolls the DATABASE back, but not these Python lists —
            # without the mark a rolled-back create still leaves a migration.map row
            # pointing at an id that does not exist, and resume mode then skips that
            # document forever.
            mark, mark_pending = len(batch), len(pending)
            try:
                with self.env.cr.savepoint():
                    partner = self._resolve(cache, 'res.partner', row.get('partner_id'))
                    if not partner:
                        counts['skipped'] += 1
                        continue
                    cid = self._txn_company(cache, row)
                    existing = self._resolve(cache, 'sale.order', row['id'])
                    if existing and self.txn_only_new:
                        counts['skipped'] += 1
                        continue
                    src_lines = lines_by_order.get(row['id'], [])
                    vals = {f: row[f] for f in self._SO_SCALARS if row.get(f) is not None}
                    vals.update({
                        'name': row.get('name') or '/',
                        'partner_id': partner,
                        'date_order': row.get('date_order'),
                        'company_id': cid,
                        'x_src_id': row['id'],
                        # dental_sale -> the suite
                        'patient': self._dental_patient(row, src_lines),
                        'priority': PRIORITY_MAP.get(row.get('sale_priority'), 'normal'),
                    })
                    material = materials.get(row.get('material_name'))
                    if material:
                        vals['instruction'] = 'Material: %s' % material.strip()
                    for dst, (src, model) in {
                            'partner_invoice_id': ('partner_invoice_id', 'res.partner'),
                            'partner_shipping_id': ('partner_shipping_id', 'res.partner'),
                            'user_id': ('user_id', 'res.users'),
                            'team_id': ('team_id', 'crm.team'),
                            'payment_term_id': ('payment_term_id', 'account.payment.term'),
                            'fiscal_position_id': ('fiscal_position_id', 'account.fiscal.position'),
                            # who typed the order in is the suite's "registered by"
                            'register_person_id': ('create_uid', 'res.users'),
                    }.items():
                        val = self._resolve(cache, model, row.get(src))
                        if val:
                            vals[dst] = val
                    techs = [t for t in (self._resolve(cache, 'res.partner', p)
                                         for p in technicians.get(row['id'], [])) if t]
                    vals['technician_ids'] = [(6, 0, techs)]
                    wh = self._txn_warehouse(cache, cid)
                    if wh:
                        vals['warehouse_id'] = wh
                    colour = self._resolve(cache, 'product.colour', row.get('shade_name'))
                    line_vals = []
                    for ln in src_lines:
                        lv = {f: ln[f] for f in self._SOL_SCALARS if ln.get(f) is not None}
                        if ln.get('display_type') in ('line_section', 'line_note'):
                            # a heading or a note between the works: no product
                            lv['x_src_id'] = ln['id']
                            line_vals.append((0, 0, self._valid(self.env['sale.order.line'], lv)))
                            continue
                        product = self._resolve(cache, 'product.product', ln.get('product_id'))
                        if not product:
                            continue
                        lv.pop('display_type', None)
                        lv['product_id'] = product
                        uom = self._resolve(cache, 'uom.uom', ln.get('product_uom'))
                        if uom:
                            lv['product_uom_id'] = uom          # v19 rename
                        if colour:
                            lv['color_scheme'] = colour
                        lv.update(self._dental_line_extras(ln))
                        # ALWAYS set tax_ids, empty included. v19 fills a line's taxes
                        # from product.taxes_id when the key is absent, and the master
                        # sync gave the products their v17 taxes — which would add 5%
                        # GST to lines the source deliberately booked tax-free.
                        lv['tax_ids'] = [(6, 0, [
                            t for t in (self._resolve(cache, 'account.tax', t)
                                        for t in line_taxes.get(ln['id'], [])) if t])]
                        lv['x_src_id'] = ln['id']
                        line_vals.append((0, 0, self._valid(self.env['sale.order.line'], lv)))
                    vals = self._valid(self.env['sale.order'], vals)
                    # sale_custom.create() renames any order created with
                    # is_edit_number set to "Old Work"; keep the v17 number and
                    # restore the flag right after.
                    edit_number = vals.pop('is_edit_number', None)
                    if existing:
                        order = SO.browse(existing)
                        # A re-sync drops back to draft — so neither a changed
                        # quantity re-launches procurement nor v19 refuses to drop a
                        # stale line — merges the lines in place, and the state write
                        # below puts the order back where the source has it.
                        order.write({'state': 'draft'})
                        if not self._txn_rewrite_lines(order, vals, line_vals):
                            counts['lines_kept'] += 1
                        counts['updated'] += 1
                    else:
                        order = SO.with_company(cid).create(dict(vals, order_line=line_vals))
                        batch.append((row['id'], order.id))
                        counts['created'] += 1
                    post = {'state': self._SO_STATE.get(row.get('state'), 'draft')}
                    if edit_number:
                        post['is_edit_number'] = True
                    if 'verification_state' in order._fields:
                        # migrated confirmed work is, by definition, already checked
                        post['verification_state'] = \
                            'verified' if post['state'] == 'sale' else 'draft'
                    order.write(post)
                    # Each line already carries its own x_src_id, so read the
                    # pairing back off the records rather than zipping two lists
                    # that only happen to be in the same order.
                    for rec in order.order_line:
                        if rec.x_src_id:
                            pending.append((rec.x_src_id, rec.id))
            except Exception as e:
                counts['failed'] += 1
                # the savepoint undid the create; undo the bookkeeping too
                if len(batch) > mark:
                    del batch[mark:]
                    counts['created'] -= 1
                if len(pending) > mark_pending:
                    del pending[mark_pending:]
                _logger.warning("txn sale.order id=%s: %s", row['id'], e)
            if processed % self._TXN_BATCH == 0:
                self._txn_put_many(cache, 'sale.order', batch)
                # unfiltered: a line the update path had to create gets a new id
                # under an existing key (_txn_put_many upserts)
                self._txn_put_many(cache, 'sale.order.line', pending)
                batch, pending = [], []
                self.env.cr.commit()
        self._txn_put_many(cache, 'sale.order', batch)
        self._txn_put_many(cache, 'sale.order.line', pending)
        self.env.cr.commit()
        stats.append("Sale Orders            created=%d updated=%d skipped=%d failed=%d "
                     "lines-left-unpaired=%d"
                     % (counts['created'], counts['updated'], counts['skipped'], counts['failed'],
                        counts['lines_kept']))

    # ------------------------------------------------------------------ invoices
    _INV_TYPE = {'out_invoice': 'out_invoice', 'in_invoice': 'in_invoice',
                 'out_refund': 'out_refund', 'in_refund': 'in_refund'}
    # v17 invoices are posted here when posted there; matching against payments
    # is replayed afterwards by _txn_reconcile.
    _INV_POST = {'posted'}

    def _txn_journal_for(self, cache, company_id, journal_id, move_type):
        """The journal to post `move_type` in: the mapped one when its type fits,
        else the company's default journal of the fitting type."""
        wanted = 'purchase' if move_type in ('in_invoice', 'in_refund') else 'sale'
        Journal = self.env['account.journal'].sudo()
        if Journal.browse(journal_id).type == wanted:
            return journal_id
        key = ('_journal_', company_id, wanted)
        if key not in cache:
            # the chart's own journal (BILL / INV) comes before an opening one (OBP)
            cache[key] = Journal.search([('company_id', '=', company_id),
                                         ('type', '=', wanted)], order='id', limit=1).id
        return cache[key] or journal_id

    def _txn_cash_rounding_map(self, cur):
        """{source cash rounding id: target id}, adopted rather than copied.

        Matched on what a rule DOES - the same step, the same method, the same
        strategy - and not on its name: the lab's is called "Round Off" in the
        source and "Half Up" here, and both round to the rupee, half up, as an
        extra line. Creating one instead is not an option worth taking: in Odoo
        19 a rule needs a profit and a loss account that the source's version of
        the model does not have, so a made-up rule would post the difference to a
        made-up account.
        """
        out = {}
        cur.execute("SELECT id, rounding, rounding_method, strategy FROM account_cash_rounding")
        candidates = self.env['account.cash.rounding'].sudo().search([])
        for rule in cur.fetchall():
            match = candidates.filtered(
                lambda c: c.strategy == rule['strategy']
                and c.rounding_method == rule['rounding_method']
                and abs((c.rounding or 0) - (rule['rounding'] or 0)) < 1e-6)
            if match:
                out[rule['id']] = match[0].id
            else:
                _logger.warning(
                    "no cash rounding rule here matches the source's %s %s %s; "
                    "those invoices keep unrounded totals",
                    rule['rounding'], rule['rounding_method'], rule['strategy'])
        return out

    def _txn_invoices(self, cur, cache, stats):
        d = self._txn_dates()
        Move = self.env['account.move'].sudo().with_context(
            active_test=False, mail_create_nolog=True, mail_notrack=True,
            tracking_disable=True)
        cur.execute("""SELECT * FROM account_move
                        WHERE move_type IN ('out_invoice', 'in_invoice', 'out_refund', 'in_refund')
                          AND COALESCE(invoice_date, date) >= %s
                        ORDER BY id""" + self._txn_tail(), (d,))
        invoices = cur.fetchall()
        if not invoices:
            stats.append("Invoices               nothing to migrate")
            return
        ids = tuple(i['id'] for i in invoices)
        # Only the lines a person typed: products, sections and notes. Tax and
        # receivable lines are rebuilt by posting, exactly as the source built them.
        cur.execute("""SELECT * FROM account_move_line
                        WHERE move_id IN %s
                          AND display_type IN ('product', 'line_section', 'line_note')
                        ORDER BY move_id, sequence, id""", (ids,))
        lines_by_inv = self._group(cur.fetchall(), 'move_id')
        line_taxes = self._rel(cur, 'account_move_line_account_tax_rel',
                               'account_move_line_id', 'account_tax_id')
        sale_lines = self._rel(cur, 'sale_order_line_invoice_rel',
                               'invoice_line_id', 'order_line_id')
        roundings = self._txn_cash_rounding_map(cur)

        # The master sync posts a zero-amount "numbering anchor" per journal, named
        # after the LAST v17 invoice number, so that new invoices continue the v17
        # series. That was written for a balance-forward cut-over where the invoices
        # themselves never arrive. They do now — and the anchor sits on exactly the
        # number the last real invoice needs, so it loses account_move_unique_name.
        # The real document is the better anchor: v19 reads the next number off the
        # highest name in the journal either way. Drop the placeholder when its
        # number is claimed.
        anchors = {}
        for m in Move.search([('ref', '=', self._SEQ_ANCHOR_REF)]):
            anchors[(m.journal_id.id, m.name)] = m

        counts = {'created': 0, 'updated': 0, 'posted': 0, 'failed': 0, 'skipped': 0,
                  'kept': 0, 'protected': 0}
        batch, processed = [], 0
        for row in invoices:
            processed += 1
            # A savepoint rolls the DATABASE back, but not these Python lists —
            # without the mark a rolled-back create still leaves a migration.map row
            # pointing at an id that does not exist, and resume mode then skips that
            # document forever.
            mark = len(batch)
            try:
                with self.env.cr.savepoint():
                    partner = self._resolve(cache, 'res.partner', row.get('partner_id'))
                    journal = self._resolve(cache, 'account.journal', row.get('journal_id'))
                    move_type = self._INV_TYPE.get(row.get('move_type'))
                    if not (partner and journal and move_type):
                        counts['skipped'] += 1
                        continue
                    cid = self._txn_company(cache, row)
                    # Odoo 17 let the lab book 13 vendor bills and refunds in the B2B
                    # SALE journal; Odoo 19 refuses a purchase document there. Such a
                    # document goes to the company's default journal of the right
                    # type, keeping its own number.
                    journal = self._txn_journal_for(cache, cid, journal, move_type)
                    existing = self._resolve(cache, 'account.move', row['id'])
                    if existing and self._txn_keep_move(existing, counts, row['id']):
                        continue
                    vals = {
                        'move_type': move_type,
                        'partner_id': partner,
                        'journal_id': journal,
                        'company_id': cid,
                        'invoice_date': row.get('invoice_date'),
                        'date': row.get('date') or row.get('invoice_date'),
                        'invoice_date_due': row.get('invoice_date_due'),
                        'invoice_origin': row.get('invoice_origin'),
                        'ref': row.get('ref'),
                        'payment_reference': row.get('payment_reference'),
                        'narration': row.get('narration'),
                        'l10n_in_gst_treatment': row.get('l10n_in_gst_treatment'),
                        'x_src_id': row['id'],
                    }
                    # Keep the v17 document number. _seed_invoice_numbers() already
                    # points the v19 sequence past the v17 maximum, so new invoices
                    # continue the same series instead of colliding with these.
                    number = row.get('name')
                    if number and number != '/':
                        vals['name'] = number
                        anchor = anchors.pop((journal, number), None)
                        if anchor:
                            if anchor.state == 'posted':
                                anchor.button_draft()
                            anchor.unlink()
                    # ROUNDED TO THE RUPEE. The lab invoices through Odoo's cash
                    # rounding rule (1.00, half up, as an extra line), on 48,040 of
                    # its posted documents. Without it every one of those invoices
                    # comes out a few paise off its own number, and a payment for
                    # the rupee amount leaves a residual of 4 paise - which reads
                    # on the screen as "Partially Paid", on 1,284 invoices that
                    # were paid in full years ago.
                    rounding = roundings.get(row.get('invoice_cash_rounding_id'))
                    if rounding:
                        vals['invoice_cash_rounding_id'] = rounding
                    for dst, (src, model) in {
                            'invoice_payment_term_id': ('invoice_payment_term_id', 'account.payment.term'),
                            'invoice_user_id': ('invoice_user_id', 'res.users'),
                            'team_id': ('team_id', 'crm.team'),
                            'fiscal_position_id': ('fiscal_position_id', 'account.fiscal.position'),
                            'partner_shipping_id': ('partner_shipping_id', 'res.partner'),
                    }.items():
                        val = self._resolve(cache, model, row.get(src))
                        if val:
                            vals[dst] = val
                    line_vals = []
                    for ln in lines_by_inv.get(row['id'], []):
                        if ln.get('display_type') in ('line_section', 'line_note'):
                            line_vals.append((0, 0, {
                                'display_type': ln['display_type'],
                                'name': ln.get('name') or '/',
                                'sequence': ln.get('sequence') or 10,
                                'x_src_id': ln['id'],
                            }))
                            continue
                        lv = {
                            'name': ln.get('name') or '/',
                            'quantity': ln.get('quantity') or 0.0,
                            'price_unit': ln.get('price_unit') or 0.0,
                            'discount': ln.get('discount') or 0.0,
                            'sequence': ln.get('sequence') or 10,
                            'patient': ' '.join((ln.get('patient_name') or '').split()) or False,
                            'x_src_id': ln['id'],
                        }
                        lv.update(self._dental_line_extras(ln))
                        product = self._resolve(cache, 'product.product', ln.get('product_id'))
                        if product:
                            lv['product_id'] = product
                        account = self._resolve(cache, 'account.account', ln.get('account_id'))
                        if account:
                            lv['account_id'] = account
                        uom = self._resolve(cache, 'uom.uom', ln.get('product_uom_id'))
                        if uom:
                            lv['product_uom_id'] = uom
                        # the order line(s) this invoice line bills: what makes the
                        # order read "invoiced" and the invoice open from the order
                        sol = [s for s in (self._resolve(cache, 'sale.order.line', s)
                                           for s in sale_lines.get(ln['id'], [])) if s]
                        if sol:
                            lv['sale_line_ids'] = [(6, 0, sol)]
                        # and a bill line onto its purchase line, for the same reasons
                        pol = self._resolve(cache, 'purchase.order.line', ln.get('purchase_line_id'))
                        if pol:
                            lv['purchase_line_id'] = pol
                        # See the sale-order note: absent tax_ids means "use the
                        # product default", and 20,481 of the source invoices are
                        # deliberately tax-free.
                        lv['tax_ids'] = [(6, 0, [
                            t for t in (self._resolve(cache, 'account.tax', t)
                                        for t in line_taxes.get(ln['id'], [])) if t])]
                        line_vals.append((0, 0, self._valid(self.env['account.move.line'], lv)))
                    if not line_vals:
                        counts['skipped'] += 1
                        continue
                    vals = self._valid(self.env['account.move'], vals)
                    if existing:
                        move = Move.browse(existing)
                        if move.state == 'posted':
                            move.button_draft()
                        move.write(dict(vals, invoice_line_ids=[(5, 0, 0)] + line_vals))
                        counts['updated'] += 1
                    else:
                        move = Move.with_company(cid).create(
                            dict(vals, invoice_line_ids=line_vals))
                        batch.append((row['id'], move.id))
                        counts['created'] += 1
                    if row.get('state') in self._INV_POST:
                        move.action_post()
                        counts['posted'] += 1
                    elif row.get('state') == 'cancel':
                        move.button_cancel()
            except Exception as e:
                counts['failed'] += 1
                # the savepoint undid the create; undo the bookkeeping too
                if len(batch) > mark:
                    del batch[mark:]
                    counts['created'] -= 1
                _logger.warning("txn invoice id=%s: %s", row['id'], e)
            if processed % self._TXN_BATCH == 0:
                self._txn_put_many(cache, 'account.move', batch)
                batch = []
                self.env.cr.commit()
        self._txn_put_many(cache, 'account.move', batch)
        self.env.cr.commit()
        stats.append("Invoices               created=%d updated=%d posted=%d skipped=%d failed=%d "
                     "kept=%d protected=%d"
                     % (counts['created'], counts['updated'], counts['posted'],
                        counts['skipped'], counts['failed'], counts['kept'], counts['protected']))

    # ------------------------------------------------------------------ journal entries
    def _txn_journal_entries(self, cur, cache, stats):
        """Every posted v17 move that is NOT an invoice's own move.

        Invoices re-post in v19 and generate their entry there, so importing the
        v17 invoice moves as well would double every sale. What is left is the
        cash side of the business — receipts, bank entries, miscellaneous — and
        those are copied line for line, debit and credit exactly as booked, which
        is the only way the v19 trial balance can tie back to v17.
        """
        d = self._txn_dates()
        Move = self.env['account.move'].sudo().with_context(
            mail_create_nolog=True, mail_notrack=True, tracking_disable=True)
        cur.execute("""
            SELECT m.* FROM account_move m
             WHERE m.date >= %s AND m.state = 'posted' AND m.move_type = 'entry'
             ORDER BY m.id
        """ + self._txn_tail(), (d,))
        moves = cur.fetchall()
        if not moves:
            stats.append("Journal Entries        nothing to migrate")
            return
        ids = tuple(m['id'] for m in moves)
        cur.execute("""
            SELECT id, move_id, account_id, partner_id, name, ref, debit, credit,
                   date_maturity, product_id, quantity
              FROM account_move_line WHERE move_id IN %s ORDER BY move_id, id
        """, (ids,))
        lines_by_move = self._group(cur.fetchall(), 'move_id')

        counts = {'created': 0, 'updated': 0, 'failed': 0, 'skipped': 0,
                  'kept': 0, 'protected': 0}
        batch, processed = [], 0
        for row in moves:
            processed += 1
            # A savepoint rolls the DATABASE back, but not these Python lists —
            # without the mark a rolled-back create still leaves a migration.map row
            # pointing at an id that does not exist, and resume mode then skips that
            # document forever.
            mark = len(batch)
            try:
                with self.env.cr.savepoint():
                    journal = self._resolve(cache, 'account.journal', row.get('journal_id'))
                    if not journal:
                        counts['skipped'] += 1
                        continue
                    cid = self._txn_company(cache, row)
                    line_vals, ok = [], True
                    for ln in lines_by_move.get(row['id'], []):
                        account = self._resolve(cache, 'account.account', ln.get('account_id'))
                        if not account:
                            # An unmapped account would silently unbalance the
                            # entry; drop the whole move instead and report it.
                            ok = False
                            break
                        lv = {
                            'account_id': account,
                            'name': ln.get('name') or '/',
                            'debit': ln.get('debit') or 0.0,
                            'credit': ln.get('credit') or 0.0,
                            'date_maturity': ln.get('date_maturity'),
                            # keyed, so the reconciliation phase can find this line
                            'x_src_id': ln['id'],
                        }
                        partner = self._resolve(cache, 'res.partner', ln.get('partner_id'))
                        if partner:
                            lv['partner_id'] = partner
                        product = self._resolve(cache, 'product.product', ln.get('product_id'))
                        if product:
                            lv['product_id'] = product
                        line_vals.append((0, 0, self._valid(self.env['account.move.line'], lv)))
                    if not ok or not line_vals:
                        counts['skipped'] += 1
                        continue
                    vals = {
                        'journal_id': journal, 'company_id': cid,
                        'date': row.get('date'), 'ref': row.get('ref'),
                        'narration': row.get('narration'),
                        'x_src_id': row['id'],
                    }
                    if row.get('name') and row['name'] != '/':
                        vals['name'] = row['name']
                    partner = self._resolve(cache, 'res.partner', row.get('partner_id'))
                    if partner:
                        vals['partner_id'] = partner
                    vals = self._valid(self.env['account.move'], vals)
                    existing = self._resolve(cache, 'account.move', row['id'])
                    if existing and self._txn_keep_move(existing, counts, row['id']):
                        continue
                    if existing:
                        move = Move.browse(existing)
                        if move.state == 'posted':
                            move.button_draft()
                        move.write(dict(vals, line_ids=[(5, 0, 0)] + line_vals))
                        counts['updated'] += 1
                    else:
                        move = Move.with_company(cid).create(dict(vals, line_ids=line_vals))
                        batch.append((row['id'], move.id))
                        counts['created'] += 1
                    move.action_post()
            except Exception as e:
                counts['failed'] += 1
                # the savepoint undid the create; undo the bookkeeping too
                if len(batch) > mark:
                    del batch[mark:]
                    counts['created'] -= 1
                _logger.warning("txn account.move id=%s: %s", row['id'], e)
            if processed % self._TXN_BATCH == 0:
                self._txn_put_many(cache, 'account.move', batch)
                batch = []
                self.env.cr.commit()
        self._txn_put_many(cache, 'account.move', batch)
        self.env.cr.commit()
        stats.append("Journal Entries        created=%d updated=%d skipped=%d failed=%d "
                     "kept=%d protected=%d"
                     % (counts['created'], counts['updated'], counts['skipped'], counts['failed'],
                        counts['kept'], counts['protected']))

    # ------------------------------------------------------------------ manufacturing
    # v17 mrp.production states -> v19. v19 dropped 'planned' (scheduling moved
    # onto the work orders), so a planned MO arrives as confirmed.
    _MO_STATE = {'draft': 'draft', 'confirmed': 'confirmed', 'planned': 'confirmed',
                 'progress': 'progress', 'done': 'done', 'cancel': 'cancel'}

    def _txn_manufacturing(self, cur, cache, stats):
        """Manufacturing orders — the lab's job cards.

        Created with their BoM so the component lines explode exactly as they do
        for a native MO, then moved to the source state by a direct write. The
        raw-material moves are therefore documents, not stock postings; on-hand
        comes from the inventory refresh instead (``action_refresh_inventory``).
        """
        d = self._txn_dates()
        MO = self.env['mrp.production'].sudo().with_context(
            mail_create_nolog=True, mail_notrack=True, tracking_disable=True)
        try:
            cur.execute("SELECT * FROM mrp_production WHERE COALESCE(date_start, create_date) >= %s "
                        "ORDER BY id" + self._txn_tail(), (d,))
            rows = cur.fetchall()
        except psycopg2.Error as e:
            stats.append("Manufacturing Orders   SKIPPED (%s)" % str(e).split('\n')[0])
            return
        if not rows:
            stats.append("Manufacturing Orders   nothing to migrate")
            return
        counts = {'created': 0, 'updated': 0, 'failed': 0, 'skipped': 0}
        batch, processed = [], 0
        for row in rows:
            processed += 1
            # A savepoint rolls the DATABASE back, but not these Python lists —
            # without the mark a rolled-back create still leaves a migration.map row
            # pointing at an id that does not exist, and resume mode then skips that
            # document forever.
            mark = len(batch)
            try:
                with self.env.cr.savepoint():
                    product = self._resolve(cache, 'product.product', row.get('product_id'))
                    if not product:
                        counts['skipped'] += 1
                        continue
                    cid = self._txn_company(cache, row)
                    pt = self._txn_picking_type(cache, cid, 'mrp_operation')
                    vals = {
                        'name': row.get('name') or '/',
                        'product_id': product,
                        'product_qty': row.get('product_qty') or 1.0,
                        'company_id': cid,
                        'origin': row.get('origin'),
                        'date_start': row.get('date_start') or row.get('date_planned_start'),
                        'date_finished': row.get('date_planned_finished'),
                        'x_src_id': row['id'],
                    }
                    if pt:
                        vals['picking_type_id'] = pt
                    uom = self._resolve(cache, 'uom.uom', row.get('product_uom_id'))
                    if uom:
                        vals['product_uom_id'] = uom
                    bom = self._resolve(cache, 'mrp.bom', row.get('bom_id'))
                    if bom:
                        vals['bom_id'] = bom
                    user = self._resolve(cache, 'res.users', row.get('user_id'))
                    if user:
                        vals['user_id'] = user
                    # Link the ORIGIN LINE, not the order. sale_custom declares
                    # mrp.production.sale_id (and color_scheme, and ul) as stored
                    # *related* fields off sale_line_id; v19 makes a related field
                    # readonly with no inverse, so a direct write to sale_id is
                    # undone by the recompute that follows the create — the MO ends
                    # up with no sale order at all. Writing sale_line_id (native to
                    # sale_mrp) sets all three, and fills in the colour and U/L the
                    # job card prints.
                    sol = self._resolve(cache, 'sale.order.line', row.get('sale_line_id'))
                    if not sol:
                        # No origin line in the source: fall back to the order's
                        # first line so the job card still points home.
                        so_id = self._resolve(cache, 'sale.order', row.get('sale_id'))
                        line = self.env['sale.order'].sudo().browse(so_id).order_line[:1] \
                            if so_id else None
                        sol = line.id if line else False
                    if sol:
                        vals['sale_line_id'] = sol
                    vals = self._valid(self.env['mrp.production'], vals)
                    existing = self._resolve(cache, 'mrp.production', row['id'])
                    if existing and self.txn_only_new:
                        counts['skipped'] += 1
                        continue
                    if existing:
                        mo = MO.browse(existing)
                        # v19 refuses to move the dates of a done/cancelled MO
                        # ("You cannot move a manufacturing order once it is
                        # cancelled or done") unless force_date is set — and 1,123
                        # of these arrive already done or cancelled, so a re-run
                        # would fail on every one of them.
                        mo.with_context(force_date=True).write(vals)
                        counts['updated'] += 1
                    else:
                        mo = MO.with_company(cid).create(vals)
                        batch.append((row['id'], mo.id))
                        counts['created'] += 1
                    state = self._MO_STATE.get(row.get('state'), 'draft')
                    if state != mo.state:
                        mo.write({'state': state})
            except Exception as e:
                counts['failed'] += 1
                # the savepoint undid the create; undo the bookkeeping too
                if len(batch) > mark:
                    del batch[mark:]
                    counts['created'] -= 1
                _logger.warning("txn mrp.production id=%s: %s", row['id'], e)
            if processed % self._TXN_BATCH == 0:
                self._txn_put_many(cache, 'mrp.production', batch)
                batch = []
                self.env.cr.commit()
        self._txn_put_many(cache, 'mrp.production', batch)
        self.env.cr.commit()
        stats.append("Manufacturing Orders   created=%d updated=%d skipped=%d failed=%d"
                     % (counts['created'], counts['updated'], counts['skipped'], counts['failed']))

    # ------------------------------------------------------------------ transfers
    _PICK_STATE = {'draft': 'draft', 'waiting': 'confirmed', 'confirmed': 'confirmed',
                   'partially_available': 'assigned', 'assigned': 'assigned',
                   'done': 'done', 'cancel': 'cancel'}

    def _txn_pickings(self, cur, cache, stats):
        """Transfers and their moves.

        v19 removed ``stock.move.name``; the source line description goes to
        ``description_picking``. States are written, not executed — see the module
        docstring — so a 'done' transfer is a record of what happened rather than a
        replayed stock posting.
        """
        d = self._txn_dates()
        Picking = self.env['stock.picking'].sudo().with_context(
            mail_create_nolog=True, mail_notrack=True, tracking_disable=True)
        cur.execute("SELECT * FROM stock_picking WHERE date >= %s ORDER BY id"
                    + self._txn_tail(), (d,))
        rows = cur.fetchall()
        if not rows:
            stats.append("Transfers              nothing to migrate")
            return
        ids = tuple(r['id'] for r in rows)
        cur.execute("""SELECT id, picking_id, product_id, product_uom, product_uom_qty,
                              quantity, name, origin, state, date, price_unit,
                              location_id, location_dest_id, description_picking,
                              sale_line_id, purchase_line_id, reference, date_deadline,
                              procure_method, is_inventory
                         FROM stock_move WHERE picking_id IN %s ORDER BY picking_id, id""",
                    (ids,))
        moves_by_pick = self._group(cur.fetchall(), 'picking_id')
        # v17 type id -> code, so the v19 operation type can be matched per company
        cur.execute("SELECT id, code FROM stock_picking_type")
        type_code = {r['id']: r['code'] for r in cur.fetchall()}

        counts = {'created': 0, 'updated': 0, 'failed': 0, 'skipped': 0, 'moves': 0}
        batch, states, processed = [], {}, 0
        for row in rows:
            processed += 1
            # A savepoint rolls the DATABASE back, but not these Python lists —
            # without the mark a rolled-back create still leaves a migration.map row
            # pointing at an id that does not exist, and resume mode then skips that
            # document forever.
            mark = len(batch)
            try:
                with self.env.cr.savepoint():
                    cid = self._txn_company(cache, row)
                    code = type_code.get(row.get('picking_type_id'), 'internal')
                    # the transfer's own operation type when it was adopted, else
                    # the company's type of the same kind
                    pt = self._resolve(cache, 'stock.picking.type', row.get('picking_type_id')) \
                        or self._txn_picking_type(cache, cid, code)
                    if not pt:
                        counts['skipped'] += 1
                        continue
                    ptype = self.env['stock.picking.type'].browse(pt)
                    # The lab's own stores were migrated as locations; a transfer
                    # keeps its real from/to and only falls back to the operation
                    # type's defaults when a location did not map.
                    src = self._resolve(cache, 'stock.location', row.get('location_id')) \
                        or ptype.default_location_src_id.id
                    dest = self._resolve(cache, 'stock.location', row.get('location_dest_id')) \
                        or ptype.default_location_dest_id.id
                    move_vals = []
                    for mv in moves_by_pick.get(row['id'], []):
                        product = self._resolve(cache, 'product.product', mv.get('product_id'))
                        if not product:
                            continue
                        m = {
                            'product_id': product,
                            'product_uom_qty': mv.get('product_uom_qty') or 0.0,
                            'description_picking': mv.get('description_picking') or mv.get('name'),
                            'company_id': cid,
                            'date': mv.get('date'),
                            'x_src_id': mv['id'],
                        }
                        # NO `quantity` here: writing it on a move makes Odoo 19 create a
                        # move line of its own, and the move-line phase then adds the
                        # source's line on top - every done move ends up counted twice.
                        # The replayed lines carry the quantity.
                        for f in ('origin', 'reference', 'date_deadline', 'procure_method',
                                  'is_inventory', 'price_unit'):
                            if mv.get(f) is not None:
                                m[f] = mv[f]
                        # the sale / purchase line this move serves: delivered and
                        # received quantities on the orders come from these
                        for dst, model in (('sale_line_id', 'sale.order.line'),
                                           ('purchase_line_id', 'purchase.order.line')):
                            target = self._resolve(cache, model, mv.get(dst))
                            if target:
                                m[dst] = target
                        uom = self._resolve(cache, 'uom.uom', mv.get('product_uom'))
                        if uom:
                            m['product_uom'] = uom
                        m['location_id'] = self._resolve(cache, 'stock.location', mv.get('location_id')) or src
                        m['location_dest_id'] = self._resolve(cache, 'stock.location', mv.get('location_dest_id')) or dest
                        move_vals.append((0, 0, self._valid(self.env['stock.move'], m)))
                    vals = {
                        'name': row.get('name') or '/',
                        'picking_type_id': pt,
                        'company_id': cid,
                        'origin': row.get('origin'),
                        'note': row.get('note'),
                        'scheduled_date': row.get('scheduled_date') or row.get('min_date') or row.get('date'),
                        'date_done': row.get('date_done'),
                        'date_deadline': row.get('date_deadline'),
                        'move_type': row.get('move_type') or 'direct',
                        'x_src_id': row['id'],
                    }
                    for f in ('priority', 'note'):
                        if row.get(f):
                            vals[f] = row[f]
                    # (the loop variables must not be `src`: that is the source location)
                    for dst_field, (src_col, rel_model) in {
                            'partner_id': ('partner_id', 'res.partner'),
                            'sale_id': ('sale_id', 'sale.order'),
                            'user_id': ('user_id', 'res.users'),
                            'owner_id': ('owner_id', 'res.partner'),
                    }.items():
                        val = self._resolve(cache, rel_model, row.get(src_col))
                        if val:
                            vals[dst_field] = val
                    if src and dest:
                        vals['location_id'], vals['location_dest_id'] = src, dest
                    vals = self._valid(self.env['stock.picking'], vals)
                    existing = self._resolve(cache, 'stock.picking', row['id'])
                    if existing and self.txn_only_new:
                        counts['skipped'] += 1
                        continue
                    if existing:
                        pick = Picking.browse(existing)
                        # Replace the moves too. Building move_vals and then writing
                        # only the header would leave a transfer permanently stuck
                        # with whatever it got on the first run — including moves
                        # dropped because their product had not been mapped yet.
                        # A done transfer refuses to drop its moves, and its state
                        # is ours to set anyway (it is re-stamped below).
                        self._txn_apply_picking_states({'draft': [pick.id]})
                        pick.move_ids.unlink()
                        pick.write(dict(vals, move_ids=move_vals))
                        counts['updated'] += 1
                        counts['moves'] += len(move_vals)
                    else:
                        pick = Picking.with_company(cid).create(
                            dict(vals, move_ids=move_vals))
                        batch.append((row['id'], pick.id))
                        counts['created'] += 1
                        counts['moves'] += len(move_vals)
                    # Collect, don't write yet. stock.picking.state is computed and
                    # stored from move_ids.state, so a raw UPDATE here races the
                    # recompute create() has already scheduled: the flush lands
                    # after the SQL and puts every transfer back to Draft. The
                    # batch below flushes FIRST, then writes, then invalidates.
                    states.setdefault(self._PICK_STATE.get(row.get('state'), 'draft'),
                                      []).append(pick.id)
            except Exception as e:
                counts['failed'] += 1
                # the savepoint undid the create; undo the bookkeeping too
                if len(batch) > mark:
                    del batch[mark:]
                    counts['created'] -= 1
                _logger.warning("txn stock.picking id=%s: %s", row['id'], e)
            if processed % self._TXN_BATCH == 0:
                self._txn_put_many(cache, 'stock.picking', batch)
                self._txn_apply_picking_states(states)
                batch, states = [], {}
                self.env.cr.commit()
        self._txn_put_many(cache, 'stock.picking', batch)
        self._txn_apply_picking_states(states)
        self.env.cr.commit()
        self._txn_link_backorders(rows, cache)
        self._txn_seed_picking_numbers(cur, cache, stats)
        stats.append("Transfers              created=%d (%d moves) updated=%d skipped=%d failed=%d"
                     % (counts['created'], counts['moves'], counts['updated'],
                        counts['skipped'], counts['failed']))

    def _txn_link_backorders(self, rows, cache):
        """A backorder points at the transfer it continues; both exist now."""
        Picking = self.env['stock.picking'].sudo()
        for row in rows:
            if not row.get('backorder_id'):
                continue
            this = self._resolve(cache, 'stock.picking', row['id'])
            parent = self._resolve(cache, 'stock.picking', row['backorder_id'])
            if this and parent:
                try:
                    with self.env.cr.savepoint():
                        Picking.browse(this).write({'backorder_id': parent})
                except Exception as e:
                    _logger.warning("backorder link picking=%s: %s", row['id'], e)
        self.env.cr.commit()

    def _txn_stock_moves(self, cur, cache, stats):
        """Moves that belong to no transfer: inventory adjustments (and scraps).

        They are what makes the stock history complete; without them the count
        of a product jumps at every adjustment with nothing to show why.
        """
        d = self._txn_dates()
        cur.execute("""SELECT * FROM stock_move
                        WHERE picking_id IS NULL AND date >= %s AND state IN ('done', 'cancel')
                        ORDER BY date, id""" + self._txn_tail(), (d,))
        rows = cur.fetchall()
        if not rows:
            stats.append("Stock Moves (no transfer) nothing to migrate")
            return
        Move = self.env['stock.move'].sudo()
        counts = {'created': 0, 'skipped': 0, 'failed': 0}
        batch, states, processed = [], {}, 0
        for mv in rows:
            processed += 1
            mark = len(batch)
            try:
                with self.env.cr.savepoint():
                    if self._resolve(cache, 'stock.move', mv['id']):
                        counts['skipped'] += 1
                        continue
                    product = self._resolve(cache, 'product.product', mv.get('product_id'))
                    src = self._resolve(cache, 'stock.location', mv.get('location_id'))
                    dest = self._resolve(cache, 'stock.location', mv.get('location_dest_id'))
                    if not (product and src and dest):
                        counts['skipped'] += 1
                        continue
                    cid = self._txn_company(cache, mv)
                    vals = {
                        'product_id': product, 'location_id': src, 'location_dest_id': dest,
                        'product_uom_qty': mv.get('product_uom_qty') or 0.0,
                        # no `quantity`: see _txn_pickings - the move lines carry it
                        'description_picking': mv.get('description_picking') or mv.get('name'),
                        'name': mv.get('name'), 'origin': mv.get('origin'),
                        'reference': mv.get('reference'), 'date': mv.get('date'),
                        'company_id': cid, 'is_inventory': bool(mv.get('is_inventory')),
                        'x_src_id': mv['id'],
                    }
                    uom = self._resolve(cache, 'uom.uom', mv.get('product_uom'))
                    if uom:
                        vals['product_uom'] = uom
                    move = Move.with_company(cid).create(self._valid(Move, vals))
                    batch.append((mv['id'], move.id))
                    states.setdefault(mv['state'], []).append(move.id)
                    counts['created'] += 1
            except Exception as e:
                counts['failed'] += 1
                if len(batch) > mark:
                    del batch[mark:]
                    counts['created'] -= 1
                _logger.warning("txn stock.move id=%s: %s", mv['id'], e)
            if processed % self._TXN_BATCH == 0:
                self._txn_put_many(cache, 'stock.move', batch)
                self._txn_apply_move_states(states)
                batch, states = [], {}
                self.env.cr.commit()
        self._txn_put_many(cache, 'stock.move', batch)
        self._txn_apply_move_states(states)
        self.env.cr.commit()
        stats.append("Stock Moves (no transfer) created=%d skipped=%d failed=%d"
                     % (counts['created'], counts['skipped'], counts['failed']))

    def _txn_apply_move_states(self, states):
        if not states:
            return
        self.env.flush_all()
        for state, ids in states.items():
            if ids:
                self.env.cr.execute("UPDATE stock_move SET state=%s WHERE id IN %s",
                                    (state, tuple(ids)))
        self.env.invalidate_all()

    def _txn_move_lines(self, cur, cache, stats):
        """The detail lines of every done move - and, through them, the stock.

        Odoo 19 applies a move line created on a done move to the quants at
        once, so replaying the lines in date order rebuilds on-hand from the
        history itself, location by location, exactly as the old system arrived
        at it. The inventory phase afterwards only has to confirm the figures.
        """
        d = self._txn_dates()
        cur.execute("""SELECT l.* FROM stock_move_line l
                        JOIN stock_move m ON m.id = l.move_id
                       WHERE m.state = 'done' AND l.date >= %s
                       ORDER BY l.date, l.id""" + self._txn_tail(), (d,))
        rows = cur.fetchall()
        if not rows:
            stats.append("Stock Move Lines       nothing to migrate")
            return
        # every move mapped, in one query, rather than one search per line
        self.env.cr.execute("SELECT src_id, dst_id FROM migration_map WHERE dst_model = 'stock.move'")
        move_map = dict(self.env.cr.fetchall())
        self.env.cr.execute("SELECT x_src_id, id FROM stock_move WHERE x_src_id IS NOT NULL")
        move_map.update({k: v for k, v in self.env.cr.fetchall() if k not in move_map})
        Line = self.env['stock.move.line'].sudo()
        counts = {'created': 0, 'skipped': 0, 'unmapped': 0, 'failed': 0}
        batch, processed = [], 0
        for ln in rows:
            processed += 1
            mark = len(batch)
            try:
                with self.env.cr.savepoint():
                    if self._resolve(cache, 'stock.move.line', ln['id']):
                        counts['skipped'] += 1
                        continue
                    if not float(ln.get('quantity') or 0.0):
                        # the source keeps zero-quantity lines (a count that found nothing);
                        # Odoo 19 refuses them and they carry no stock anyway
                        counts['skipped'] += 1
                        continue
                    move_id = move_map.get(ln['move_id'])
                    product = self._resolve(cache, 'product.product', ln.get('product_id'))
                    src = self._resolve(cache, 'stock.location', ln.get('location_id'))
                    dest = self._resolve(cache, 'stock.location', ln.get('location_dest_id'))
                    if not (move_id and product and src and dest):
                        counts['unmapped'] += 1
                        continue
                    move = self.env['stock.move'].sudo().browse(move_id)
                    vals = {
                        'move_id': move_id, 'picking_id': move.picking_id.id or False,
                        'product_id': product, 'location_id': src, 'location_dest_id': dest,
                        'quantity': ln.get('quantity') or 0.0, 'picked': True,
                        'date': ln.get('date'), 'company_id': move.company_id.id,
                        'x_src_id': ln['id'],
                    }
                    uom = self._resolve(cache, 'uom.uom', ln.get('product_uom_id'))
                    if uom:
                        vals['product_uom_id'] = uom
                    line = Line.with_company(move.company_id).create(self._valid(Line, vals))
                    if line.date != ln.get('date') and ln.get('date'):
                        self.env.cr.execute("UPDATE stock_move_line SET date=%s WHERE id=%s",
                                            (ln['date'], line.id))
                    batch.append((ln['id'], line.id))
                    counts['created'] += 1
            except Exception as e:
                counts['failed'] += 1
                if len(batch) > mark:
                    del batch[mark:]
                    counts['created'] -= 1
                _logger.warning("txn stock.move.line id=%s: %s", ln['id'], e)
            if processed % self._TXN_BATCH == 0:
                self._txn_put_many(cache, 'stock.move.line', batch)
                batch = []
                self.env.cr.commit()
        self._txn_put_many(cache, 'stock.move.line', batch)
        self.env.cr.commit()
        stats.append("Stock Move Lines       created=%d skipped=%d unmapped=%d failed=%d"
                     % (counts['created'], counts['skipped'], counts['unmapped'], counts['failed']))

    def _txn_seed_picking_numbers(self, cur, cache, stats):
        """Continue the v17 transfer numbering.

        The transfers keep their v17 references, but the v19 operation types they
        are attached to carry their own sequences starting at 1 — and v19 enforces
        ``unique(name, company_id)`` on stock.picking. Left alone, the first new
        delivery in v19 would eventually walk straight into a migrated reference.
        Same treatment the module already gives invoices and MOs.
        """
        seeded = skipped = 0
        details = []
        try:
            cur.execute("""
                SELECT DISTINCT ON (t.code, w.company_id)
                       t.code, w.company_id, p.name
                  FROM stock_picking p
                  JOIN stock_picking_type t ON t.id = p.picking_type_id
                  LEFT JOIN stock_warehouse w ON w.id = t.warehouse_id
                 WHERE p.name IS NOT NULL
                 ORDER BY t.code, w.company_id, p.id DESC
            """)
            rows = cur.fetchall()
        except psycopg2.Error as e:
            stats.append("Transfer numbering     SKIPPED (%s)" % e)
            return
        for r in rows:
            cid = self._resolve(cache, 'res.company', r.get('company_id'))
            m = re.match(r'^(.*?)(\d+)\s*$', r.get('name') or '')
            if not cid or not m:
                skipped += 1
                continue
            prefix, num = m.group(1), int(m.group(2))
            pt_id = self._txn_picking_type(cache, cid, r['code'])
            seq = self.env['stock.picking.type'].browse(pt_id).sequence_id if pt_id else None
            if not seq:
                skipped += 1
                continue
            try:
                with self.env.cr.savepoint():
                    seq.write({'prefix': prefix, 'number_next': num + 1})
                seeded += 1
                details.append("%s[%s] -> next %s%0*d"
                               % (r['code'], self.env['res.company'].browse(cid).name,
                                  prefix, seq.padding, num + 1))
            except Exception as e:
                skipped += 1
                _logger.warning("picking seq %s: %s", seq.id, e)
        self.env.cr.commit()
        stats.append("Transfer numbering     %d sequence(s) set, %d skipped" % (seeded, skipped))
        for d in details:
            stats.append("    " + d)

    def _txn_apply_picking_states(self, states):
        """Stamp the source state onto a batch of transfers and their moves.

        Order matters. ``stock.picking.state`` is computed-and-stored from
        ``move_ids.state``, so create() schedules a recompute; flushing first
        settles it, the UPDATEs then put the source state in place, and
        invalidating drops the now-stale cache so nothing writes Draft back over
        it. Doing this per batch rather than per record keeps 24k transfers to a
        few dozen statements.
        """
        if not states:
            return
        self.env.flush_all()
        for state, ids in states.items():
            if not ids:
                continue
            self.env.cr.execute(
                "UPDATE stock_move SET state=%s WHERE picking_id IN %s", (state, tuple(ids)))
            self.env.cr.execute(
                "UPDATE stock_picking SET state=%s WHERE id IN %s", (state, tuple(ids)))
        self.env.invalidate_all()

    # ------------------------------------------------------------------ purchases
    # v19 dropped 'done' from purchase.order.state — a locked order is
    # state='purchase' with locked=True — so writing 'done' would raise.
    _PO_STATE = {'draft': 'draft', 'sent': 'sent', 'to approve': 'to approve',
                 'purchase': 'purchase', 'done': 'purchase', 'cancel': 'cancel'}

    def _txn_purchases(self, cur, cache, stats):
        d = self._txn_dates()
        PO = self.env['purchase.order'].sudo().with_context(
            mail_create_nolog=True, mail_notrack=True, tracking_disable=True)
        cur.execute("SELECT * FROM purchase_order WHERE date_order >= %s ORDER BY id"
                    + self._txn_tail(), (d,))
        rows = cur.fetchall()
        if not rows:
            stats.append("Purchase Orders        nothing to migrate")
            return
        ids = tuple(r['id'] for r in rows)
        cur.execute("SELECT * FROM purchase_order_line WHERE order_id IN %s ORDER BY order_id, sequence, id",
                    (ids,))
        lines_by_order = self._group(cur.fetchall(), 'order_id')
        line_taxes = self._rel(cur, 'account_tax_purchase_order_line_rel',
                               'purchase_order_line_id', 'account_tax_id')
        counts = {'created': 0, 'updated': 0, 'failed': 0, 'skipped': 0, 'lines_kept': 0}
        batch, pending, processed = [], [], 0
        for row in rows:
            processed += 1
            # A savepoint rolls the DATABASE back, but not these Python lists —
            # without the mark a rolled-back create still leaves a migration.map row
            # pointing at an id that does not exist, and resume mode then skips that
            # document forever.
            mark = len(batch)
            try:
                with self.env.cr.savepoint():
                    partner = self._resolve(cache, 'res.partner', row.get('partner_id'))
                    if not partner:
                        counts['skipped'] += 1
                        continue
                    cid = self._txn_company(cache, row)
                    line_vals = []
                    for ln in lines_by_order.get(row['id'], []):
                        product = self._resolve(cache, 'product.product', ln.get('product_id'))
                        if not product:
                            continue
                        lv = {
                            'product_id': product,
                            'name': ln.get('name') or '/',
                            'product_qty': ln.get('product_qty') or 0.0,
                            'price_unit': ln.get('price_unit') or 0.0,
                            'sequence': ln.get('sequence') or 10,
                            'date_planned': ln.get('date_planned'),
                            'x_src_id': ln['id'],
                            # explicit, empty included — see the sale-order note
                            'tax_ids': [(6, 0, [
                                t for t in (self._resolve(cache, 'account.tax', t)
                                            for t in line_taxes.get(ln['id'], [])) if t])],
                        }
                        uom = self._resolve(cache, 'uom.uom', ln.get('product_uom'))
                        if uom:
                            lv['product_uom_id'] = uom
                        line_vals.append((0, 0, self._valid(self.env['purchase.order.line'], lv)))
                    vals = {
                        'name': row.get('name') or '/',
                        'partner_id': partner,
                        'company_id': cid,
                        'date_order': row.get('date_order'),
                        'date_approve': row.get('date_approve'),
                        'date_planned': row.get('date_planned'),
                        'partner_ref': row.get('partner_ref'),
                        'origin': row.get('origin'),
                        'notes': row.get('notes'),
                        'x_src_id': row['id'],
                    }
                    for dst, (src, model) in {
                            'payment_term_id': ('payment_term_id', 'account.payment.term'),
                            'fiscal_position_id': ('fiscal_position_id', 'account.fiscal.position'),
                            'picking_type_id': ('picking_type_id', 'stock.picking.type'),
                            'dest_address_id': ('dest_address_id', 'res.partner'),
                            'user_id': ('user_id', 'res.users'),
                    }.items():
                        val = self._resolve(cache, model, row.get(src))
                        if val:
                            vals[dst] = val
                    vals = self._valid(self.env['purchase.order'], vals)
                    existing = self._resolve(cache, 'purchase.order', row['id'])
                    if existing and self.txn_only_new:
                        counts['skipped'] += 1
                        continue
                    if existing:
                        po = PO.browse(existing)
                        po.write({'state': 'draft'})
                        # merged in place, never replaced: see _txn_merge_lines
                        if not self._txn_rewrite_lines(po, vals, line_vals):
                            counts['lines_kept'] += 1
                        counts['updated'] += 1
                    else:
                        po = PO.with_company(cid).create(dict(vals, order_line=line_vals))
                        batch.append((row['id'], po.id))
                        counts['created'] += 1
                    post = {'state': self._PO_STATE.get(row.get('state'), 'draft')}
                    if row.get('state') == 'done' and 'locked' in po._fields:
                        post['locked'] = True
                    po.write(post)
                    for rec in po.order_line:
                        if rec.x_src_id:
                            pending.append((rec.x_src_id, rec.id))
            except Exception as e:
                counts['failed'] += 1
                # the savepoint undid the create; undo the bookkeeping too
                if len(batch) > mark:
                    del batch[mark:]
                    counts['created'] -= 1
                _logger.warning("txn purchase.order id=%s: %s", row['id'], e)
            if processed % self._TXN_BATCH == 0:
                self._txn_put_many(cache, 'purchase.order', batch)
                self._txn_put_many(cache, 'purchase.order.line', pending)
                batch, pending = [], []
                self.env.cr.commit()
        self._txn_put_many(cache, 'purchase.order', batch)
        self._txn_put_many(cache, 'purchase.order.line', pending)
        self.env.cr.commit()
        stats.append("Purchase Orders        created=%d updated=%d skipped=%d failed=%d "
                     "lines-left-unpaired=%d"
                     % (counts['created'], counts['updated'], counts['skipped'], counts['failed'],
                        counts['lines_kept']))

    # ------------------------------------------------------------------ reconciliation
    def _txn_source_line_map(self, cur, cache, src_ids):
        """{source aml id: target aml id} for the given source lines.

        An entry's lines carry their source id (x_src_id) directly. An invoice's
        receivable line is generated by posting and carries none, so it is found
        through the invoice: the target move of the source move, then the line on
        the same account for the same partner.
        """
        out = {}
        if not src_ids:
            return out
        src_ids = tuple(set(src_ids))
        self.env.cr.execute(
            "SELECT x_src_id, id FROM account_move_line WHERE x_src_id IN %s", (src_ids,))
        out.update(dict(self.env.cr.fetchall()))
        missing = [i for i in src_ids if i not in out]
        if not missing:
            return out
        cur.execute("""SELECT id, move_id, account_id, partner_id, debit, credit
                         FROM account_move_line WHERE id IN %s""", (tuple(missing),))
        rows = cur.fetchall()
        move_ids = {self._resolve(cache, 'account.move', r['move_id']) for r in rows}
        move_ids.discard(False)
        by_move = {}
        if move_ids:
            self.env.cr.execute("""SELECT id, move_id, account_id, partner_id, debit, credit
                                    FROM account_move_line
                                   WHERE move_id IN %s AND display_type IN ('payment_term', 'product', 'cogs')
                                      OR move_id IN %s AND display_type IS NULL""",
                                (tuple(move_ids), tuple(move_ids)))
            for lid, mid, aid, pid, debit, credit in self.env.cr.fetchall():
                by_move.setdefault(mid, []).append((lid, aid, pid, debit, credit))
        used = set(out.values())
        for r in rows:
            mid = self._resolve(cache, 'account.move', r['move_id'])
            aid = self._resolve(cache, 'account.account', r['account_id'])
            pid = self._resolve(cache, 'res.partner', r['partner_id'])
            side = 'debit' if (r['debit'] or 0) > 0 else 'credit'
            for lid, laid, lpid, debit, credit in by_move.get(mid, []):
                if lid in used or laid != aid or (pid and lpid and lpid != pid):
                    continue
                if (side == 'debit' and (debit or 0) > 0) or (side == 'credit' and (credit or 0) > 0):
                    out[r['id']] = lid
                    used.add(lid)
                    break
        return out

    def _txn_reconcile(self, cur, cache, stats):
        """Replay the source ledger's matching, one partial at a time, in the order
        it happened there.

        Every invoice and payment is already in, with the same amounts, so
        matching the same pairs in the same order reproduces the same partials —
        and with them the invoices' paid / partial / in-payment states and every
        clinic's open balance. Nothing is invented: a pair the source never
        matched is never matched here, however obvious it looks.
        """
        d = self._txn_dates()
        cur.execute("""
            SELECT apr.id, apr.debit_move_id, apr.credit_move_id, apr.amount
              FROM account_partial_reconcile apr
              JOIN account_move_line dl ON dl.id = apr.debit_move_id
              JOIN account_move_line cl ON cl.id = apr.credit_move_id
             WHERE dl.date >= %s AND cl.date >= %s
             ORDER BY apr.id
        """ + self._txn_tail(), (d, d))
        partials = cur.fetchall()
        if not partials:
            stats.append("Reconciliation         nothing to replay")
            return
        line_map = {}
        src_ids = []
        for r in partials:
            src_ids += [r['debit_move_id'], r['credit_move_id']]
        for start in range(0, len(src_ids), 5000):
            line_map.update(self._txn_source_line_map(cur, cache, src_ids[start:start + 5000]))
        Line = self.env['account.move.line'].sudo()
        counts = {'matched': 0, 'already': 0, 'unmapped': 0, 'failed': 0}
        processed = 0
        for r in partials:
            processed += 1
            dl, cl = line_map.get(r['debit_move_id']), line_map.get(r['credit_move_id'])
            if not (dl and cl):
                counts['unmapped'] += 1
                continue
            try:
                with self.env.cr.savepoint():
                    lines = Line.browse([dl, cl]).exists()
                    if len(lines) < 2:
                        counts['unmapped'] += 1
                        continue
                    if any(l.reconciled for l in lines):
                        counts['already'] += 1
                        continue
                    if lines[0].account_id != lines[1].account_id or not lines[0].account_id.reconcile:
                        counts['unmapped'] += 1
                        continue
                    lines.with_context(skip_account_move_synchronization=True).reconcile()
                    counts['matched'] += 1
            except Exception as e:
                counts['failed'] += 1
                _logger.warning("reconcile partial id=%s: %s", r['id'], e)
            if processed % 500 == 0:
                self.env.cr.commit()
                _logger.info("reconciliation: %d/%d", processed, len(partials))
        self.env.cr.commit()
        stats.append("Reconciliation         matched=%d already=%d unmapped=%d failed=%d"
                     % (counts['matched'], counts['already'], counts['unmapped'], counts['failed']))

    # ------------------------------------------------------------------ cheques
    # post_dated_cheque's status -> the suite's cheque register
    _CHEQUE_STATE = {'received': 'received', 'hold': 'received', 'submitted': 'deposited',
                     'accepted': 'cleared', 'bounced': 'bounced'}

    def _txn_cheques(self, cur, cache, stats):
        """Cheques received, into the suite's cheque register.

        The source kept cheque number, date and status on the payment; the suite
        keeps a register (lab.cheque) whose entries point at the payment's journal
        entry. Only cheques RECEIVED are registered — an issued cheque is the
        lab's own and has no place in a register of what clinics handed over.
        """
        if 'lab.cheque' not in self.env:
            stats.append("Cheques                SKIPPED (lab_finance_ops not installed)")
            return
        d = self._txn_dates()
        try:
            cur.execute("""
                SELECT p.id, p.cheque_nos, p.cheque_dates, p.cheque_status, p.amount,
                       p.partner_id, p.check_submitted_date, p.pay_reference,
                       m.date, m.journal_id, m.company_id, m.name AS move_name
                  FROM account_payment p JOIN account_move m ON m.id = p.move_id
                 WHERE p.cheque_status IS NOT NULL AND p.payment_type = 'inbound'
                   AND m.state = 'posted' AND m.date >= %s
                 ORDER BY p.id
            """ + self._txn_tail(), (d,))
            rows = cur.fetchall()
        except psycopg2.Error as e:
            stats.append("Cheques                SKIPPED (%s)" % str(e).split('\n')[0])
            return
        Cheque = self.env['lab.cheque'].sudo().with_context(
            mail_create_nolog=True, mail_notrack=True, tracking_disable=True)
        counts = {'created': 0, 'updated': 0, 'skipped': 0, 'failed': 0}
        batch = []
        for r in rows:
            partner = self._resolve(cache, 'res.partner', r['partner_id'])
            state = self._CHEQUE_STATE.get(r['cheque_status'])
            number = (r.get('cheque_nos') or '').strip() or (r.get('pay_reference') or '').strip()
            if not (partner and state and number):
                counts['skipped'] += 1
                continue
            vals = {
                'cheque_number': number, 'partner_id': partner,
                'cheque_date': r.get('cheque_dates') or r['date'],
                'received_date': r['date'], 'amount': float(r['amount'] or 0.0),
                'state': state, 'x_src_id': r['id'],
                'company_id': self._txn_company(cache, r),
                'note': 'Source payment %s' % (r.get('move_name') or r['id']),
            }
            journal = self._resolve(cache, 'account.journal', r.get('journal_id'))
            if journal:
                vals['journal_id'] = journal
            if state in ('deposited', 'cleared'):
                vals['deposit_date'] = r.get('check_submitted_date') or r['date']
            if state == 'cleared':
                vals['clear_date'] = r.get('check_submitted_date') or r['date']
            try:
                with self.env.cr.savepoint():
                    existing = self._resolve(cache, 'lab.cheque', r['id'])
                    rec = Cheque.browse(existing) if existing else Cheque
                    if rec:
                        # a closed cheque is locked by the register's own rule; sudo
                        # (which this run is) is how history is allowed to be rewritten
                        rec.write(self._valid(Cheque, vals))
                        counts['updated'] += 1
                    else:
                        # the register is unique on (number, clinic); the source has
                        # the same number twice for five clinics - both are real
                        # payments, so the second keeps the number with a suffix
                        taken = Cheque.with_context(active_test=False).search_count(
                            [('cheque_number', '=', number), ('partner_id', '=', partner),
                             ('company_id', '=', vals['company_id'])])
                        if taken:
                            vals['cheque_number'] = '%s/%s' % (number, r['id'])
                        rec = Cheque.create(self._valid(Cheque, vals))
                        batch.append((r['id'], rec.id))
                        counts['created'] += 1
            except Exception as e:
                counts['failed'] += 1
                _logger.warning("cheque payment id=%s: %s", r['id'], e)
        self._txn_put_many(cache, 'lab.cheque', batch)
        self.env.cr.commit()
        stats.append("Cheques                created=%d updated=%d skipped=%d failed=%d"
                     % (counts['created'], counts['updated'], counts['skipped'], counts['failed']))

    # ------------------------------------------------------------------ bill ↔ purchase links
    def _txn_bill_links(self, cur, cache, stats):
        """Point already-migrated bill lines at their purchase lines.

        The invoice phase writes this link when the purchase lines are mapped
        before it runs; a database migrated in the old phase order (or before
        purchases were in) has the bills but not the links. This fills them in
        without rewriting a single posted bill.
        """
        cur.execute("""SELECT l.id, l.purchase_line_id FROM account_move_line l
                        JOIN account_move m ON m.id = l.move_id
                       WHERE l.purchase_line_id IS NOT NULL
                         AND m.move_type IN ('in_invoice', 'in_refund')""")
        rows = cur.fetchall()
        if not rows:
            stats.append("Bill ↔ Purchase links  nothing to link")
            return
        self.env.cr.execute("""SELECT x_src_id, id, purchase_line_id FROM account_move_line
                                WHERE x_src_id IN %s""", (tuple(r['id'] for r in rows),))
        here = {src: (aml_id, pol) for src, aml_id, pol in self.env.cr.fetchall()}
        linked = already = unmapped = 0
        updates = []
        for r in rows:
            aml_id, current = here.get(r['id'], (None, None))
            pol = self._resolve(cache, 'purchase.order.line', r['purchase_line_id'])
            if not (aml_id and pol):
                unmapped += 1
                continue
            if current == pol:
                already += 1
                continue
            updates.append((pol, aml_id))
        if updates:
            # a stored many2one with no dependants: SQL is exact and does not touch
            # the posted bill's hash, dates or reconciliation
            self.env.cr.executemany(
                "UPDATE account_move_line SET purchase_line_id=%s WHERE id=%s", updates)
            self.env.invalidate_all()
            linked = len(updates)
        self.env.cr.commit()
        stats.append("Bill ↔ Purchase links  linked=%d already=%d unmapped=%d"
                     % (linked, already, unmapped))

    # ------------------------------------------------------------------ material requests
    # The lab's own module of the same name was rewritten for this suite; the
    # models kept their names, so the 1,015 requests land on the new screens.
    _MR_STATE = {'draft': 'draft', 'confirm': 'confirm', 'approved': 'approved',
                 'cancelled': 'cancelled'}

    def _txn_mr_link(self, cache, request, row, state, pickings_by_req, Picking):
        """Link a request's migrated transfer(s) and settle its state from them.

        Returns True when at least one transfer is linked. Idempotent: a transfer
        already linked, a move already on its line, a request already Delivered
        are left as they are.
        """
        pickings = Picking.browse([p for p in (
            self._resolve(cache, 'stock.picking', pk['id'])
            for pk in pickings_by_req.get(row['id'], [])) if p]).exists()
        if not pickings:
            return False
        unlinked = pickings.filtered(lambda p: p.material_request_id != request)
        if unlinked:
            unlinked.write({'material_request_id': request.id})
        by_product = {}
        for line in request.line_ids:
            by_product.setdefault(line.product_id.id, line)
        for move in pickings.move_ids:
            line = by_product.get(move.product_id.id)
            if line and not move.material_request_line_id:
                move.material_request_line_id = line.id
        if state == 'approved' and request.state == 'approved':
            live = pickings.filtered(lambda p: p.state != 'cancel')
            if live and all(p.state == 'done' for p in live):
                # v17 moved the goods on approval; here that is the Delivered state
                request.write({'state': 'done'})
        return True

    def _txn_material_requests(self, cur, cache, stats):
        """Department requests for consumables, with their lines and transfers.

        In Odoo 17 approving a request moved the goods there and then; in this
        suite approval raises the transfer and the store validates it. So a v17
        request arrives with its own transfer linked back to it, every move on
        the line it fulfils (delivered quantities come out of those moves), and
        an approved request whose transfer is done is **Delivered** - the state
        the store would have reached by validating.

        The linking is done on EVERY pass, resume mode included: a request migrated
        before its transfer was (any phase order, a paused run) picks the link up
        the next time this runs, without its lines being rewritten.
        """
        if 'material.request' not in self.env:
            stats.append("Material Requests      SKIPPED (material_request not installed)")
            return
        d = self._txn_dates()
        try:
            cur.execute("SELECT * FROM material_request WHERE create_date >= %s ORDER BY id"
                        + self._txn_tail(), (d,))
            rows = cur.fetchall()
        except psycopg2.Error as e:
            stats.append("Material Requests      SKIPPED (%s)" % str(e).split('\n')[0])
            return
        if not rows:
            stats.append("Material Requests      nothing to migrate")
            return
        ids = tuple(r['id'] for r in rows)
        cur.execute("SELECT * FROM material_request_lines WHERE material_request_id IN %s ORDER BY id",
                    (ids,))
        lines_by_req = self._group(cur.fetchall(), 'material_request_id')
        cur.execute("SELECT id, material_request_id FROM stock_picking WHERE material_request_id IN %s",
                    (ids,))
        pickings_by_req = self._group(cur.fetchall(), 'material_request_id')
        cur.execute("""SELECT m.id, m.picking_id, m.product_id FROM stock_move m
                        JOIN stock_picking p ON p.id = m.picking_id
                       WHERE p.material_request_id IN %s""", (ids,))
        moves_by_pick = self._group(cur.fetchall(), 'picking_id')

        Request = self.env['material.request'].sudo().with_context(
            mail_create_nolog=True, mail_notrack=True, tracking_disable=True,
            mail_auto_subscribe_no_notify=True)
        Picking = self.env['stock.picking'].sudo()
        Move = self.env['stock.move'].sudo()
        counts = {'created': 0, 'updated': 0, 'skipped': 0, 'failed': 0, 'linked': 0}
        batch, processed = [], 0
        for row in rows:
            processed += 1
            mark = len(batch)
            try:
                with self.env.cr.savepoint():
                    location = self._resolve(cache, 'stock.location', row.get('location_id'))
                    if not location:
                        counts['skipped'] += 1
                        continue
                    existing = self._resolve(cache, 'material.request', row['id'])
                    cid = self._txn_company(cache, row)
                    state = self._MR_STATE.get(row.get('state'), 'draft')
                    if existing and self.txn_only_new:
                        # already here: only the transfer link and the state it implies
                        request = Request.browse(existing).exists()
                        if not request:
                            counts['skipped'] += 1
                            continue
                        if self._txn_mr_link(cache, request, row, state, pickings_by_req, Picking):
                            counts['linked'] += 1
                        else:
                            counts['skipped'] += 1
                        continue
                    line_vals = []
                    for ln in lines_by_req.get(row['id'], []):
                        product = self._resolve(cache, 'product.product', ln.get('product_id'))
                        if not product:
                            continue
                        qty = float(ln.get('quantity') or 0.0)
                        line_vals.append((0, 0, self._valid(self.env['material.request.lines'], {
                            'product_id': product, 'quantity': qty,
                            'qty_approved': qty if state == 'approved' else 0.0,
                            'qty_approved_set': state == 'approved',
                            'x_src_id': ln['id'],
                        })))
                    vals = {
                        'name': row.get('name') or '/',
                        'company_id': cid, 'location_id': location,
                        'user_id': self._resolve(cache, 'res.users', row.get('create_uid'))
                        or self.env.user.id,
                        'date_request': (row.get('create_date') or fields.Datetime.now()).date(),
                        'x_src_id': row['id'],
                    }
                    vals = self._valid(Request, vals)
                    if existing:
                        request = Request.browse(existing)
                        request.line_ids.unlink()
                        request.write(dict(vals, line_ids=line_vals))
                        counts['updated'] += 1
                    else:
                        request = Request.with_company(cid).create(dict(vals, line_ids=line_vals))
                        batch.append((row['id'], request.id))
                        counts['created'] += 1
                    post = {'state': state}
                    if state == 'approved':
                        post['approver_id'] = vals['user_id']
                        post['date_approved'] = row.get('write_date')
                    request.write(post)
                    # its transfer(s), each move onto the line it fulfils, and the
                    # Delivered state when the goods already went
                    if self._txn_mr_link(cache, request, row, state, pickings_by_req, Picking):
                        counts['linked'] += 1
            except Exception as e:
                counts['failed'] += 1
                if len(batch) > mark:
                    del batch[mark:]
                    counts['created'] -= 1
                _logger.warning("txn material.request id=%s: %s", row['id'], e)
            if processed % self._TXN_BATCH == 0:
                self._txn_put_many(cache, 'material.request', batch)
                batch = []
                self.env.cr.commit()
        self._txn_put_many(cache, 'material.request', batch)
        self.env.cr.commit()
        stats.append("Material Requests      created=%d updated=%d linked-transfers=%d skipped=%d failed=%d"
                     % (counts['created'], counts['updated'], counts['linked'],
                        counts['skipped'], counts['failed']))

    # ------------------------------------------------------------------ attachments
    # Source models whose attachments and images come across, with the map used
    # to find the target record.
    _ATTACHMENT_MODELS = ('sale.order', 'res.partner', 'product.template', 'hr.employee',
                          'account.move', 'purchase.order', 'stock.picking')

    def _txn_attachments(self, cur, cache, stats):
        """Files and images, read from the source filestore on disk.

        An attachment row only names its file (store_fname); the bytes live in
        the filestore, which is why the backend needs a path to a copy of it. The
        bytes are handed to Odoo as `raw`, so the target computes its own
        checksum and store path — nothing about the old filestore layout is
        assumed beyond "<filestore>/<store_fname>".
        """
        root = (self.src_filestore or '').strip()
        if not root:
            stats.append("Attachments            SKIPPED (no source filestore path set)")
            return
        if not os.path.isdir(root):
            stats.append("Attachments            SKIPPED (%s is not a directory)" % root)
            return
        d = self._txn_dates()
        cur.execute("""
            SELECT id, name, res_model, res_id, res_field, mimetype, store_fname,
                   file_size, public, create_date
              FROM ir_attachment
             WHERE res_model IN %s AND res_id IS NOT NULL AND store_fname IS NOT NULL
               AND (res_field IS NULL OR res_field = 'image_1920')
             ORDER BY id
        """, (self._ATTACHMENT_MODELS,))
        rows = cur.fetchall()
        if not rows:
            stats.append("Attachments            nothing to migrate")
            return
        Attachment = self.env['ir.attachment'].sudo()
        counts = {'files': 0, 'images': 0, 'missing_file': 0, 'unmapped': 0, 'failed': 0,
                  'already': 0}
        batch, processed = [], 0
        for r in rows:
            processed += 1
            target = self._resolve(cache, r['res_model'], r['res_id'])
            if not target:
                counts['unmapped'] += 1
                continue
            path = os.path.join(root, r['store_fname'])
            if not os.path.isfile(path):
                counts['missing_file'] += 1
                continue
            try:
                with self.env.cr.savepoint():
                    if r['res_field'] == 'image_1920':
                        Model = self.env[r['res_model']].sudo().with_context(active_test=False)
                        rec = Model.browse(target).exists()
                        if rec and 'image_1920' in rec._fields and not rec.image_1920:
                            with open(path, 'rb') as fh:
                                rec.write({'image_1920': fh.read()})
                            counts['images'] += 1
                        else:
                            counts['already'] += 1
                        continue
                    if self._resolve(cache, 'ir.attachment', r['id']):
                        counts['already'] += 1
                        continue
                    with open(path, 'rb') as fh:
                        data = fh.read()
                    att = Attachment.create({
                        'name': r['name'] or 'file',
                        'res_model': r['res_model'], 'res_id': target,
                        'mimetype': r['mimetype'] or mimetypes.guess_type(r['name'] or '')[0]
                        or 'application/octet-stream',
                        'raw': data, 'public': bool(r.get('public')),
                    })
                    batch.append((r['id'], att.id))
                    counts['files'] += 1
            except Exception as e:
                counts['failed'] += 1
                _logger.warning("attachment id=%s: %s", r['id'], e)
            if processed % self._TXN_BATCH == 0:
                self._txn_put_many(cache, 'ir.attachment', batch)
                batch = []
                self.env.cr.commit()
        self._txn_put_many(cache, 'ir.attachment', batch)
        self.env.cr.commit()
        stats.append("Attachments            files=%d images=%d already=%d unmapped=%d "
                     "missing-file=%d failed=%d"
                     % (counts['files'], counts['images'], counts['already'],
                        counts['unmapped'], counts['missing_file'], counts['failed']))

    # ------------------------------------------------------------------ orchestration
    _TXN_MAP_MODELS = (
        'res.company', 'res.partner', 'res.users', 'product.product', 'product.template',
        'uom.uom', 'account.account', 'account.journal', 'account.tax',
        'account.payment.term', 'account.fiscal.position', 'crm.team', 'product.colour',
        'stock.location', 'stock.warehouse', 'stock.picking.type', 'hr.employee',
        'sale.order', 'sale.order.line', 'account.move', 'account.move.line',
        'mrp.bom', 'mrp.production', 'stock.picking', 'stock.move', 'stock.move.line',
        'purchase.order', 'purchase.order.line',
        'lab.cheque', 'ir.attachment', 'material.request',
    )

    # Phases in dependency order: invoices point at order lines, reconciliation
    # needs every entry in, transfers reference the orders, attachments the lot.
    # Purchases come before invoices (bills point at purchase lines) and before
    # transfers (receipts point at them too); move lines come last among the
    # stock phases because they rebuild the quants from every done move.
    _TXN_PHASES = ['share_records', 'sale_orders', 'purchases', 'invoices',
                   'journal_entries', 'reconcile', 'cheques', 'manufacturing',
                   'pickings', 'stock_moves', 'move_lines', 'material_requests',
                   'attachments']

    def _txn_map_user_partners(self, cur, cache):
        """A user's partner resolves to the migrated user's partner.

        The partner spec skips the partners behind internal users (the user sync
        creates its own), so a document pointing at one — 5,993 technician links on
        the lab's orders are the technicians who also log in — would otherwise
        lose the reference. Only fills gaps; a partner already mapped stays as is.
        """
        try:
            cur.execute("SELECT id, partner_id FROM res_users WHERE share = false")
            rows = cur.fetchall()
        except psycopg2.Error:
            return
        partners = cache.setdefault('res.partner', {})
        Users = self.env['res.users'].sudo().with_context(active_test=False)
        for r in rows:
            if r['partner_id'] in partners:
                continue
            user_id = self._resolve(cache, 'res.users', r['id'])
            if user_id:
                partner = Users.browse(user_id).partner_id.id
                if partner:
                    partners[r['partner_id']] = partner

    def _txn_run(self, phases):
        self = self._migration_env()
        conn = self._connect()
        conn.autocommit = True
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cache, stats = {}, []
        try:
            cache['_main_company_'] = self._main_company_id(cur)
            pruned = self._txn_prune_map(self._TXN_MAP_MODELS)
            if pruned:
                stats.append("Pruned Map             %d stale mapping(s) removed" % pruned)
            self._txn_preload(cache, self._TXN_MAP_MODELS)
            self._txn_map_user_partners(cur, cache)
            for name in phases:
                _logger.info("transaction migration: %s", name)
                getattr(self, '_txn_%s' % name)(cur, cache, stats)
            # Documents carry the date they were created in Odoo 17, not the date
            # this ran. Odoo overwrites create_date/write_date on create(), so the
            # source values have to be put back once the documents exist — and it
            # belongs here rather than behind a button somebody has to remember.
            total, ts_stats = self._sync_timestamps(cur)
            stats.append("")
            stats.append("Source Timestamps      restored=%d" % total)
            stats += ts_stats
            self.env.cr.commit()
        finally:
            conn.close()
        return stats

    def action_sync_transactions(self):
        """Every document phase, in dependency order, from txn_from_date onwards."""
        self = self._migration_env()
        stats = self._txn_run(self._TXN_PHASES)
        self.txn_log = "TRANSACTION SYNC (from %s)\n%s" % (
            self._txn_dates(), "\n".join(stats))
        return self._notify(_("Transactions migrated. See the transaction log."), sticky=True)

    def action_sync_transactions_gl(self):
        """The accounting spine only: sale orders, invoices, journal entries,
        the matching between them, and the cheque register."""
        self = self._migration_env()
        stats = self._txn_run(['share_records', 'sale_orders', 'purchases', 'invoices',
                               'journal_entries', 'reconcile', 'cheques'])
        self.txn_log = "TRANSACTION SYNC — GL (from %s)\n%s" % (
            self._txn_dates(), "\n".join(stats))
        return self._notify(_("Sales and accounting documents migrated."), sticky=True)

    def action_sync_transactions_ops(self):
        """The operational documents: MOs, transfers, purchase orders, attachments."""
        self = self._migration_env()
        stats = self._txn_run(['manufacturing', 'pickings', 'stock_moves', 'move_lines',
                               'material_requests', 'attachments'])

    def action_sync_reconciliation(self):
        """Replay the source ledger's matching on its own, after a GL run."""
        self = self._migration_env()
        stats = self._txn_run(['reconcile'])
        self.txn_log = "RECONCILIATION\n%s" % "\n".join(stats)
        return self._notify(_("Reconciliation replayed. See the transaction log."), sticky=True)
        self.txn_log = "TRANSACTION SYNC — OPS (from %s)\n%s" % (
            self._txn_dates(), "\n".join(stats))
        return self._notify(_("Operational documents migrated."), sticky=True)

    def action_refresh_inventory(self):
        """Re-set on-hand quantities as of ``inventory_as_of``.

        Migrated transfers are documents, not replayed stock postings, so quants
        would otherwise still show the opening position. This recomputes on-hand
        from the source as of a later date — normally the day the dump was taken —
        and applies it as an inventory adjustment, which is also how the opening
        inventory was laid down in the first place.
        """
        self = self._migration_env()
        if not self.inventory_as_of:
            # Falling through to opening_date would quietly rewind on-hand to the
            # cut-over position — the opposite of what this button is for.
            from odoo.exceptions import UserError
            raise UserError(_("Set 'On-hand As Of' before refreshing stock. "
                              "Leaving it empty would reset on-hand back to the "
                              "opening date instead of bringing it forward."))
        stats = self._opening_inventory()
        self.txn_log = "INVENTORY REFRESH (as of %s)\n%s" % (
            self.inventory_as_of, "\n".join(stats))
        return self._notify(_("On-hand quantities refreshed."), sticky=True)
