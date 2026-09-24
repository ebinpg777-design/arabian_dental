# -*- coding: utf-8 -*-
"""Seed an orthodontic demo dataset that exercises every module of the suite.

Run against a database installed WITHOUT Odoo's demo data::

    instances/arabian_dental/venv/bin/python odoo19/odoo-bin shell \
        -c instances/arabian_dental/arabian_dental.conf -d arabian_dental --no-http \
        < projects/arabian_dental/tools/seed_demo.py

    SEED_SCALE=0.15 ... < tools/seed_demo.py      # a small smoke run (a few days)

Nothing here is inserted as raw rows. Every case goes through the suite's own doors:
the executive's visit and case slip, the maker-checker gate, the confirmed order that
raises the manufacturing order, the station board's accept / hand-over on each bench,
the delivery record, the invoice, and the cash or cheque that settles it. That is what
makes the dashboards, the desks, the station board, the collections screens, the CEO
hub and the doctor portal all show a coherent lab rather than a pile of records.

Dates are spread over the last ~90 days (orders) and ~45 working days (field visits);
`create_date` is back-dated at the end so age-based screens read correctly.

The people, clinics and patients are fictitious. The catalogue is a real orthodontic
laboratory's range; prices are indicative INR.
"""
import logging
import os
import random
from datetime import date, datetime, time, timedelta

from odoo import fields
from odoo.exceptions import UserError

_logger = logging.getLogger('seed')
random.seed(20260922)

SCALE = float(os.environ.get('SEED_SCALE', '1.0'))
IST = timedelta(hours=5, minutes=30)
TODAY = date.today()
NOW = datetime.now()

env = env  # noqa: F821  (provided by `odoo shell`)
env = env(context=dict(env.context, tz='Asia/Kolkata', tracking_disable=True, mail_notrack=True,
                       mail_create_nolog=True, mail_create_nosubscribe=True))
company = env.company


# ------------------------------------------------------------------ helpers
def ist(d, hour, minute=0):
    """A wall-clock time in Kerala, stored as the UTC Odoo keeps."""
    return datetime.combine(d, time(hour, minute)) - IST


def vals_for(model, **kw):
    """Only the keys the model actually has — optional columns differ by build."""
    f = env[model]._fields
    return {k: v for k, v in kw.items() if k in f}


