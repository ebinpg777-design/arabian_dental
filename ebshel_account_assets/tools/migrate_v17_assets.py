# -*- coding: utf-8 -*-
"""Bring the assets of an Odoo 17 `om_account_asset` system into ebshel_account_assets.

Run inside `odoo-bin shell` with the four CSVs the old database exports (see the
docstring of each loader for the SQL): the categories, the assets, the depreciation
lines with their journal entries, and those entries' lines. Environment:

    ASSETS_DIR   folder holding v17_categories.csv, v17_assets.csv, v17_lines.csv
    ASSETS_DRY   "1" (default) rolls back at the end; "0" commits

What it does, and why:
* Categories are made per ASSET CLASS - one per fixed-asset account - not per product
  as the old system had them (eleven, "LED TV", "LAPTOP-MSI"...): a bill line on an
  account must find one category, and the old ones all shared the same accounts.
* An asset's accounts are the ones its cost actually sits on (its bill line), its
  accumulated depreciation account the class's; the expense account is the one every
  old entry debited. The old categories pointed accumulated depreciation at the asset
  account itself, or at the expense account, in six cases of eleven.
* Posted depreciation entries already migrated as journal entries (`x_src_id`) are
  attached, not posted again; the rest of the old schedule is kept as it was planned,
  'to post' - including the months the old system never posted. Nothing is posted
  here and the categories start with automatic posting OFF: the accountant decides
  what to do with the backlog (post it, or skip it) before the nightly job runs.
* Idempotent through `migration.map` (lab_migration): a second run changes nothing.
"""
import csv
import os
from collections import defaultdict
from datetime import date

ASSETS_DIR = os.environ.get('ASSETS_DIR', '/tmp/v17_assets')
DRY = os.environ.get('ASSETS_DRY', '1') != '0'
EXPENSE_CODE = '200240'
JOURNAL_CODE = 'FAD'
# class name -> (asset account codes, accumulated depreciation account, made if missing)
CLASSES = {
    'Plant & Machinery':   (['101301'], ('200250', 'Acc.Depreciation-Machinery')),
    'Equipment':           (['101300'], ('200280', 'Acc.Depreciation-Equipment')),
    'Computers & Laptops': (['101500'], ('200260', 'Acc.depreciation-computer & laptop')),
    'Furniture & Fixtures': (['101600', '400202'], ('200230', 'Acc.Depreciation-Furniture & Fixtures')),
    'Vehicles':            (['101400'], ('200270', 'Acc.Depreciation-Vehicles')),
}
STATE = {'draft': 'draft', 'open': 'running', 'close': 'closed'}


def read(name):
    with open(os.path.join(ASSETS_DIR, name), newline='') as handle:
        return list(csv.DictReader(handle))


def money(v):
    return round(float(v or 0.0), 2)


company = env.company
Account = env['account.account'].with_company(company)
Map = env['migration.map'].sudo()


def mapped(model, src):
    row = Map.search([('dst_model', '=', model), ('src_id', '=', int(src))], limit=1)
    return row.dst_id if row else None


def remember(model, src, dst):
    Map.create({'dst_model': model, 'src_id': int(src), 'dst_id': int(dst)})


def account(code):
    acc = Account.search([('code', '=', code), ('company_ids', 'in', company.id)], limit=1) \
        or Account.search([('code', '=', code)], limit=1)
    if not acc:
        raise RuntimeError('account %s is not on this database' % code)
    return acc


def accumulated_account(code, name):
    acc = Account.search([('code', '=', code)], limit=1)
    if acc:
        if acc.account_type != 'asset_fixed':
            # an accumulated depreciation account typed as an expense (the old chart):
            # its credits reduced the P&L instead of the fixed assets
            print('  account %s %s: type %s -> asset_fixed' % (code, name, acc.account_type))
            acc.write({'account_type': 'asset_fixed'})
        return acc
    acc = Account.create({'code': code, 'name': name, 'account_type': 'asset_fixed', 'reconcile': False})
    print('  account %s %s: created' % (code, name))
    return acc


