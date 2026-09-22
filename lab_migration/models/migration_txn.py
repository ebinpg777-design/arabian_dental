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
because posting is what produces the journal entry, and the v10 invoice's own
journal entry is deliberately NOT imported (see ``_txn_journal_entries``).

**Everything is idempotent.** Every document carries ``x_odoo10_id`` plus a
``migration.map`` row, so a second run updates in place instead of duplicating.

v19 differences that bite here, all handled below:
  sale.order.line.product_uom -> product_uom_id ;  tax_id -> tax_ids
  account.invoice(+line)      -> account.move(+line) with move_type
  account.move.line.name      is required-ish; v10 allows NULL
"""
import logging
import re

import psycopg2
import psycopg2.extras

from odoo import fields, models, _

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
                        .browse(rows.mapped('odoo19_id')).exists().ids)
            dead = rows.filtered(lambda r: r.odoo19_id not in alive)
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
            SELECT dst_model, odoo10_id, odoo19_id FROM migration_map
             WHERE dst_model IN %s
        """, (tuple(models_),))
        for model, o10, o19 in self.env.cr.fetchall():
            cache.setdefault(model, {})[o10] = o19
        return cache

    def _txn_put_many(self, cache, model, pairs):
        """Record many Odoo10->Odoo19 mappings at once.

        Upsert, not insert. migration.map is unique on (dst_model, odoo10_id), and
        a re-run legitimately produces a NEW v19 id for an old key — the sale-order
        update path deletes and re-creates its lines, for one. A plain create would
        either raise (aborting the whole phase, since this runs outside the per-row
        savepoint) or leave the map pointing at an unlinked record.
        """
        if not pairs:
            return
        Map = self.env['migration.map'].sudo()
        pairs = dict(pairs)                      # last write wins within a batch
        existing = Map.search([('dst_model', '=', model), ('odoo10_id', 'in', list(pairs))])
        for rec in existing:
            new_id = pairs.pop(rec.odoo10_id, None)
            if new_id and rec.odoo19_id != new_id:
                rec.odoo19_id = new_id
        if pairs:
            Map.create([{'dst_model': model, 'odoo10_id': o10, 'odoo19_id': o19}
                        for o10, o19 in pairs.items()])
        mc = cache.setdefault(model, {})
        mc.update(pairs)
        for rec in existing:
            mc[rec.odoo10_id] = rec.odoo19_id

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
        # No fallback to '_main_company_': that key holds an Odoo 10 company id,
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
        """Key order lines created before they carried an ``x_odoo10_id``.

        Only when the pairing is certain: the same number of lines, in the order
        they were created from the source, each on the same product. Anything
        less and the lines are left unkeyed — and so untouched by the merge.
        """
        ordered = lines.sorted(lambda l: (l.sequence, l.id))
        if len(ordered) != len(line_vals) or any(
                line.product_id.id != cmd[2].get('product_id') or not cmd[2].get('x_odoo10_id')
                for line, cmd in zip(ordered, line_vals)):
            return False
        for line, cmd in zip(ordered, line_vals):
            line.x_odoo10_id = cmd[2]['x_odoo10_id']
        return True

    def _txn_merge_lines(self, lines, line_vals):
        """x2many commands that bring existing order lines in line with the source.

        A re-run used to delete every line and create it again, which cut every
        invoice line, stock move and MO off the sale/purchase line it came from.
        Now a line matched by ``x_odoo10_id`` is updated in place and keeps its id;
        a source line with no match is created. An existing keyed line the source
        no longer has is removed only when nothing points at it. Unkeyed lines —
        added in Odoo 19 after the cut-over — are never touched.
        """
        by_key = {}
        for line in lines.sorted('id'):
            if line.x_odoo10_id and line.x_odoo10_id not in by_key:
                by_key[line.x_odoo10_id] = line
        commands, matched = [], set()
        for cmd in line_vals:
            lv = cmd[2]
            line = by_key.get(lv.get('x_odoo10_id'))
            if line and line.id not in matched:
                matched.add(line.id)
                commands.append((1, line.id, lv))
            else:
                commands.append((0, 0, lv))
        stale = lines.filtered(lambda l: l.x_odoo10_id and l.id not in matched)
        commands += [(2, line.id) for line in stale - self._txn_linked_lines(stale)]
        return commands

    def _txn_rewrite_lines(self, order, vals, line_vals):
        """Write an existing order's header and merge its lines (see above).

        Returns False when the order's lines could not be paired with the source
        (unkeyed and not adoptable): the header is still updated, the lines are
        left exactly as they are rather than doubled.
        """
        lines = order.order_line
        if lines and not any(lines.mapped('x_odoo10_id')) \
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

        The v10 types are the six stock defaults per warehouse, so they are matched
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
        """Share the records that v10 used across companies, before any document
        references them.

        v10 let one company sell another company's product and bill another
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
              FROM account_invoice_line il
              JOIN account_invoice i ON i.id = il.invoice_id
              JOIN product_product pp ON pp.id = il.product_id
              JOIN product_template pt ON pt.id = pp.product_tmpl_id
             WHERE i.date_invoice >= %(d)s
               AND pt.company_id IS NOT NULL AND pt.company_id <> i.company_id
             UNION
            SELECT DISTINCT pt.id
              FROM mrp_production m
              JOIN product_product pp ON pp.id = m.product_id
              JOIN product_template pt ON pt.id = pp.product_tmpl_id
             WHERE m.date_planned_start >= %(d)s
               AND pt.company_id IS NOT NULL AND pt.company_id <> m.company_id
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
                     "(v10 cross-company usage)" % n)

    def _txn_share_partners(self, cur, cache, stats):
        """Same treatment for contacts.

        Every v10 partner carries a company_id, so v19 scopes all 7055 of them to
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
              FROM account_invoice i JOIN res_partner p ON p.id = i.partner_id
             WHERE i.date_invoice >= %(d)s
               AND p.company_id IS NOT NULL AND p.company_id <> i.company_id
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
                     "(v10 cross-company usage)" % n)

    # ------------------------------------------------------------------ sale orders
    # v10 state -> v19 state. sale_custom drops 'done' from the selection, so a
    # v10 'done' order lands as a plain confirmed order.
    _SO_STATE = {'draft': 'draft', 'sent': 'sent', 'sale': 'sale',
                 'done': 'sale', 'cancel': 'cancel'}

    _SO_SCALARS = (
        'client_order_ref', 'origin', 'note', 'picking_policy', 'validity_date',
        # dental / lab fields carried 1:1 by sale_custom in v19
        'patient', 'age', 'gender', 'modification', 'instruction', 'priority',
        'is_dd_cheque', 'is_screw', 'is_bite', 'is_bands', 'is_wires', 'is_teeth',
        'is_facebow', 'is_others', 'is_rework', 'is_pending_work', 'active',
        'impression_type', 'scanned_impression', 'impression_tray', 'wax_bite',
        'company_warning',
    )

    _SOL_SCALARS = ('name', 'sequence', 'product_uom_qty', 'price_unit', 'discount',
                    'customer_lead', 'ul', 'is_urgent', 'is_urgent_service')

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
                    vals = {f: row[f] for f in self._SO_SCALARS if row.get(f) is not None}
                    vals.update({
                        'name': row.get('name') or '/',
                        'partner_id': partner,
                        'date_order': row.get('date_order'),
                        'company_id': cid,
                        'x_odoo10_id': row['id'],
                    })
                    for dst, (src, model) in {
                            'partner_invoice_id': ('partner_invoice_id', 'res.partner'),
                            'partner_shipping_id': ('partner_shipping_id', 'res.partner'),
                            'user_id': ('user_id', 'res.users'),
                            'team_id': ('team_id', 'crm.team'),
                            'payment_term_id': ('payment_term_id', 'account.payment.term'),
                            'send_through': ('send_through', 'send.through'),
                            'register_person_id': ('register_person_id', 'res.users'),
                    }.items():
                        val = self._resolve(cache, model, row.get(src))
                        if val:
                            vals[dst] = val
                    wh = self._txn_warehouse(cache, cid)
                    if wh:
                        vals['warehouse_id'] = wh
                    line_vals = []
                    for ln in lines_by_order.get(row['id'], []):
                        product = self._resolve(cache, 'product.product', ln.get('product_id'))
                        if not product:
                            continue
                        lv = {f: ln[f] for f in self._SOL_SCALARS if ln.get(f) is not None}
                        lv['product_id'] = product
                        uom = self._resolve(cache, 'uom.uom', ln.get('product_uom'))
                        if uom:
                            lv['product_uom_id'] = uom          # v19 rename
                        colour = self._resolve(cache, 'product.colour', ln.get('color_scheme'))
                        if colour:
                            lv['color_scheme'] = colour
                        # ALWAYS set tax_ids, empty included. v19 fills a line's taxes
                        # from product.taxes_id when the key is absent, and the master
                        # sync gave the products their v10 taxes — which would add 5%
                        # GST to lines the source deliberately booked tax-free.
                        lv['tax_ids'] = [(6, 0, [
                            t for t in (self._resolve(cache, 'account.tax', t)
                                        for t in line_taxes.get(ln['id'], [])) if t])]
                        lv['x_odoo10_id'] = ln['id']
                        line_vals.append((0, 0, self._valid(self.env['sale.order.line'], lv)))
                    vals = self._valid(self.env['sale.order'], vals)
                    # sale_custom.create() renames any order created with
                    # is_edit_number set to "Old Work"; keep the v10 number and
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
                    # Each line already carries its own x_odoo10_id, so read the
                    # pairing back off the records rather than zipping two lists
                    # that only happen to be in the same order.
                    for rec in order.order_line:
                        if rec.x_odoo10_id:
                            pending.append((rec.x_odoo10_id, rec.id))
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
    # v10 account.invoice.type -> v19 account.move.move_type
    _INV_TYPE = {'out_invoice': 'out_invoice', 'in_invoice': 'in_invoice',
                 'out_refund': 'out_refund', 'in_refund': 'in_refund'}
    # v10 invoice state -> what to do in v19. 'open'/'paid' are posted; payment
    # matching itself is not migrated (see the run report).
    _INV_POST = {'open', 'paid'}

    def _txn_invoices(self, cur, cache, stats):
        d = self._txn_dates()
        Move = self.env['account.move'].sudo().with_context(
            active_test=False, mail_create_nolog=True, mail_notrack=True,
            tracking_disable=True)
        cur.execute("SELECT * FROM account_invoice WHERE date_invoice >= %s ORDER BY id"
                    + self._txn_tail(), (d,))
        invoices = cur.fetchall()
        if not invoices:
            stats.append("Invoices               nothing to migrate")
            return
        ids = tuple(i['id'] for i in invoices)
        cur.execute("SELECT * FROM account_invoice_line WHERE invoice_id IN %s ORDER BY invoice_id, sequence, id",
                    (ids,))
        lines_by_inv = self._group(cur.fetchall(), 'invoice_id')
        line_taxes = self._rel(cur, 'account_invoice_line_tax', 'invoice_line_id', 'tax_id')

        # The master sync posts a zero-amount "numbering anchor" per journal, named
        # after the LAST v10 invoice number, so that new invoices continue the v10
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
                    move_type = self._INV_TYPE.get(row.get('type'))
                    if not (partner and journal and move_type):
                        counts['skipped'] += 1
                        continue
                    cid = self._txn_company(cache, row)
                    existing = self._resolve(cache, 'account.move', row['id'])
                    if existing and self._txn_keep_move(existing, counts, row['id']):
                        continue
                    vals = {
                        'move_type': move_type,
                        'partner_id': partner,
                        'journal_id': journal,
                        'company_id': cid,
                        'invoice_date': row.get('date_invoice'),
                        'date': row.get('date') or row.get('date_invoice'),
                        'invoice_date_due': row.get('date_due'),
                        'invoice_origin': row.get('origin'),
                        'ref': row.get('reference') or row.get('name'),
                        'narration': row.get('comment'),
                        'x_odoo10_id': row['id'],
                    }
                    # Keep the v10 document number. _seed_invoice_numbers() already
                    # points the v19 sequence past the v10 maximum, so new invoices
                    # continue the same series instead of colliding with these.
                    if row.get('number'):
                        vals['name'] = row['number']
                        anchor = anchors.pop((journal, row['number']), None)
                        if anchor:
                            if anchor.state == 'posted':
                                anchor.button_draft()
                            anchor.unlink()
                    pt = self._resolve(cache, 'account.payment.term', row.get('payment_term_id'))
                    if pt:
                        vals['invoice_payment_term_id'] = pt
                    user = self._resolve(cache, 'res.users', row.get('user_id'))
                    if user:
                        vals['invoice_user_id'] = user
                    team = self._resolve(cache, 'crm.team', row.get('team_id'))
                    if team:
                        vals['team_id'] = team
                    line_vals = []
                    for ln in lines_by_inv.get(row['id'], []):
                        lv = {
                            'name': ln.get('name') or '/',
                            'quantity': ln.get('quantity') or 0.0,
                            'price_unit': ln.get('price_unit') or 0.0,
                            'discount': ln.get('discount') or 0.0,
                            'sequence': ln.get('sequence') or 10,
                            'patient': ln.get('patient'),
                            'ul': ln.get('ul'),
                        }
                        product = self._resolve(cache, 'product.product', ln.get('product_id'))
                        if product:
                            lv['product_id'] = product
                        account = self._resolve(cache, 'account.account', ln.get('account_id'))
                        if account:
                            lv['account_id'] = account
                        uom = self._resolve(cache, 'uom.uom', ln.get('uom_id'))
                        if uom:
                            lv['product_uom_id'] = uom
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
        """Every posted v10 move that is NOT an invoice's own move.

        Invoices re-post in v19 and generate their entry there, so importing the
        v10 invoice moves as well would double every sale. What is left is the
        cash side of the business — receipts, bank entries, miscellaneous — and
        those are copied line for line, debit and credit exactly as booked, which
        is the only way the v19 trial balance can tie back to v10.
        """
        d = self._txn_dates()
        Move = self.env['account.move'].sudo().with_context(
            mail_create_nolog=True, mail_notrack=True, tracking_disable=True)
        cur.execute("""
            SELECT m.* FROM account_move m
             WHERE m.date >= %s AND m.state = 'posted'
               AND NOT EXISTS (SELECT 1 FROM account_invoice i WHERE i.move_id = m.id)
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
                        'x_odoo10_id': row['id'],
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
    # v10 mrp.production states -> v19. v19 dropped 'planned' (scheduling moved
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
        cur.execute("SELECT * FROM mrp_production WHERE date_planned_start >= %s ORDER BY id"
                    + self._txn_tail(), (d,))
        rows = cur.fetchall()
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
                        'date_start': row.get('date_planned_start'),
                        'date_finished': row.get('date_planned_finished'),
                        'x_odoo10_id': row['id'],
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
                              name, origin, state, date, price_unit
                         FROM stock_move WHERE picking_id IN %s ORDER BY picking_id, id""",
                    (ids,))
        moves_by_pick = self._group(cur.fetchall(), 'picking_id')
        # v10 type id -> code, so the v19 operation type can be matched per company
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
                    pt = self._txn_picking_type(cache, cid, code)
                    if not pt:
                        counts['skipped'] += 1
                        continue
                    ptype = self.env['stock.picking.type'].browse(pt)
                    src = ptype.default_location_src_id.id
                    dest = ptype.default_location_dest_id.id
                    move_vals = []
                    for mv in moves_by_pick.get(row['id'], []):
                        product = self._resolve(cache, 'product.product', mv.get('product_id'))
                        if not product:
                            continue
                        m = {
                            'product_id': product,
                            'product_uom_qty': mv.get('product_uom_qty') or 0.0,
                            'description_picking': mv.get('name'),   # v19: name removed
                            'company_id': cid,
                            'date': mv.get('date'),
                        }
                        uom = self._resolve(cache, 'uom.uom', mv.get('product_uom'))
                        if uom:
                            m['product_uom'] = uom
                        if src and dest:
                            m['location_id'], m['location_dest_id'] = src, dest
                        move_vals.append((0, 0, self._valid(self.env['stock.move'], m)))
                    vals = {
                        'name': row.get('name') or '/',
                        'picking_type_id': pt,
                        'company_id': cid,
                        'origin': row.get('origin'),
                        'note': row.get('note'),
                        'scheduled_date': row.get('min_date') or row.get('date'),
                        'date_done': row.get('date_done'),
                        'move_type': row.get('move_type') or 'direct',
                        'courier_company': row.get('courier_company'),
                        'courier_option': row.get('courier_option'),
                        'consignment_number': row.get('consignment_number'),
                        'x_odoo10_id': row['id'],
                    }
                    partner = self._resolve(cache, 'res.partner', row.get('partner_id'))
                    if partner:
                        vals['partner_id'] = partner
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
        self._txn_seed_picking_numbers(cur, cache, stats)
        stats.append("Transfers              created=%d (%d moves) updated=%d skipped=%d failed=%d"
                     % (counts['created'], counts['moves'], counts['updated'],
                        counts['skipped'], counts['failed']))

    def _txn_seed_picking_numbers(self, cur, cache, stats):
        """Continue the v10 transfer numbering.

        The transfers keep their v10 references, but the v19 operation types they
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
                            'x_odoo10_id': ln['id'],
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
                        'partner_ref': row.get('partner_ref'),
                        'origin': row.get('origin'),
                        'notes': row.get('notes'),
                        'x_odoo10_id': row['id'],
                    }
                    pt = self._resolve(cache, 'account.payment.term', row.get('payment_term_id'))
                    if pt:
                        vals['payment_term_id'] = pt
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
            except Exception as e:
                counts['failed'] += 1
                # the savepoint undid the create; undo the bookkeeping too
                if len(batch) > mark:
                    del batch[mark:]
                    counts['created'] -= 1
                _logger.warning("txn purchase.order id=%s: %s", row['id'], e)
            if processed % self._TXN_BATCH == 0:
                self._txn_put_many(cache, 'purchase.order', batch)
                batch = []
                self.env.cr.commit()
        self._txn_put_many(cache, 'purchase.order', batch)
        self.env.cr.commit()
        stats.append("Purchase Orders        created=%d updated=%d skipped=%d failed=%d "
                     "lines-left-unpaired=%d"
                     % (counts['created'], counts['updated'], counts['skipped'], counts['failed'],
                        counts['lines_kept']))

    # ------------------------------------------------------------------ orchestration
    _TXN_MAP_MODELS = (
        'res.company', 'res.partner', 'res.users', 'product.product', 'product.template',
        'uom.uom', 'account.account', 'account.journal', 'account.tax',
        'account.payment.term', 'crm.team', 'send.through', 'product.colour',
        'sale.order', 'sale.order.line', 'account.move',
        'mrp.bom', 'mrp.production', 'stock.picking', 'purchase.order',
    )

    # Phases in dependency order: MOs and transfers reference the sale orders, and
    # the GL phases must precede the inventory refresh.
    _TXN_PHASES = ['share_records', 'sale_orders', 'invoices', 'journal_entries',
                   'manufacturing', 'pickings', 'purchases']

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
            for name in phases:
                _logger.info("transaction migration: %s", name)
                getattr(self, '_txn_%s' % name)(cur, cache, stats)
            # Documents carry the date they were created in Odoo 10, not the date
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
        """The accounting spine only: sale orders, invoices, journal entries."""
        self = self._migration_env()
        stats = self._txn_run(['share_records', 'sale_orders', 'invoices',
                               'journal_entries'])
        self.txn_log = "TRANSACTION SYNC — GL (from %s)\n%s" % (
            self._txn_dates(), "\n".join(stats))
        return self._notify(_("Sales and accounting documents migrated."), sticky=True)

    def action_sync_transactions_ops(self):
        """The operational documents: MOs, transfers, purchase orders."""
        self = self._migration_env()
        stats = self._txn_run(['manufacturing', 'pickings', 'purchases'])
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
