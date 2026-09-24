# -*- coding: utf-8 -*-
"""Neutralise a restored copy of the lab's Odoo 17 database, by SQL.

`odoo-bin neutralize` needs Odoo 17 code, which is not installed here, and it
skips every custom module anyway. This applies what the core neutralize.sql files
do (mail servers, crons, queued mail, push keys, IAP tokens, payment providers,
base URL) plus the lab's own: e-invoice / e-waybill portal credentials.

    instances/arabian_dental/venv/bin/python projects/arabian_dental/tools/neutralise_source.py adl_prod_v17
"""
import sys

import psycopg2

DB = sys.argv[1] if len(sys.argv) > 1 else 'adl_prod_v17'
conn = psycopg2.connect(host='127.0.0.1', user='odoo', password='odoo', dbname=DB)
cur = conn.cursor()
cur.execute("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema='public'")
COLS = {}
for table, column in cur.fetchall():
    COLS.setdefault(table, set()).add(column)

steps = []


def run(label, sql, need=()):
    """Apply one step if every (table[, column]) it needs exists."""
    ok = all(n[0] in COLS and (len(n) == 1 or n[1] in COLS[n[0]]) for n in need)
    if ok:
        cur.execute(sql)
        steps.append((label, cur.rowcount))
    else:
        steps.append((label, 'skipped (absent)'))


run('mail servers off', "UPDATE ir_mail_server SET active=false", (('ir_mail_server',),))
run('dummy mail server', """INSERT INTO ir_mail_server(name, smtp_port, smtp_host, smtp_encryption, active, smtp_authentication)
    SELECT 'neutralization - disable emails', 1025, 'invalid', 'none', true, 'login'
    WHERE NOT EXISTS (SELECT 1 FROM ir_mail_server WHERE name='neutralization - disable emails')""",
    (('ir_mail_server',),))
run('crons off', """UPDATE ir_cron SET active=false WHERE id NOT IN (
    SELECT res_id FROM ir_model_data WHERE model='ir.cron' AND name='autovacuum_job' AND module='base')""",
    (('ir_cron',),))
run('neutralized flag', """INSERT INTO ir_config_parameter (key, value) VALUES ('database.is_neutralized', 'true')
    ON CONFLICT (key) DO UPDATE SET value='true'""")
run('webhooks off', "UPDATE ir_act_server SET webhook_url='neutralization - disable webhook' WHERE state='webhook'",
    (('ir_act_server', 'webhook_url'),))
run('templates no server', "UPDATE mail_template SET mail_server_id=NULL", (('mail_template',),))
run('fetchmail off', "UPDATE fetchmail_server SET active=false", (('fetchmail_server',),))
run('push keys', "DELETE FROM ir_config_parameter WHERE key IN ('mail.web_push_vapid_private_key','mail.web_push_vapid_public_key','mail.sfu_server_key')")
run('mail_push', "DELETE FROM mail_push", (('mail_push',),))
run('mail_push_device', "DELETE FROM mail_push_device", (('mail_push_device',),))
run('outgoing mail queue', "DELETE FROM mail_mail WHERE state IN ('outgoing','exception')", (('mail_mail',),))
run('iap tokens', "UPDATE iap_account SET account_token = REGEXP_REPLACE(coalesce(account_token,''), '(\\+.*)?$', '+disabled')",
    (('iap_account', 'account_token'),))
run('sms queue', "DELETE FROM sms_sms WHERE state IN ('outgoing','error')", (('sms_sms',),))
run('payment providers', "UPDATE payment_provider SET state='disabled' WHERE state NOT IN ('test','disabled')",
    (('payment_provider',),))
run('gmail secrets', "DELETE FROM ir_config_parameter WHERE key IN ('google_gmail_client_id','google_gmail_client_secret')")
run('base url', "UPDATE ir_config_parameter SET value='http://localhost:8017' WHERE key='web.base.url'")
run('alias domain', "UPDATE mail_alias_domain SET name='localhost'", (('mail_alias_domain',),))
run('totp devices', "DELETE FROM auth_totp_device", (('auth_totp_device',),))
run('totp secrets', "UPDATE res_users SET totp_secret=NULL WHERE totp_secret IS NOT NULL", (('res_users', 'totp_secret'),))
# the lab's own: GSP portal credentials of the e-invoice module
for table in ('einv_users', 'ewaybill_users'):
    cols = [c for c in COLS.get(table, ()) if any(k in c for k in ('user', 'pass', 'key', 'token'))]
    if cols:
        run('%s credentials' % table, "UPDATE %s SET %s" % (table, ', '.join("%s='neutralized'" % c for c in cols)))
conn.commit()
for label, result in steps:
    print('%-26s %s' % (label, result))
cur.execute("SELECT count(*) FROM ir_cron WHERE active")
print('active crons left:', cur.fetchone()[0])