# ---------------------------------------------------------------- the categories
journal = env['account.journal'].search([('code', '=', JOURNAL_CODE), ('company_id', '=', company.id)], limit=1)
assert journal, 'journal %s' % JOURNAL_CODE
expense = account(EXPENSE_CODE)
Category = env['ebshel.asset.category']
by_asset_code, categories = {}, {}
for name, (codes, (acc_code, acc_name)) in CLASSES.items():
    cat = Category.search([('name', '=', name), ('company_id', '=', company.id)], limit=1)
    if not cat:
        accum = accumulated_account(acc_code, acc_name)
        cat = Category.create({
            'name': name, 'company_id': company.id, 'journal_id': journal.id,
            'asset_account_id': account(codes[0]).id, 'depreciation_account_id': accum.id,
            'expense_account_id': expense.id, 'method': 'linear', 'period': 'month', 'duration': 60,
            'prorata': 'none', 'salvage_pct': 0.0, 'auto_post': False, 'bill_trigger': 'draft',
            'trigger_account_ids': [(6, 0, [account(c).id for c in codes])],
            'note': 'Made by the Odoo 17 asset migration. Automatic posting is off until the '
                    'depreciation backlog of the migrated assets has been dealt with.'})
        print('  category %s: created (accounts %s / %s / %s)' % (name, codes, acc_code, EXPENSE_CODE))
    categories[name] = cat
    for code in codes:
        by_asset_code[code] = cat

old_categories = {r['id']: r for r in read('v17_categories.csv')}
for old_id, row in old_categories.items():
    if mapped('ebshel.asset.category', old_id):
        continue
    cat = by_asset_code.get(row['asset_code'])
    if cat:
        remember('ebshel.asset.category', old_id, cat.id)

# ---------------------------------------------------------------- the assets
Asset = env['ebshel.asset']
Line = env['ebshel.asset.line']
Move = env['account.move'].sudo()
Partner = env['res.partner'].sudo().with_context(active_test=False)
admin = env.ref('base.user_admin')
lines_by_asset = defaultdict(list)
for row in read('v17_lines.csv'):
    lines_by_asset[row['asset_id']].append(row)
for rows in lines_by_asset.values():
    rows.sort(key=lambda r: (int(r['sequence'] or 0), int(r['id'])))

