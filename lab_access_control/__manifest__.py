# -*- coding: utf-8 -*-
{
    'name': 'Lab Access Control',
    'version': '19.0.1.0.0',
    'summary': 'State-based write locks, role hierarchy and configuration security '
               'shared by the Lab field-force, order-control and finance modules.',
    'description': """
Lab Access Control
====================
The shared access layer the three Lab gap-closure modules build on.

* **State-based write lock** (`lab.lock.mixin`) — a record in a closed state is
  read-only in the *server*, not merely in the form view. A junior user cannot edit a
  completed visit or a cleared cheque through RPC, an imported file or a list view.
* **Role hierarchy** — Field Manager implies Field Executive, Order Checker implies
  Order Entry, and a single *Lab Configuration Manager* group owns every settings block.
* **Configuration security** — the Lab settings blocks are restricted to the
  configuration manager instead of every Settings user.
""",
    'author': 'Ebin P G',
    'category': 'Services',
    'license': 'LGPL-3',
    # hr: the timezone normaliser has to load AFTER hr, not before. hr syncs a user's
    # timezone onto their employee record and back again, and only stops when the two
    # agree — so if hr wraps the normaliser it keeps propagating the name the user
    # asked for while the normaliser keeps storing the name PostgreSQL accepts, the
    # two never agree, and logging in recurses until the stack gives out.
    'depends': ['base_setup', 'mail', 'hr'],
    'data': [
        'security/lab_access_control_security.xml',
        'data/menu_repair.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