def working_days(back, until=None):
    """Mon–Sat, oldest first, ending yesterday (or `until`)."""
    end = until or (TODAY - timedelta(days=1))
    days = [end - timedelta(days=i) for i in range(back * 7 // 6 + 7)]
    return sorted(d for d in days if d.weekday() != 6 and d <= end)[-back:]


def pick(seq, k=1):
    return random.sample(seq, k) if k > 1 else random.choice(seq)


def chance(p):
    return random.random() < p


def param(key, value):
    env['ir.config_parameter'].sudo().set_param(key, value)


def commit(label):
    env.cr.commit()
    _logger.info('seed: %s committed', label)
    print('seed:', label)


def safe(label, fn, *args):
    """One item behind a savepoint; a failure is logged and the loop goes on."""
    try:
        with env.cr.savepoint():
            return fn(*args)
    except Exception as exc:   # noqa: BLE001
        _logger.exception('seed: %s failed', label)
        print('seed: %s failed: %s: %s' % (label, type(exc).__name__, str(exc)[:140]))


def try_block(label, fn):
    """A whole optional block behind a savepoint: one module's quirk must not kill the run."""
    try:
        with env.cr.savepoint():
            fn()
        print('seed:', label)
    except Exception as exc:   # noqa: BLE001
        _logger.exception('seed: %s FAILED', label)
        print('seed: %s SKIPPED (%s: %s)' % (label, type(exc).__name__, str(exc)[:160]))


# ------------------------------------------------------------------ 0. settings
param('lab_order_control.require_order_verification', 'True')
param('lab_order_control.credit_limit_policy', 'warn')
param('lab_finance_ops.require_payment_approval', 'False')
param('lab_fieldwork.require_odo_photo', 'False')
param('lab_fieldwork.require_visit_photo', 'False')
param('lab_fieldwork.visit_radius_m', '300')
param('lab_whatsapp.notify_invoice', 'True')

company.write(vals_for('res.company', gst_number='32AAFCA1234K1Z5', pan_number='AAFCA1234K',
                       company_warning=False, emergency_service_perc=25.0))

# ------------------------------------------------------------------ 1. people
Users = env['res.users']
GROUPS = {k: env.ref(v) for k, v in {
    'user': 'base.group_user', 'portal': 'base.group_portal',
    'exec': 'lab_fieldwork.group_fieldwork_executive',
    'fw_mgr': 'lab_fieldwork.group_fieldwork_manager',
    'ops_mgr': 'lab_fieldwork.group_fieldwork_ops_manager',
    'mkt_mgr': 'lab_fieldwork.group_fieldwork_marketing_manager',
    'fw_admin': 'lab_fieldwork.group_fieldwork_admin',
    'ceo': 'lab_ceo_dashboard.group_lab_executive',
    'bench': 'lab_workcenter_scan.group_workcenter_bound',
    'prod_mgr': 'lab_workcenter_scan.group_production_manager',
    'mrp_user': 'mrp.group_mrp_user', 'mrp_mgr': 'mrp.group_mrp_manager',
    'sale_user': 'sales_team.group_sale_salesman', 'sale_mgr': 'sales_team.group_sale_manager',
    'acc_mgr': 'account.group_account_manager', 'acc_inv': 'account.group_account_invoice',
    'order_entry': 'lab_order_control.group_lab_order_entry',
    'checker': 'lab_order_control.group_lab_order_checker',
    'credit': 'lab_order_control.group_lab_credit_override',
    'cheque': 'lab_finance_ops.group_lab_cheque_user',
    'approver': 'lab_finance_ops.group_lab_payment_approver',
    'config': 'lab_access_control.group_lab_config_manager',
    'stock_user': 'stock.group_stock_user',
}.items()}
for _key, _xmlid in {'petty_officer': 'petty_cash.group_petty_cash_officer', 'petty_mgr': 'petty_cash.group_petty_cash_manager'}.items():
    _g = env.ref(_xmlid, raise_if_not_found=False)
    if _g:
        GROUPS[_key] = _g


def make_user(login, name, groups, **extra):
    user = Users.search([('login', '=', login)], limit=1)
    if user:
        return user
    user = Users.with_context(no_reset_password=True).create(dict({
        'login': login, 'name': name, 'tz': 'Asia/Kolkata',
        'group_ids': [(6, 0, [GROUPS[g].id for g in groups])],
    }, **vals_for('res.users', **extra)))
    user.password = 'demo'
    user.partner_id.write({'phone': extra.get('mobile') or ''})
    return user


EXECS = [
    make_user('rahul', 'Rahul K P', ['user', 'exec', 'stock_user'], mobile='+91 97450 11001'),
    make_user('faisal', 'Faisal Ahammed', ['user', 'exec', 'stock_user'], mobile='+91 97450 11002'),
    make_user('sneha', 'Sneha Menon', ['user', 'exec', 'stock_user'], mobile='+91 97450 11003'),
    make_user('anas', 'Anas Muhammed', ['user', 'exec', 'stock_user'], mobile='+91 97450 11004'),
]
OPS = make_user('shafeeq', 'Shafeeq Rahman', ['user', 'fw_mgr', 'ops_mgr', 'sale_mgr', 'stock_user', 'mrp_user'] + [g for g in ('petty_officer', 'petty_mgr') if g in GROUPS])
MKT = make_user('nimmy', 'Nimmy Thomas', ['user', 'fw_mgr', 'mkt_mgr', 'sale_user'])
PRIYA = make_user('priya', 'Priya Nair', ['user', 'order_entry', 'sale_user', 'stock_user', 'acc_inv'])
SURESH = make_user('suresh', 'Suresh Babu', ['user', 'checker', 'order_entry', 'sale_mgr', 'stock_user', 'credit'])
LATHA = make_user('latha', 'Latha Krishnan', ['user', 'acc_mgr', 'cheque', 'approver', 'credit', 'sale_user'] + [g for g in ('petty_officer', 'petty_mgr') if g in GROUPS])
PROD = make_user('vishnu', 'Vishnu Das', ['user', 'prod_mgr', 'mrp_mgr', 'mrp_user', 'stock_user'])
TECHS = [
    make_user('sajid', 'Sajid Ali', ['user', 'mrp_user', 'bench']),
    make_user('arjun', 'Arjun S', ['user', 'mrp_user', 'bench']),
    make_user('deepa', 'Deepa Raj', ['user', 'mrp_user', 'bench']),
    make_user('manoj', 'Manoj Kumar', ['user', 'mrp_user', 'bench']),
    make_user('salma', 'Salma Beevi', ['user', 'mrp_user', 'bench']),
    make_user('jithin', 'Jithin Jose', ['user', 'mrp_user', 'bench']),
]
CEO = make_user('rasheed', 'Dr. Rasheed A', ['user', 'ceo', 'fw_mgr', 'ops_mgr', 'mkt_mgr', 'fw_admin',
                                             'sale_mgr', 'acc_mgr', 'mrp_mgr', 'prod_mgr', 'checker',
                                             'credit', 'cheque', 'approver', 'config', 'stock_user'])
admin = env.ref('base.user_admin')
admin.write({'group_ids': [(4, GROUPS[g].id) for g in
                           ('ceo', 'fw_mgr', 'ops_mgr', 'mkt_mgr', 'fw_admin', 'prod_mgr', 'checker',
                            'credit', 'cheque', 'approver', 'config', 'order_entry', 'exec')]})
# An employee record per field person, so attendance (Start the day) works on My Day.
if 'hr.employee' in env:
    Employee = env['hr.employee']
    for u in EXECS + [OPS, MKT, PRIYA, SURESH, LATHA, PROD] + TECHS:
        if not Employee.search([('user_id', '=', u.id)], limit=1):
            Employee.create(vals_for('hr.employee', name=u.name, user_id=u.id, work_email=u.email,
                                     mobile_phone=u.partner_id.phone, company_id=company.id))
commit('people')

# ------------------------------------------------------------------ 2. routes & clinics
TOWNS = {   # (lat, lon)
    'Manjeri': (11.1203, 76.1200), 'Wandoor': (11.1490, 76.2590), 'Mampad': (11.1830, 76.2030),
    'Pandikkad': (11.1180, 76.2450), 'Edakkara': (11.2830, 76.3080), 'Nilambur': (11.2760, 76.2250),
    'Perinthalmanna': (10.9760, 76.2280), 'Melattur': (11.0480, 76.2810),
    'Malappuram': (11.0510, 76.0710), 'Kottakkal': (10.9990, 76.0000), 'Vengara': (11.0490, 75.9730),
    'Tirur': (10.9140, 75.9220), 'Valanchery': (10.8890, 76.0730), 'Kondotty': (11.1410, 75.9610),
    'Areekode': (11.2260, 75.9970), 'Kozhikode': (11.2588, 75.7804), 'Feroke': (11.1830, 75.8380),
    'Ramanattukara': (11.1730, 75.8680),
}
ROUTES = [
    ('Manjeri – Wandoor', EXECS[0], ['Manjeri', 'Wandoor', 'Mampad', 'Pandikkad']),
    ('Malappuram – Kottakkal', EXECS[1], ['Malappuram', 'Kottakkal', 'Vengara', 'Tirur', 'Valanchery']),
    ('Perinthalmanna – Nilambur', EXECS[2], ['Perinthalmanna', 'Melattur', 'Nilambur', 'Edakkara']),
    ('Kozhikode – Areekode', EXECS[3], ['Kozhikode', 'Feroke', 'Ramanattukara', 'Kondotty', 'Areekode']),
]
CLINIC_NAMES = ['Smile Dental Clinic', 'Dentcare', 'Pearl Dental', 'Family Dental Centre', 'Bright Smiles',
                'Ortho Care Clinic', 'Al Ameen Dental', 'Green Dental Studio', 'Sunrise Dental', 'Care Dental',
                'Kids Dental Home', 'Confident Smile', 'Royal Dental Clinic', 'City Dental Hospital',
                'Aster Dental', 'Noble Dental Care', 'Lotus Dental Clinic', 'Prime Dental', 'Apex Orthodontics',
                'Metro Dental', 'Blue Tooth Dental', 'Sree Dental Clinic', 'Crescent Dental', 'Unity Dental Care']
DOCTORS = ['Dr. Anjali Menon', 'Dr. Muhammed Shafi', 'Dr. Reshma K', 'Dr. Vinod Kumar', 'Dr. Fathima Nasrin',
           'Dr. Hari Prasad', 'Dr. Jasmin Rasheed', 'Dr. Arun Raj', 'Dr. Sithara Beegum', 'Dr. Nikhil Nair',
           'Dr. Saleena T', 'Dr. Sreejith P', 'Dr. Aysha Musthafa', 'Dr. Rajesh Menon', 'Dr. Divya Krishnan',
           'Dr. Basheer Ahammed', 'Dr. Lakshmi Priya', 'Dr. Yaseen K', 'Dr. Gopika S', 'Dr. Firoz Khan',
           'Dr. Neha Thomas', 'Dr. Sajeer M', 'Dr. Meera Nambiar', 'Dr. Abdul Kareem']
Team = env['crm.team']
Partner = env['res.partner']
routes, clinics = [], []
n_clinic = 0
for name, executive, towns in ROUTES:
    team = Team.search([('name', '=', name)], limit=1) or Team.create(vals_for(
        'crm.team', name=name, user_id=executive.id, member_ids=[(4, executive.id)],
        lab_sales_target=350000.0, lab_collection_target=85.0, use_quotations=True))   # collection target is a percentage
    routes.append((team, executive, towns))
    for i in range(int(11 * SCALE) + 4):
        town = towns[i % len(towns)]
        cname = '%s, %s' % (CLINIC_NAMES[n_clinic % len(CLINIC_NAMES)], town)
        lat, lon = TOWNS[town]
        clinic = Partner.create(vals_for(
            'res.partner', name=cname, is_company=True, is_clinic=True, team_id=team.id,
            city=town, street='%d %s Road' % (random.randint(2, 88), pick(['Main', 'Bypass', 'Temple', 'Market', 'Hospital'])),
            zip=str(random.choice([676121, 676122, 676123, 676505, 676519, 676503, 679322, 673001, 673632, 676306])),
            state_id=env.ref('base.state_in_kl').id, country_id=env.ref('base.in').id,
            phone='+91 %d %05d' % (random.choice([94470, 96450, 98470, 90480, 85900]), random.randint(10000, 99999)),
            partner_latitude=lat + random.uniform(-0.012, 0.012), partner_longitude=lon + random.uniform(-0.012, 0.012),
            credit_limit=random.choice([0, 0, 25000, 50000, 75000, 100000]),
            credit_limit_policy=random.choices(['company', 'warn', 'block'], weights=[70, 22, 8])[0],
            customer_rank=1, whatsapp_optin=True,
            whatsapp_number='+91 9%09d' % random.randint(100000000, 999999999),
            gst_number=None, contact_person=DOCTORS[n_clinic % len(DOCTORS)]))
        doctor = Partner.create(vals_for(
            'res.partner', name=DOCTORS[n_clinic % len(DOCTORS)], parent_id=clinic.id, is_doctor=True,
            function='Orthodontist' if chance(.35) else 'Dental Surgeon',
            phone=clinic.phone, whatsapp_number=clinic.whatsapp_number if 'whatsapp_number' in Partner._fields else None,
            whatsapp_optin=True, dci_number='KL-%05d' % random.randint(1000, 99999),
            email='%s@example.com' % DOCTORS[n_clinic % len(DOCTORS)].lower().replace('dr. ', '').replace(' ', '.')))
        clinics.append((clinic, doctor, team, executive))
        n_clinic += 1
commit('%d routes, %d clinics' % (len(routes), len(clinics)))

# ------------------------------------------------------------------ 3. catalogue & benches
WC = {}
Workcenter = env['mrp.workcenter']
for code, name, finisher, heads in [
    ('CAST', 'Model Pouring', False, [TECHS[0]]),
    ('WIRE', 'Wire Bending', False, [TECHS[1], TECHS[2]]),
    ('BAND', 'Banding & Soldering', False, [TECHS[3]]),
    ('ACRY', 'Acrylic', False, [TECHS[4]]),
    ('TRIM', 'Trimming & Polishing', True, [TECHS[5]]),
    ('QC', 'Quality Check', False, [PROD]),
    ('PACK', 'Packing & Dispatch', False, [PROD]),
]:
    wc = Workcenter.search([('name', '=', name)], limit=1) or Workcenter.create(vals_for(
        'mrp.workcenter', name=name, code=code, is_finisher=finisher, scan_confirm=False,
        head_user_ids=[(6, 0, [h.id for h in heads])], users=[(6, 0, [h.id for h in heads] + [t.id for t in TECHS])],
        time_efficiency=100, default_capacity=1))
    WC[code] = wc
FLOWS = {
    'removable': ['CAST', 'WIRE', 'ACRY', 'TRIM', 'QC', 'PACK'],
    'fixed': ['CAST', 'BAND', 'WIRE', 'TRIM', 'QC', 'PACK'],
    'functional': ['CAST', 'WIRE', 'ACRY', 'TRIM', 'QC', 'PACK'],
    'aligner': ['CAST', 'ACRY', 'TRIM', 'QC', 'PACK'],
    'retainer': ['CAST', 'WIRE', 'ACRY', 'TRIM', 'PACK'],
}
CATALOGUE = [   # (category, flow, [(name, price, minutes)])
    ('Classic Removable Appliances', 'removable', [
        ("Hawley's Retainer", 1400, 55), ('Hawley with Anterior Bite Plane', 1700, 65),
        ('Expansion Plate with Midline Screw', 2200, 75), ('Active Plate with Finger Springs', 2000, 70),
        ('Removable Space Maintainer', 1500, 50), ('Habit Breaker (Tongue Crib) — Removable', 1800, 60),
        ('Inclined Plane', 1600, 55), ('Bite Plate (Posterior)', 1700, 55)]),
    ('Classic Fixed Appliances', 'fixed', [
        ('Hyrax Expander', 4200, 120), ('Quad Helix', 3200, 95), ('Nance Palatal Arch', 2600, 80),
        ('Transpalatal Arch (TPA)', 2400, 75), ('Lingual Holding Arch', 2500, 80), ('Band and Loop Space Maintainer', 1900, 60),
        ('Fixed Tongue Crib', 2800, 85), ('Distal Jet', 5200, 140), ('Pendulum Appliance', 4800, 130)]),
    ('Orthopaedic & Myofunctional Appliances', 'functional', [
        ('Twin Block', 4800, 150), ('Frankel FR-II', 6200, 190), ('Bionator', 4400, 140), ('Activator', 4000, 130),
        ('Reverse Pull Face Mask', 5500, 90), ('Headgear with Facebow', 3800, 80), ('Chin Cup', 2200, 50),
        ('Herbst Appliance (Cast Splint)', 7800, 220), ('Forsus Fitting Set-up', 3500, 90)]),
    ('Speciality & Aligners', 'aligner', [
        ('Clear Aligner — Single Arch (per set)', 6500, 90), ('Clear Aligner — Both Arches (per set)', 11500, 150),
        ('Essix Retainer', 1300, 40), ('Bleaching Tray', 900, 35), ('Night Guard (Hard)', 1800, 55),
        ('Night Guard (Soft)', 1400, 45), ('Sports Mouth Guard', 1600, 50), ('Tooth Positioner', 5200, 150),
        ('Digital Study Model (printed pair)', 1200, 40)]),
    ('Retainers', 'retainer', [
        ('Begg Retainer', 1500, 55), ('Wrap-around Retainer', 1600, 60), ('Fixed Lingual Retainer (lab-made)', 1700, 60),
        ('Spring Retainer', 1900, 70)]),
]
Category = env['product.category']
Product = env['product.template']
mto = env.ref('stock.route_warehouse0_mto', raise_if_not_found=False)
if mto and not mto.active:
    mto.active = True
manufacture = env.ref('mrp.route_warehouse0_manufacture')
route_ids = [r.id for r in (mto, manufacture) if r]
products_by_family = {}
all_products = []
Bom = env['mrp.bom']
for cat_name, flow, items in CATALOGUE:
    categ = Category.search([('name', '=', cat_name)], limit=1) or Category.create({'name': cat_name})
    for name, price, minutes in items:
        tmpl = Product.create(vals_for(
            'product.template', name=name, categ_id=categ.id, type='consu', is_storable=False, sale_ok=True,
            purchase_ok=False, list_price=price, standard_price=round(price * 0.38),
            route_ids=[(6, 0, route_ids)], incentive_price=round(price * 0.05),
            default_code='ADL-%s-%03d' % (flow[:3].upper(), len(all_products) + 1)))
        bom = Bom.create({'product_tmpl_id': tmpl.id, 'product_qty': 1.0, 'type': 'normal'})
        steps = FLOWS[flow]
        share = [0.15, 0.35, 0.25, 0.15, 0.05, 0.05][:len(steps)]
        for i, code in enumerate(steps):
            env['mrp.routing.workcenter'].create({
                'bom_id': bom.id, 'workcenter_id': WC[code].id, 'name': WC[code].name,
                'sequence': (i + 1) * 10, 'time_cycle_manual': max(5, round(minutes * share[i]))})
        products_by_family.setdefault(flow, []).append(tmpl.product_variant_id)
        all_products.append(tmpl.product_variant_id)
Colour = env['product.colour']
COLOURS = [Colour.create({'name': n}) for n in ['Clear', 'Pink', 'Sky Blue', 'Green', 'Purple', 'Glitter Red', 'Two-tone']]
SendThrough = env['send.through']
SENDS = [SendThrough.create({'name': n}) for n in ['Executive', 'By Hand', 'Courier']]
Courier = env['lab.courier']
COURIERS = [Courier.create(vals_for('lab.courier', name=n, code=c, transit_days=d, phone=p,
                                    tracking_url=u))
            for n, c, d, p, u in [
                ('Professional Couriers', 'PROF', 2, '0483 2766000', 'https://www.tpcindia.com/Tracking2014.aspx?id={awb}'),
                ('DTDC', 'DTDC', 2, '0483 2733100', 'https://www.dtdc.in/tracking.asp?awb={awb}'),
                ('India Post Speed Post', 'IPOST', 3, '0483 2766111', 'https://www.indiapost.gov.in/_layouts/15/dop.portal.tracking/trackconsignment.aspx?awb={awb}')]]
for name, code, delivery in [('Round', 'ROUND', False), ('Collect case', 'CASE', False), ('Collect payment', 'PAY', False),
                             ('Delivery', 'DELIVER', True), ('New clinic', 'NEW', False), ('Complaint', 'COMPLAINT', False)]:
    env['lab.visit.purpose'].create(vals_for('lab.visit.purpose', name=name, code=code, is_delivery=delivery))
for name, code in [('Case collected', 'CASE'), ('Payment received', 'PAY'), ('Delivered', 'DELIVERED'),
                   ('Doctor absent', 'ABSENT'), ('Follow up', 'FOLLOW'), ('Rework collected', 'REWORK')]:
    env['lab.visit.outcome'].create(vals_for('lab.visit.outcome', name=name, code=code))
RedoReason = env['lab.redo.reason']
if not RedoReason.search([], limit=1):
    for n in ['Wire fracture', 'Acrylic bubble', 'Fit adjustment', 'Wrong colour', 'Clasp retention']:
        RedoReason.create({'name': n})
ReworkReason = env['lab.rework.reason']
REWORK_REASONS = ReworkReason.search([]) or ReworkReason.create([{'name': n} for n in [
    'Does not fit', 'Broken in use', 'Patient discomfort', 'Design change', 'Wire adjustment']])
commit('%d products in %d families, %d benches' % (len(all_products), len(products_by_family), len(WC)))

# ------------------------------------------------------------------ 4. beats, targets, floats, whatsapp
Beat = env['lab.beat']
BEATS = {}
for team, executive, towns in routes:
    mine = [c for c in clinics if c[2] == team]
    for wd in range(6):
        chunk = mine[wd::6] if len(mine) > 12 else mine[:7 + wd % 3]
        if not chunk:
            chunk = mine[:6]
        beat = Beat.create(vals_for('lab.beat', name='%s — %s' % (towns[wd % len(towns)], ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][wd]),
                                    code='%s-%d' % (team.name[:3].upper(), wd), weekday=str(wd), user_id=executive.id,
                                    partner_ids=[(6, 0, [c[0].id for c in chunk])]))
        BEATS[(executive.id, wd)] = beat
Target = env['lab.target']
for m in (TODAY.replace(day=1), (TODAY.replace(day=1) - timedelta(days=1)).replace(day=1)):
    for team, executive, towns in routes:
        Target.create(vals_for('lab.target', user_id=executive.id, month=m, state='open',
                               goal_visits=140, goal_orders=90, goal_value=350000, goal_collect=300000))
# Petty-cash floats: every executive carries one, which is where collected cash lands.
# The Indian chart installs a Bank journal and no Cash journal; the lab's counter and its
# executives' floats both settle in cash, so make one.
Journal = env['account.journal']
cash_journal = Journal.search([('type', '=', 'cash'), ('company_id', '=', company.id)], limit=1) or Journal.create({
    'name': 'Cash', 'type': 'cash', 'code': 'CSH1', 'company_id': company.id})
bank_journal = Journal.search([('type', '=', 'bank'), ('company_id', '=', company.id)], limit=1)
if bank_journal and bank_journal.name == 'Bank':
    # Name only: an account number would create a partner bank that invoices then
    # refuse to post against until somebody marks it trusted.
    bank_journal.write({'name': 'Federal Bank — Manjeri'})
assert cash_journal and bank_journal, 'cash and bank journals are needed'


def make_float(user):
    """A petty-cash float in the executive's name, allocated the way the module does it."""
    Alloc = env['petty.cash.allocation']
    existing = Alloc.sudo().search([('partner_id', '=', user.partner_id.id), ('state', '=', 'allocated')], limit=1)
    if existing:
        return existing
    alloc = Alloc.with_user(LATHA).sudo().create(vals_for(
        'petty.cash.allocation', partner_id=user.partner_id.id, journal_id=cash_journal.id, amount_limit=5000.0,
        float_amount=5000.0, allocated_date=TODAY - timedelta(days=95), note='Field float — %s' % user.name))
    for m in ('action_submit_request', 'action_allocate'):
        if hasattr(alloc, m):
            try:
                with env.cr.savepoint():
                    getattr(alloc.with_user(LATHA).sudo(), m)()
            except Exception as exc:  # noqa: BLE001
                print('seed: float %s %s: %s' % (user.login, m, str(exc)[:120]))
    if alloc.state != 'allocated':
        alloc.sudo().write({'state': 'allocated'})
    return alloc


try_block('petty-cash floats', lambda: [make_float(u) for u in EXECS])
Users.invalidate_model(['petty_cash_allocation_id', 'petty_cash_balance'])

Account = env['epg.whatsapp.account']
wa = Account.search([], limit=1) or Account.create({'name': 'ADL WhatsApp'})
wa.write(vals_for('epg.whatsapp.account', simulation_mode=True, whatsapp_number='+91 94478 49858',
                  link_base_url='http://localhost:8079', desk_user_id=PRIYA.id, upi_id='arabiandentallab@upi',
                  upi_payee_name='Arabian Dental Lab', send_from_hour=8, send_until_hour=21))
commit('beats, targets, floats, whatsapp (simulation)')

# ------------------------------------------------------------------ 5. the lab at work
PATIENT_FIRST = ['Aadhya', 'Aarav', 'Abhinav', 'Aiswarya', 'Akhil', 'Amal', 'Ameena', 'Anagha', 'Ananya', 'Arjun',
                 'Ashwin', 'Ayaan', 'Devika', 'Dhanya', 'Fathima', 'Gauri', 'Hafsa', 'Hari', 'Ishaan', 'Jishnu',
                 'Kavya', 'Keerthana', 'Krishna', 'Lekha', 'Malavika', 'Meenakshi', 'Midhun', 'Mohammed', 'Nandana',
                 'Neha', 'Niranjan', 'Nithya', 'Parvathy', 'Rahul', 'Riya', 'Rohan', 'Sanjana', 'Shreya', 'Sidharth',
                 'Sneha', 'Sreelakshmi', 'Suhail', 'Tanvi', 'Varun', 'Vishnu', 'Yadhu', 'Zara', 'Zayan']
PATIENT_LAST = ['K', 'S', 'P', 'M', 'Nair', 'Menon', 'Krishnan', 'Rahman', 'Thomas', 'Pillai', 'Varma', 'Ali', 'Das', 'Raj']
FAMILY_WEIGHTS = [('removable', 38), ('fixed', 22), ('functional', 14), ('aligner', 14), ('retainer', 12)]


def random_product():
    fam = random.choices([f for f, w in FAMILY_WEIGHTS], weights=[w for f, w in FAMILY_WEIGHTS])[0]
    return pick(products_by_family[fam])


def patient_name():
    return '%s %s' % (pick(PATIENT_FIRST), pick(PATIENT_LAST))


Visit = env['lab.visit']
Case = env['lab.case']
Trip = env['lab.trip']
Collection = env['lab.cash.collection']
Delivery = env['lab.delivery']
Cheque = env['lab.cheque']
Order = env['sale.order']
Workorder = env['mrp.workorder']

VISIT_DAYS = working_days(int(45 * SCALE) + 3)
ORDER_DAYS = working_days(int(78 * SCALE) + 6)
stats = {'visits': 0, 'cases': 0, 'orders': 0, 'mos': 0, 'delivered': 0, 'invoices': 0, 'paid': 0,
         'cheques': 0, 'reworks': 0, 'held': 0}
orders_made = []       # (order, executive, clinic, day)
open_invoices = []     # for cheques later


def bench_tech(wc):
    heads = wc.head_user_ids
    return pick(list(heads)) if heads else pick(TECHS)


def progress_production(order, day, fraction):
    """Walk the MOs through the benches as the board would, back-dated to `day`."""
    mos = order.sudo().mrp_production_ids.filtered(lambda m: m.state not in ('cancel', 'done'))
    for mo in mos:
        if mo.state == 'draft':
            mo.action_confirm()
        wos = mo.workorder_ids.sorted('sequence')
        if not wos:
            continue
        n_done = int(round(len(wos) * fraction))
        t = ist(day, 10, random.randint(0, 50))
        for i, wo in enumerate(wos):
            if i >= n_done:
                break
            tech = bench_tech(wo.workcenter_id)
            wo.write({'bench_user_id': tech.id})
            if wo.workcenter_id.is_finisher:
                wo.write({'finisher_user_id': tech.id})
            wo.with_user(PROD).action_station_accept()
            t_acc = t + timedelta(minutes=random.randint(5, 40))
            t_hand = t_acc + timedelta(minutes=max(10, int((wo.duration_expected or 30) * random.uniform(.7, 1.4))))
            wo.with_user(PROD).action_station_handover()
            wo.sudo().write({'accepted_at': t_acc, 'handed_over_at': t_hand,
                             'date_start': t_acc, 'date_finished': t_hand})
            t = t_hand
            if t > ist(day, 18):     # next bench picks it up next morning
                day = day + timedelta(days=1 if day.weekday() != 5 else 2)
                t = ist(day, 9, 30)
        if n_done >= len(wos):
            mo.sudo().write({'qty_producing': mo.product_qty} if 'qty_producing' in mo._fields else {})
            try:
                with env.cr.savepoint():
                    mo.sudo().button_mark_done()
            except Exception:  # noqa: BLE001
                mo.sudo().with_context(skip_backorder=True).button_mark_done()
            # MRP refuses to move a done order; the back-date goes straight to the row.
            env.cr.execute("UPDATE mrp_production SET date_finished = %s, date_start = %s WHERE id = %s",
                           (t, ist(day, 9), mo.id))
            mo.invalidate_recordset(['date_finished', 'date_start'])
            stats['mos'] += 1
    return day


def deliver(order, executive, day, by_courier=False):
    delivery = Delivery.sudo().search([('sale_order_id', '=', order.id), ('direction', '=', 'out'),
                                       ('state', 'not in', ('cancel', 'failed'))], limit=1)
    if not delivery:
        return None
    delivery.write({'executive_id': executive.id, 'scheduled_date': ist(day, 9)})
    if by_courier:
        courier = pick(COURIERS)
        delivery.write(vals_for('lab.delivery', delivery_mode='courier', courier_id=courier.id,
                                courier_awb='%s%09d' % (courier.code[:2], random.randint(1, 999999999)),
                                courier_dispatched_at=ist(day, 15), state='out', out_datetime=ist(day, 15)))
        delivery.with_context(lab_delivered_lat=order.partner_id.partner_latitude,
                              lab_delivered_lon=order.partner_id.partner_longitude, lab_delivered_accuracy=25.0
                              ).sudo().write({'state': 'delivered', 'delivery_outcome': 'clinic',
                                              'received_by': 'Reception', 'delivered_datetime': ist(day + timedelta(days=2), 12),
                                              'delivered_by_id': executive.id})
    else:
        delivery.write({'state': 'out', 'out_datetime': ist(day, 9, 15), 'delivery_mode': 'executive'})
        hour = random.randint(10, 17)
        delivery.with_user(executive).sudo().with_context(
            lab_delivered_lat=order.partner_id.partner_latitude + random.uniform(-0.0008, 0.0008),
            lab_delivered_lon=order.partner_id.partner_longitude + random.uniform(-0.0008, 0.0008),
            lab_delivered_accuracy=random.uniform(8, 40)).write({
                'state': 'delivered', 'delivery_outcome': 'clinic', 'received_by': pick(['Reception', 'Doctor', 'Assistant']),
                'delivered_datetime': ist(day, hour, random.randint(0, 59))})
    stats['delivered'] += 1
    return delivery


def invoice(order, day):
    order = order.sudo()
    if order.invoice_status != 'to invoice' and not order.order_line.filtered(lambda l: l.qty_to_invoice):
        return None
    inv = order._create_invoices()
    inv.write({'invoice_date': day, 'date': day})
    inv.action_post()
    stats['invoices'] += 1
    return inv


def pay(inv, day, amount=None, journal=None):
    reg = env['account.payment.register'].with_context(active_model='account.move', active_ids=inv.ids).create({
        'payment_date': day, 'journal_id': (journal or cash_journal).id,
        'amount': amount or inv.amount_residual})
    reg.action_create_payments()
    stats['paid'] += 1


def make_case(visit, executive, clinic, day, n_lines):
    lines = []
    for _ in range(n_lines):
        product = random_product()
        lines.append((0, 0, vals_for('lab.case.line', product_id=product.id, quantity=1.0,
                                     ul=random.choices(['upper', 'lower', 'ul'], weights=[5, 3, 2])[0],
                                     colour_id=pick(COLOURS).id if chance(.6) else False, is_urgent=False)))
    prio = random.choices(['normal', 'urgent', 'emergency'], weights=[91, 7, 2])[0]
    case = Case.with_user(executive).create(vals_for(
        'lab.case', visit_id=visit.id, partner_id=clinic.id, date=day, patient=patient_name(),
        age=random.randint(8, 34), gender=pick(['male', 'female']), priority=prio, line_ids=lines,
        impression_type=pick(['Alginate', 'Rubber Base']), impression_tray=pick(['Upper', 'Lower', 'Both']),
        scanned_impression='Yes' if chance(.2) else 'No', wax_bite='Yes' if chance(.5) else 'No',
        send_through_id=pick(SENDS).id, is_wires=chance(.3), is_screw=chance(.15), is_bite=chance(.4),
        needs_doctor_call=chance(.06), doctor_call_note='Confirm the expansion screw size' if chance(.5) else 'Clarify which arch',
        modification=pick(['', '', 'Add labial bow', 'Extra clasp on 16', 'Trim distal ends', 'Thicker acrylic']),
        instruction=pick(['', '', 'Urgent for camp', 'Patient travelling next week', 'Match previous colour']),
        duplicate_ack=True))
    case.with_user(executive).action_submit()
    stats['cases'] += 1
    return case


def selection_values(model, field):
    f = env[model]._fields.get(field)
    sel = f.selection if f is not None else None
    if callable(sel):
        sel = sel(env[model])
    return [v for v, _l in (sel or [])]


def log_call(order, day, answered=False):
    """One attempt to reach the doctor, with whatever this build's log records."""
    Log = env['lab.doctor.call.log']
    responses = selection_values('lab.doctor.call.log', 'response')
    outcomes = selection_values('lab.doctor.call.log', 'outcome')
    good = [v for v in responses if v in ('answered', 'resolved', 'reached', 'ok', 'done')]
    bad = [v for v in responses if v not in good] or responses
    vals = vals_for('lab.doctor.call.log', order_id=order.id, user_id=PRIYA.id, called_by_id=PRIYA.id,
                    call_date=ist(day + timedelta(days=1), 11), date=ist(day + timedelta(days=1), 11),
                    response=(pick(good) if (answered and good) else pick(bad)) if responses else None,
                    outcome=pick(outcomes) if outcomes else None,
                    remarks=pick(['No answer', 'Asked to call after 5 PM', 'Will send the cast tomorrow', 'Confirmed on the phone']),
                    note=pick(['No answer', 'Asked to call after 5 PM', 'Will send the cast tomorrow', 'Confirmed on the phone']),
                    try_again_at=ist(day + timedelta(days=2), 11), next_call_at=ist(day + timedelta(days=2), 11))
    return Log.sudo().create({k: v for k, v in vals.items() if v is not None})


def work_the_order(order, executive, clinic, day):
    """Everything after registration, according to how old the case is."""
    order = order.sudo()
    age = (TODAY - day).days
    # the checker, next morning
    if age <= 1 and chance(.5):
        return                                       # still awaiting verification
    if chance(.03):
        order.write({'verification_note': pick(['Arch not marked', 'Colour missing', 'Patient age missing'])})
        order.with_user(SURESH).action_reject_verification()
        return
    if chance(.05) and age <= 20:
        order.write({'verification_state': 'on_hold', 'hold_reason': pick(['awaiting_doctor_response', 'missing_info', 'pricing_approval']),
                     'hold_note': pick(['Doctor to confirm expansion screw', 'Waiting for opposing cast', 'Price to be approved by clinic']),
                     'hold_date': ist(day + timedelta(days=1), 10), 'next_followup_date': day + timedelta(days=3)})
        for i in range(random.randint(1, 3)):
            log_call(order, day + timedelta(days=i), answered=(i == 2))
        stats['held'] += 1
        return
    # A pending doctor query blocks verification, as it should. Old ones were answered;
    # the recent ones stay in the Doctor Calls queue for the desk to work.
    if 'call_doctor_required' in order._fields and order.call_doctor_required and not order.call_doctor_resolved:
        if age <= 3:
            return
        log_call(order, day, answered=True)
        if hasattr(order, 'action_resolve_doctor_call'):
            try:
                with env.cr.savepoint():
                    order.with_user(PRIYA).sudo().action_resolve_doctor_call()
            except Exception:  # noqa: BLE001
                order.write({'call_doctor_resolved': True})
        else:
            order.write({'call_doctor_resolved': True})
    order.with_user(SURESH).action_verify()
    # The counter confirms; sudo because the procurement chain touches stock and MRP
    # records an order-entry user cannot read.
    order.with_user(PRIYA).sudo().action_confirm()
    order.write({'date_order': ist(day, 11, random.randint(0, 59))})
    stats['orders'] += 1
    # how far along the bench it is
    if age >= 6:
        finish_day = progress_production(order, day + timedelta(days=1), 1.0)
        if age >= 8:
            delivered_day = max(finish_day + timedelta(days=1), day + timedelta(days=3))
            if delivered_day >= TODAY:
                return
            deliver(order, executive, delivered_day, by_courier=chance(.12))
            inv = invoice(order, delivered_day)
            if inv:
                r = random.random()
                if r < .50:
                    pay(inv, delivered_day + timedelta(days=random.randint(0, 6)))
                    pay_day = delivered_day
                    Collection.sudo().create(vals_for('lab.cash.collection', user_id=executive.id, partner_id=clinic.id,
                                                      date=pay_day + timedelta(days=random.randint(0, 6)), amount=inv.amount_total,
                                                      pay_mode='cash', note='Invoice %s' % inv.name))
                elif r < .68:
                    pay(inv, delivered_day + timedelta(days=random.randint(2, 9)), amount=round(inv.amount_total * random.choice([.3, .5, .6])))
                    open_invoices.append((inv, clinic, executive))
                else:
                    open_invoices.append((inv, clinic, executive))
            if chance(.05) and age >= 12:
                rework = Order.sudo().with_context(default_is_rework=True, default_rework_origin_id=order.id).create(vals_for(
                    'sale.order', partner_id=clinic.id, is_rework=True, rework_origin_id=order.id, patient=order.patient,
                    age=order.age, gender=order.gender, priority='urgent', rework_reason_id=pick(list(REWORK_REASONS)).id,
                    date_order=ist(delivered_day + timedelta(days=4), 12), team_id=order.team_id.id,
                    order_line=[(0, 0, {'product_id': l.product_id.id, 'product_uom_qty': l.product_uom_qty, 'price_unit': 0.0,
                                        'ul': l.ul}) for l in order.order_line]))
                if 'verification_state' in rework._fields:
                    rework.write({'verification_state': 'verified'})
                rework.action_confirm()
                progress_production(rework, delivered_day + timedelta(days=5), random.choice([0.4, 0.7, 1.0]))
                stats['reworks'] += 1
    elif age >= 2:
        progress_production(order, day + timedelta(days=1), random.choice([0.2, 0.35, 0.5, 0.7, 0.85]))


def day_road_km(visits):
    """Crow's flight between the day's doors, scaled to what roads actually cost."""
    fixes = [(v.gps_lat, v.gps_lon) for v in visits if v.gps_lat and v.gps_lon]
    metres = 0.0
    for (lat1, lon1), (lat2, lon2) in zip(fixes, fixes[1:]):
        step = env['lab.visit'].metres_between(lat1, lon1, lat2, lon2) \
            if hasattr(env['lab.visit'], 'metres_between') else None
        if step is None:
            from odoo.addons.lab_fieldwork.models.lab_visit import metres_between
            step = metres_between(lat1, lon1, lat2, lon2)
        metres += step or 0.0
    return metres / 1000.0 * 1.35          # roads are not straight


def clinic_visit(executive, clinic, doctor, day, hour, minute, beat):
    lat = clinic.partner_latitude + random.uniform(-0.0006, 0.0006)
    lon = clinic.partner_longitude + random.uniform(-0.0006, 0.0006)
    purpose = random.choices(['round', 'order', 'payment', 'delivery', 'new'], weights=[45, 25, 15, 10, 5])[0]
    visit = Visit.with_user(executive).sudo().create(vals_for(
        'lab.visit', partner_id=clinic.id, contact_id=doctor.id, user_id=executive.id, beat_id=beat.id if beat else False,
        date=day, purpose=purpose, state='open', check_in=ist(day, hour, minute), gps_lat=lat, gps_lon=lon,
        distance_m=random.uniform(5, 120), gps_state='ok'))
    stats['visits'] += 1
    n_cases = random.choices([0, 1, 2, 3], weights=[52, 34, 11, 3])[0]
    cases = [make_case(visit, executive, clinic, day, random.choices([1, 2], weights=[80, 20])[0]) for _ in range(n_cases)]
    collected = 0.0
    pay_mode = False
    if chance(.22):
        collected = float(random.choice([500, 1000, 1500, 2000, 2500, 3000, 4000, 5000]))
        pay_mode = random.choices(['cash', 'online', 'cheque'], weights=[70, 20, 10])[0]
    outcome = 'order' if cases else ('payment' if collected else random.choices(['followup', 'absent', 'none'], weights=[5, 2, 3])[0])
    minutes = random.randint(8, 45)
    out_t = ist(day, hour, minute) + timedelta(minutes=minutes)
    visit.sudo().write(vals_for('lab.visit', state='done', outcome=outcome, check_out=out_t, out_gps_lat=lat, out_gps_lon=lon,
                                out_distance_m=random.uniform(5, 120), out_gps_state='ok', collected=collected, pay_mode=pay_mode,
                                note=pick(['', '', 'Doctor asked about aligner pricing', 'Handed over the new price list',
                                           'Impression tray to be returned', 'Complaint about last case colour — noted'])))
    if collected:
        col = Collection.sudo().create(vals_for('lab.cash.collection', user_id=executive.id, partner_id=clinic.id, date=day,
                                                amount=collected, pay_mode=pay_mode, visit_id=visit.id, note='At the clinic'))
        if pay_mode == 'cash':
            try:
                with env.cr.savepoint():
                    col.action_cash_to_float()
            except Exception:  # noqa: BLE001
                pass
    for case in cases:
        orders = case.with_user(executive).action_create_order()
        for order in orders:
            orders_made.append((order, executive, clinic, day))
    return visit, out_t


for day in VISIT_DAYS:
    for team, executive, towns in routes:
        if chance(.06):
            continue                               # a day off
        beat = BEATS.get((executive.id, day.weekday()))
        mine = [c for c in clinics if c[2] == team]
        todays = [c for c in mine if beat and c[0].id in beat.partner_ids.ids] or mine
        n = min(len(todays), random.randint(4, 8))
        trip = Trip.sudo().create(vals_for('lab.trip', user_id=executive.id, date=day, vehicle='bike',
                                           odo_start=float(random.randint(20000, 60000)), state='open'))
        t = ist(day, 9, random.randint(15, 50))
        visits_today = []
        for clinic, doctor, _team, _exec in random.sample(todays, n):
            hour = (t + IST).hour
            minute = (t + IST).minute
            if hour >= 18:
                break
            _visit, out_t = clinic_visit(executive, clinic, doctor, day, hour, minute, beat)
            visits_today.append(_visit)
            t = out_t + timedelta(minutes=random.randint(12, 40))
        # The odometer must agree with the ground the round covers, or the day sheet's
        # map reads as a fraud signal ("109 km claimed, 5 km between the doors") on
        # every ordinary day. Road distance is worked out by the map; here the crow's
        # flight between the doors is scaled for roads, plus the run from the lab and
        # back. (client, 2026-09-23)
        km = round(day_road_km(visits_today) + random.uniform(12, 30))
        trip.sudo().write(vals_for('lab.trip', odo_end=trip.odo_start + km, state='closed'))
    env.cr.commit()
    print('seed: visits through', day, stats)

# Orders older than the visit window: registered straight at the counter, so the
# ledger and the benches have a longer history than the field diary.
early_days = [d for d in ORDER_DAYS if d < VISIT_DAYS[0]]
for day in early_days:
    for _ in range(random.randint(3, 6)):
        clinic, doctor, team, executive = pick(clinics)
        visit = Visit.sudo().create(vals_for('lab.visit', partner_id=clinic.id, contact_id=doctor.id, user_id=executive.id,
                                             date=day, purpose='order', state='done', outcome='order',
                                             check_in=ist(day, 10), check_out=ist(day, 10, 20), gps_state='ok',
                                             gps_lat=clinic.partner_latitude, gps_lon=clinic.partner_longitude))
        stats['visits'] += 1
        case = make_case(visit, executive, clinic, day, 1)
        for order in case.with_user(executive).action_create_order():
            orders_made.append((order, executive, clinic, day))
    env.cr.commit()
print('seed: %d orders registered' % len(orders_made))

for i, (order, executive, clinic, day) in enumerate(sorted(orders_made, key=lambda t: t[3])):
    try:
        with env.cr.savepoint():
            work_the_order(order, executive, clinic, day)
    except Exception as exc:  # noqa: BLE001
        _logger.exception('seed: order %s failed', order.name)
        print('seed: order %s skipped: %s' % (order.name, str(exc)[:140]))
    if i % 40 == 39:
        env.cr.commit()
        print('seed: worked %d orders' % (i + 1), stats)
commit('orders worked')

# ------------------------------------------------------------------ 6. cheques, handovers, day sheets, incentives
Bank = env['res.bank']
BANKS = [Bank.search([('name', '=', n)], limit=1) or Bank.create({'name': n, 'bic': b}) for n, b in
         [('State Bank of India', 'SBININBB'), ('Federal Bank', 'FDRLINBB'), ('Canara Bank', 'CNRBINBB'), ('South Indian Bank', 'SIBLINBB')]]


def cheques():
    random.shuffle(open_invoices)
    for inv, clinic, executive in open_invoices[:int(18 * SCALE) + 4]:
        if inv.payment_state == 'paid':
            continue
        rec_day = TODAY - timedelta(days=random.randint(1, 30))
        chq = Cheque.with_user(LATHA).create(vals_for(
            'lab.cheque', cheque_number='%06d' % random.randint(100000, 999999), partner_id=clinic.id, bank_id=pick(BANKS).id,
            branch=clinic.city, cheque_date=rec_day + timedelta(days=random.choice([0, 0, 7, 15, 30])), received_date=rec_day,
            amount=inv.amount_residual, journal_id=bank_journal.id, collected_by_id=executive.id))
        chq.with_user(LATHA).action_load_open_invoices()
        stats['cheques'] += 1
        r = random.random()
        if r < .55:
            chq.with_user(LATHA).action_deposit()
            if r < .40:
                chq.with_user(LATHA).action_clear()
        elif r < .62:
            chq.with_user(LATHA).action_deposit()
            chq.write({'bounce_reason': 'Insufficient funds'})
            chq.with_user(LATHA).action_bounce()


try_block('cheques', cheques)


def handovers():
    Handover = env['lab.cash.handover']
    for executive in EXECS:
        for week_end in [TODAY - timedelta(days=d) for d in (36, 29, 22, 15, 8)]:
            alloc = executive.sudo().petty_cash_allocation_id
            held = (alloc.amount_collected or 0.0) - (alloc.amount_handed_over or 0.0) if alloc else 0.0
            if held < 1000:
                break
            amount = round(min(held, random.randint(4, 14) * 1000), -2)
            if amount <= 0:
                break
            def one_handover(executive=executive, week_end=week_end, amount=amount):
                h = Handover.with_user(executive).sudo().create(vals_for('lab.cash.handover', user_id=executive.id, date=week_end, amount=amount,
                                                                         counted_amount=amount, note='Weekly handover'))
                h.with_user(executive).action_declare()
                h.with_user(OPS).action_confirm_receipt()
            safe('handover %s %s' % (executive.login, week_end), one_handover)


try_block('cash handovers', handovers)


def day_sheets():
    Daily = env['lab.daily.update']
    for day in VISIT_DAYS:
        for team, executive, towns in routes:
            if not Visit.search_count([('user_id', '=', executive.id), ('date', '=', day)]):
                continue
            def one_sheet(executive=executive, day=day):
                sheet = Daily.search([('user_id', '=', executive.id), ('date', '=', day)], limit=1) or \
                    Daily.create(vals_for('lab.daily.update', user_id=executive.id, date=day))
                if hasattr(sheet, 'action_refresh'):
                    sheet.action_refresh()
                age = (TODAY - day).days
                if age <= 1:
                    return
                sheet.with_user(executive).action_submit()
                if age <= 3 and chance(.5):
                    return
                if hasattr(sheet, 'action_ops_approve'):
                    sheet.with_user(OPS).action_ops_approve()
                if age > 4 and hasattr(sheet, 'action_marketing_approve'):
                    if 'marketing_score' in sheet._fields:
                        sheet.with_user(MKT).write({'marketing_score': str(random.randint(3, 5))})
                    sheet.with_user(MKT).action_marketing_approve()
            safe('day sheet %s %s' % (executive.login, day), one_sheet)


try_block('day sheets', day_sheets)


def incentives():
    Rule = env['lab.incentive.rule']
    rule = Rule.create(vals_for('lab.incentive.rule', name='Field incentive 2026', date_from=TODAY - timedelta(days=120),
                                slab_mode='telescopic', new_clinic_bonus=500.0,
                                slab_ids=[(0, 0, vals_for('lab.incentive.rule.slab', amount_from=0, amount_to=150000, incentive_type='percent', value=1.0)),
                                          (0, 0, vals_for('lab.incentive.rule.slab', amount_from=150000, amount_to=300000, incentive_type='percent', value=2.0)),
                                          (0, 0, vals_for('lab.incentive.rule.slab', amount_from=300000, amount_to=0, incentive_type='percent', value=3.0))]))
    last_month = (TODAY.replace(day=1) - timedelta(days=1)).replace(day=1)
    wiz = env['lab.incentive.generate.wizard'].with_user(CEO).create({'period': last_month})
    wiz.with_user(CEO).action_generate()
    for sheet in env['lab.incentive.sheet'].search([('period', '=', last_month)]):
        if sheet.state == 'computed' and chance(.6):
            sheet.with_user(CEO).action_approve()


try_block('incentive rule + sheets', incentives)


def work_targets():
    WT = env['lab.work.target']
    for day in working_days(int(12 * SCALE) + 2):
        for code, wc in WC.items():
            for tech in (wc.head_user_ids or TECHS[:1]):
                WT.create(vals_for('lab.work.target', date=day, user_id=tech.id, workcenter_id=wc.id,
                                   target=random.randint(6, 14), company_id=company.id))
    for m in ('action_refresh_all', 'action_refresh', '_cron_recount', 'cron_recount'):
        if hasattr(WT, m):
            try:
                with env.cr.savepoint():
                    getattr(WT.search([]), m)() if m.startswith('action') else getattr(WT, m)()
                break
            except Exception:  # noqa: BLE001
                continue


try_block('bench targets', work_targets)


def daily_sales():
    DS = env['daily.sales']
    for day in working_days(10)[-int(10 * SCALE) - 2:]:
        for team, executive, towns in routes:
            visits = Visit.search([('user_id', '=', executive.id), ('date', '=', day)])
            if not visits:
                continue
            rep = DS.with_user(executive).sudo().create(vals_for(
                'daily.sales', sales_person_id=executive.id, date=day, team_id=team.id,
                work_collected=sum(visits.mapped('case_count')), work_delivered=random.randint(0, 5),
                payment_amount=sum(visits.mapped('collected')),
                sales_line_ids=[(0, 0, vals_for('daily.sales.line', partner_id=v.partner_id.id, contact_id=v.contact_id.id,
                                                 work_collected=v.case_count, work_delivered=random.randint(0, 2),
                                                 payment_amount=v.collected, date=day, sales_person_id=executive.id))
                                for v in visits]))
            safe('daily sales submit', rep.with_user(executive).action_submit)
            if (TODAY - day).days > 2:
                safe('daily sales approve', rep.with_user(SURESH).action_approve)


try_block('daily sales reports', daily_sales)


def portal_requests():
    Req = env['lab.case.request']
    for clinic, doctor, team, executive in random.sample(clinics, 4):
        user = Users.with_context(no_reset_password=True).create({
            'login': doctor.email, 'name': doctor.name, 'partner_id': doctor.id,
            'group_ids': [(6, 0, [GROUPS['portal'].id])]})
        user.password = 'demo'
        for _ in range(random.randint(1, 2)):
            req = Req.with_user(user).sudo().create(vals_for(
                'lab.case.request', partner_id=doctor.id, patient=patient_name(), age=random.randint(9, 30),
                gender=pick(['male', 'female']), note='Sent from the doctor portal',
                line_ids=[(0, 0, vals_for('lab.case.request.line', product_id=random_product().id, ul=pick(['upper', 'lower', 'ul']), quantity=1.0))]))
            if chance(.4):
                req.with_user(SURESH).sudo().action_mark_reviewed()


try_block('doctor portal users + case requests', portal_requests)


def bank_reconciliation():
    Rec = env['bank.reconciliation']
    account = bank_journal.default_account_id
    rec = Rec.create(vals_for('bank.reconciliation', journal_id=bank_journal.id, account_id=account.id,
                              date=TODAY.replace(day=1) - timedelta(days=1), bank_balance=0.0, stale_days=90))
    if 'book_balance' in rec._fields:
        rec.write({'bank_balance': (rec.book_balance or 0.0) + 12500.0})


try_block('bank reconciliation (draft)', bank_reconciliation)


def petty_expenses():
    Exp = env['petty.cash.expense']
    for executive in EXECS:
        alloc = executive.sudo().petty_cash_allocation_id
        if not alloc:
            continue
        for d in working_days(8)[-3:]:
            Exp.with_user(executive).sudo().create(vals_for(
                'petty.cash.expense', allocation_id=alloc.id, user_id=executive.id, date=d,
                name=pick(['Fuel', 'Parking', 'Courier charge', 'Tea with doctor']), description=pick(['Fuel', 'Parking', 'Courier charge']),
                amount=random.choice([120, 200, 350, 500]), total_amount=random.choice([120, 200, 350, 500])))


try_block('petty-cash expenses', petty_expenses)

# ------------------------------------------------------------------ 6b. WhatsApp queue
def whatsapp_flush():
    """The confirmations, delivery notes and invoice reminders the workflows queued: send
    them through the account's simulation so the Desk and the chats show a working lab."""
    Message = env['epg.whatsapp.message']
    for _ in range(12):                      # the cron works within a time budget per run
        pending = Message.sudo().search_count([('state', 'in', ('draft', 'queued', 'ready'))])
        if not pending:
            break
        Message.sudo()._cron_send_queue()
        env.cr.commit()
    print('seed: whatsapp states', dict(Message.sudo()._read_group([], ['state'], ['__count'])))


try_block('whatsapp queue (simulated send)', whatsapp_flush)

# ------------------------------------------------------------------ 7. back-date creation stamps
env.cr.execute("UPDATE sale_order SET create_date = date_order WHERE date_order IS NOT NULL")
env.cr.execute("UPDATE lab_visit SET create_date = COALESCE(check_in, date::timestamp) WHERE date IS NOT NULL")
env.cr.execute("UPDATE lab_case c SET create_date = v.check_in FROM lab_visit v WHERE c.visit_id = v.id AND v.check_in IS NOT NULL")
env.cr.execute("UPDATE lab_delivery d SET create_date = COALESCE(d.out_datetime, d.scheduled_date, d.create_date)")
env.cr.execute("UPDATE account_move SET create_date = invoice_date::timestamp WHERE invoice_date IS NOT NULL")
env.cr.execute("UPDATE lab_cash_collection SET create_date = date::timestamp + interval '10 hours' WHERE date IS NOT NULL")
env.cr.execute("UPDATE mrp_production p SET create_date = o.date_order, date_start = COALESCE(p.date_start, o.date_order) FROM sale_order o WHERE p.sale_id = o.id")
env.cr.execute("UPDATE mrp_workorder w SET create_date = p.create_date FROM mrp_production p WHERE w.production_id = p.id")
env.cr.execute("UPDATE lab_trip SET create_date = date::timestamp + interval '3 hours' WHERE date IS NOT NULL")
env.cr.execute("UPDATE lab_cheque SET create_date = received_date::timestamp + interval '6 hours' WHERE received_date IS NOT NULL")
commit('back-dated')

print('SEED DONE', stats)
