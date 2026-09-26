# -*- coding: utf-8 -*-
{
    'name': "Lab Master Data",
    'version': '19.0.1.0.1',
    'summary': "The lab's departments, cost centres, benches, raw materials and "
               "bills of materials, as the SOPs describe them",
    'description': """
Lab Master Data
===============

Everything the lab's own procedure documents describe, written into the database
once: the departments a case passes through, the cost centre each of them posts
to, the bench each stage happens at, the material each stage consumes, and the
bill of materials that ties the three together.

Where it comes from
-------------------
* **Departments and cost centres** - the production departments named in the
  Production SOP (Wax Up, CAD/CAM, Metal, Ceramic, Acrylic, Orthodontic) and the
  support functions named in the ERP proposal, arranged as a tree of twenty-three
  with one cost centre each, in a plan of their own.
* **Work centres** - fifty-seven benches, one per stage of the Ceramic, Zirconia,
  Veneer, Acrylic and Orthodontic SOPs.
* **Consumption materials** - forty-four, with the rate each one costs, from
  *PRODUCTION DETAILS.xlsx*.
* **Bills of materials** - fifteen: the six zirconia grades, metal ceramic, full
  metal, the pressed veneer, four orthodontic appliances and two dentures.

Everything is `noupdate="1"`: once the lab renames a bench or corrects a rate, a
module upgrade must not put its own version back.

What it changes about the lab's data
------------------------------------
**The fifteen costed products stop being services.** Everything the lab sells is
a service today, which is why a database with twenty-six thousand orders holds
not one manufacturing order: Odoo will not raise a job for a service and a bill
of materials cannot be written against one. The suite was built expecting
otherwise - `sale_custom.action_confirm` has planned "the generated Manufacturing
Orders (dental lab workflow)" since before this module existed, and
`lab_workcenter_scan` is a station board for their work orders. These fifteen
become goods with Manufacture and Replenish on Order, so confirming a sale raises
the job for that patient's case. They are not storable: a crown gets a job and a
route but no quants and no valuation layer of its own.

**Three existing departments are adopted, not duplicated.** Administration,
Accounts and `sales` carry nine employees between them. They are folded into the
tree with codes, kinds and parents rather than left beside a second set with the
same names.

**Nothing of the lab's is created twice.** Sixteen hundred products, twenty
locations and two dozen categories came across from Odoo 17 without external
identifiers, so a data file cannot point at them. `models/masters_loader.py`
finds them by name and writes the identifier; `uninstall_hook` releases them
again, so uninstalling this module cannot take the lab's own records with it.

What the costing sheet gets wrong
---------------------------------
The rates here are derived from the lab's own per-unit figures, because those are
what their totals are built from - the Ceramic block adds to 416.821 exactly,
to the third decimal, using them. Two of their stated rates do not agree with
their own arithmetic and the lab should settle them:

* **Base dentine** is stated as a 25 g bottle. At 2,800 that would make the
  0.183 g a crown cost 20.50, and they total 10.33. The bottle is 50 g - which is
  what their own purchase items say - and the note is the error.
* **Investment** is stated at 6,700 a kilogram. At the 400 g per 30 units the
  same line gives, that is 89.33 a unit, and they total 0.60. The rate is wrong
  by a factor of about 150.

Why a bill of materials costs less than their sheet
---------------------------------------------------
A metal ceramic crown is 271.32 of material and labour here against the 416.82
their sheet prints. The difference is overheads - rent, power, logistics,
packing, the fuel of the routes, and the salaries nobody bills by the hour -
which are a period cost belonging to a cost centre, not a component of a crown.
The Case Cost screen shows both, and takes the margin against the fuller figure
wherever the lab has one, because a margin against works cost alone reads 64%
where their own sheet says 45%.

Labour is touch time from *Process Time Cycle*, not the capacity figures in the
costing sheet. A model preparation bench takes one minute; sixty models a day is
eight. The difference is the bench standing idle, and seeing it is the point.
Benches where nobody stands - furnaces, printers, curing units - carry no hourly
rate at all, because the lab books machines to overheads and inventing a rate
would double-count them.

The screen
----------
**Case Cost** (Cost Centres, and again under Manufacturing / Reporting): every
route with what it consumes, where it is worked, and what is left of the price.
""",
    'author': 'Arabian Dental Lab',
    'category': 'Manufacturing',
    'license': 'LGPL-3',
    'depends': ['lab_cost_centre', 'mrp', 'stock', 'purchase'],
    'data': [
        # First, because everything after it refers to records this names.
        'data/link_existing.xml',
        'data/hr_department_data.xml',
        'data/analytic_data.xml',
        'data/mrp_workcenter_data.xml',
        'data/product_raw_material_data.xml',
        # Before the bills of materials: those point at products that are
        # services until this runs.
        'data/product_anchor_goods.xml',
        'data/mrp_bom_data.xml',
        # Last, because it needs the departments, the locations and the products.
        'data/masters_link.xml',
        'views/case_cost_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'lab_masters/static/src/case_cost/case_cost.js',
            'lab_masters/static/src/case_cost/case_cost.xml',
            'lab_masters/static/src/case_cost/case_cost.scss',
        ],
    },
    'uninstall_hook': 'uninstall_hook',
    'installable': True,
    'application': False,
}
