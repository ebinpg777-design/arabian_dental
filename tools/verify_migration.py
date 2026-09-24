# -*- coding: utf-8 -*-
"""Compare the migrated Odoo 19 database with its Odoo 17 source, figure by figure.

    instances/arabian_dental/venv/bin/python projects/arabian_dental/tools/verify_migration.py adl_prod_v17 arabian_dental_live
"""
import sys

import psycopg2

SRC = sys.argv[1] if len(sys.argv) > 1 else 'adl_prod_v17'
DST = sys.argv[2] if len(sys.argv) > 2 else 'arabian_dental_live'


def connect(db):
    return psycopg2.connect(host='127.0.0.1', user='odoo', password='odoo', dbname=db).cursor()


s, d = connect(SRC), connect(DST)

CHECKS = [
    # label, source SQL, target SQL
    ('sale orders (all)', "select count(*) from sale_order", "select count(*) from sale_order where x_src_id is not null"),
    ('sale orders confirmed', "select count(*) from sale_order where state='sale'",
     "select count(*) from sale_order where x_src_id is not null and state='sale'"),
    ('sale order lines', "select count(*) from sale_order_line where display_type is null",
     "select count(*) from sale_order_line where x_src_id is not null and display_type is null"),
    ('order value (confirmed, INR)', "select round(sum(amount_total)) from sale_order where state='sale'",
     "select round(sum(amount_total)) from sale_order where state='sale' and x_src_id is not null"),
    ('customer invoices posted', "select count(*) from account_move where move_type='out_invoice' and state='posted'",
     "select count(*) from account_move where move_type='out_invoice' and state='posted' and x_src_id is not null"),
    ('credit notes posted', "select count(*) from account_move where move_type='out_refund' and state='posted'",
     "select count(*) from account_move where move_type='out_refund' and state='posted' and x_src_id is not null"),
    ('vendor bills posted', "select count(*) from account_move where move_type='in_invoice' and state='posted'",
     "select count(*) from account_move where move_type='in_invoice' and state='posted' and x_src_id is not null"),
    ('invoiced total (posted out_invoice)', "select round(sum(amount_total)) from account_move where move_type='out_invoice' and state='posted'",
     "select round(sum(amount_total)) from account_move where move_type='out_invoice' and state='posted' and x_src_id is not null"),
    ('journal entries posted', "select count(*) from account_move where move_type='entry' and state='posted'",
     "select count(*) from account_move where move_type='entry' and state='posted' and x_src_id is not null"),
    ('receivable balance', "select round(sum(balance)) from account_move_line l join account_account a on a.id=l.account_id where a.account_type='asset_receivable' and l.parent_state='posted'",
     "select round(sum(balance)) from account_move_line l join account_account a on a.id=l.account_id where a.account_type='asset_receivable' and l.parent_state='posted'"),
    ('payable balance', "select round(sum(balance)) from account_move_line l join account_account a on a.id=l.account_id where a.account_type='liability_payable' and l.parent_state='posted'",
     "select round(sum(balance)) from account_move_line l join account_account a on a.id=l.account_id where a.account_type='liability_payable' and l.parent_state='posted'"),
    ('bank+cash balance', "select round(sum(balance)) from account_move_line l join account_account a on a.id=l.account_id where a.account_type='asset_cash' and l.parent_state='posted'",
     "select round(sum(balance)) from account_move_line l join account_account a on a.id=l.account_id where a.account_type='asset_cash' and l.parent_state='posted'"),
    ('income total', "select round(sum(credit-debit)) from account_move_line l join account_account a on a.id=l.account_id where a.account_type in ('income','income_other') and l.parent_state='posted'",
     "select round(sum(credit-debit)) from account_move_line l join account_account a on a.id=l.account_id where a.account_type in ('income','income_other') and l.parent_state='posted'"),
    ('invoices paid', "select count(*) from account_move where move_type='out_invoice' and state='posted' and payment_state='paid'",
     "select count(*) from account_move where move_type='out_invoice' and state='posted' and payment_state='paid' and x_src_id is not null"),
    ('invoices partial', "select count(*) from account_move where move_type='out_invoice' and state='posted' and payment_state='partial'",
     "select count(*) from account_move where move_type='out_invoice' and state='posted' and payment_state='partial' and x_src_id is not null"),
    ('invoices in_payment', "select count(*) from account_move where move_type='out_invoice' and state='posted' and payment_state='in_payment'",
     "select count(*) from account_move where move_type='out_invoice' and state='posted' and payment_state='in_payment' and x_src_id is not null"),
    ('invoices not_paid', "select count(*) from account_move where move_type='out_invoice' and state='posted' and payment_state='not_paid'",
     "select count(*) from account_move where move_type='out_invoice' and state='posted' and payment_state='not_paid' and x_src_id is not null"),
    ('open receivable on invoices', "select round(sum(amount_residual)) from account_move where move_type='out_invoice' and state='posted'",
     "select round(sum(amount_residual)) from account_move where move_type='out_invoice' and state='posted' and x_src_id is not null"),
    ('partial reconciles', "select count(*) from account_partial_reconcile", "select count(*) from account_partial_reconcile"),
    ('cheques received', "select count(*) from account_payment where cheque_status is not null and payment_type='inbound'",
     "select count(*) from lab_cheque where x_src_id is not null"),
    ('transfers', "select count(*) from stock_picking", "select count(*) from stock_picking where x_src_id is not null"),
    ('transfers done', "select count(*) from stock_picking where state='done'", "select count(*) from stock_picking where state='done' and x_src_id is not null"),
    ('purchase orders', "select count(*) from purchase_order", "select count(*) from purchase_order where x_src_id is not null"),
    ('on-hand qty (internal)', "select round(sum(q.quantity)) from stock_quant q join stock_location l on l.id=q.location_id where l.usage='internal'",
     "select round(sum(q.quantity)) from stock_quant q join stock_location l on l.id=q.location_id where l.usage='internal'"),
    ('attachments (manual)', "select count(*) from ir_attachment where res_field is null and res_model in ('sale.order','res.partner','product.template','hr.employee','account.move','purchase.order','stock.picking')",
     "select count(*) from migration_map where dst_model='ir.attachment'"),
    ('partners (customers)', "select count(*) from res_partner where customer_rank>0 or is_customer",
     "select count(*) from res_partner where x_src_id is not null and is_clinic"),
    ('users (internal)', "select count(*) from res_users where share=false and active", "select count(*) from res_users where share=false and active"),
    ('products', "select count(*) from product_template", "select count(*) from product_template where x_src_id is not null"),
    ('employees', "select count(*) from hr_employee", "select count(*) from hr_employee where x_src_id is not null"),
]


def one(cur, sql):
    try:
        cur.execute(sql)
        row = cur.fetchone()
        return row[0] if row else None
    except Exception as e:
        cur.connection.rollback()
        return 'ERR %s' % str(e).split('\n')[0][:40]


print('%-36s %14s %14s %8s' % ('figure', SRC[:14], DST[:14], 'match'))
for label, sq, dq in CHECKS:
    a, b = one(s, sq), one(d, dq)
    same = 'yes' if a == b else ('' if isinstance(a, str) or isinstance(b, str) else 'NO')
    print('%-36s %14s %14s %8s' % (label, a, b, same))