made, skipped, attached, missing_moves, overdue = [], 0, 0, [], 0
today = date.today()
for row in sorted(read('v17_assets.csv'), key=lambda r: int(r['id'])):
    old_id = row['id']
    if mapped('ebshel.asset', old_id):
        skipped += 1
        continue
    value, salvage = money(row['value']), money(row['salvage_value'])
    old_cat = old_categories[row['category_id']]
    # the bill line the cost came in on: by amount, then by the start of the name
    bill_line = env['account.move.line']
    bill = Move.search([('x_src_id', '=', int(row['invoice_id'])), ('move_type', '=', 'in_invoice')], limit=1) \
        if row['invoice_id'] else Move
    if bill:
        candidates = bill.invoice_line_ids.filtered(
            lambda l: l.display_type == 'product' and l.account_id.account_type == 'asset_fixed' and not l.ebshel_asset_ids)
        by_amount = candidates.filtered(lambda l: abs(round(l.balance, 2) - value) < 0.05)
        by_name = candidates.filtered(lambda l: (l.name or '')[:30].strip().upper() == row['name'][:30].strip().upper())
        bill_line = (by_name & by_amount) or by_amount or by_name or candidates
        bill_line = bill_line[:1]
    asset_code = bill_line.account_id.code if bill_line else old_cat['asset_code']
    cat = by_asset_code.get(asset_code) or by_asset_code.get(old_cat['asset_code'])
    if not cat:
        raise RuntimeError('asset %s: no class for account %s' % (row['name'], asset_code))
    partner = Partner.search([('x_src_id', '=', int(row['partner_id']))], limit=1) if row['partner_id'] else Partner
    old_lines = lines_by_asset[old_id]
    first = old_lines[0]['depreciation_date'] if old_lines else row['date']
    one_shot = int(row['method_number'] or 1) == 1
    vals = {
        'name': row['name'], 'category_id': cat.id, 'company_id': company.id, 'user_id': admin.id,
        'partner_id': partner.id if partner else (bill.partner_id.id if bill else False),
        'bill_line_id': bill_line.id if bill_line else False,
        'purchase_date': row['date'], 'start_date': first,
        'purchase_value': value, 'salvage_value': salvage, 'already_depreciated': 0.0,
        'method': 'linear', 'period': 'month', 'duration': 1 if one_shot else int(row['method_number']),
        'prorata': 'none',
        'asset_account_id': (bill_line.account_id if bill_line else account(old_cat['asset_code'])).id,
        'depreciation_account_id': cat.depreciation_account_id.id,
        'expense_account_id': expense.id, 'journal_id': journal.id,
        'notes': '<p>Migrated from the Odoo 17 system on %s: asset #%s, reference %s, category "%s".</p>' % (
            today.isoformat(), old_id, row['code'] or '-', old_cat['name']),
    }
    asset = Asset.with_context(mail_create_nolog=True, tracking_disable=True).create(vals)
    remember('ebshel.asset', old_id, asset.id)
    state = STATE[row['state']]
    if state != 'draft':
        cumulative, line_vals, posted_here = 0.0, [], 0
        for old in old_lines:
            amount = money(old['amount'])
            cumulative += amount
            move = Move.search([('x_src_id', '=', int(old['move_id']))], limit=1) if old['move_id'] else Move
            posted = old['move_posted_check'] == 't' and bool(move) and move.state == 'posted'
            if old['move_posted_check'] == 't' and not posted:
                missing_moves.append((row['name'], old['move_id']))
            line_vals.append({
                'asset_id': asset.id, 'sequence': int(old['sequence'] or 0), 'name': old['name'] or '',
                'date': old['depreciation_date'], 'kind': 'depreciation', 'amount': amount,
                'cumulative': money(old['depreciated_value']) or cumulative,
                'remaining': money(old['remaining_value']) if old['remaining_value'] else value - cumulative,
                'state': 'posted' if posted else 'draft', 'move_id': move.id if posted else False,
            })
            if posted:
                move.write({'ebshel_asset_id': asset.id})
                attached += 1
                posted_here += 1
            elif old['depreciation_date'] <= today.isoformat():
                overdue += 1
        lines = Line.create(line_vals)
        for ln, old in zip(lines, old_lines):
            remember('ebshel.asset.line', old['id'], ln.id)
        asset.write({'state': state})
        asset._log('running', note='Migrated running from Odoo 17: %d of %d depreciation entries posted there' % (
            posted_here, len(old_lines)), day=today)
        if state == 'closed':
            asset._log('closed', note='Fully depreciated in the Odoo 17 system', day=today)
    else:
        asset._log('note', note='Migrated as a draft from Odoo 17: never confirmed there', day=today)
    made.append(asset)

env.flush_all()
print('categories: %d classes | old categories mapped: %d' % (len(categories), Map.search_count([('dst_model', '=', 'ebshel.asset.category')])))
print('assets: %d made, %d already there | entries attached: %d | posted entries not found here: %s'
      % (len(made), skipped, attached, missing_moves or 'none'))
print('depreciation lines dated up to today still to post: %d (categories have automatic posting OFF)' % overdue)
for asset in made:
    asset.invalidate_recordset()
    print('  %-9s %-44s %-20s %-8s value %12.2f dep %11.2f book %12.2f lines %3d posted %2d bill %s' % (
        asset.code, asset.name[:44], asset.category_id.name[:20], asset.state, asset.purchase_value,
        asset.depreciated_value, asset.book_value, len(asset.line_ids), asset.posted_count, asset.bill_id.name or '-'))
total_posted = sum(a.depreciated_value for a in made)
[(fad_credit,)] = env['account.move.line'].sudo()._read_group(
    [('move_id.journal_id', '=', journal.id), ('parent_state', '=', 'posted')], [], ['credit:sum'])
print('posted depreciation carried by the assets: %.2f | credits in the %s journal: %.2f' % (total_posted, JOURNAL_CODE, fad_credit or 0.0))
if DRY:
    env.cr.rollback()
    print('DRY RUN: rolled back')
else:
    env.cr.commit()
    print('COMMITTED')
