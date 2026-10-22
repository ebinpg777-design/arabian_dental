# -*- coding: utf-8 -*-
###############################################################################
#
#    Ebshel Technologies
#
#    Copyright (C) 2026 Ebshel Technologies (https://ebshel.com)
#    Ported to Odoo 20 and modified by Ebshel Technologies.
#
#    Based on "auto_database_backup" by Cybrosys Technologies:
#    Copyright (C) 2023-TODAY Cybrosys Technologies(<https://www.cybrosys.com>)
#    Author: Cybrosys Techno Solutions(<https://www.cybrosys.com>)
#
#    You can modify it under the terms of the GNU LESSER
#    GENERAL PUBLIC LICENSE (LGPL v3), Version 3.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU LESSER GENERAL PUBLIC LICENSE (LGPL v3) for more details.
#
#    You should have received a copy of the GNU LESSER GENERAL PUBLIC LICENSE
#    (LGPL v3) along with this program.
#    If not, see <http://www.gnu.org/licenses/>.
#
###############################################################################
{
    'name': 'Automatic Database Backup',
    'version': '20.0.1.0.0',
    'category': 'Extra Tools',
    'summary': 'Scheduled database backups to local storage, FTP, SFTP, '
               'Google Drive, Dropbox, OneDrive, Nextcloud or Amazon S3, '
               'with old-backup cleanup and e-mail alerts',
    'description': """
Generates backups of your databases automatically at regular intervals and
stores them in local storage, on an FTP or SFTP server, in Google Drive,
Dropbox, OneDrive, Nextcloud or Amazon S3. Old backups can be removed
automatically, and a user can be e-mailed when a backup succeeds or fails.
Backups are taken with pg_dump directly, so they also work on servers where
the database manager is disabled (list_db = False).
""",
    'author': 'Ebshel Technologies',
    'maintainer': 'Ebshel Technologies',
    'website': 'https://ebshel.com',
    'support': 'ebinpg777@gmail.com',
    'depends': ['base', 'mail'],
    'data': [
        'security/ir.access.csv',
        'data/ir_cron_data.xml',
        'data/mail_data.xml',
        'views/db_backup_configure_views.xml',
        'wizard/dropbox_auth_code_views.xml',
    ],
    # The libraries of the remote destinations are imported only when that
    # destination is used (a missing one is reported to the admin): paramiko
    # (SFTP), dropbox (Dropbox), boto3 (Amazon S3), pyncclient (Nextcloud).
    'images': ['static/description/banner.png'],
    # A derivative of an LGPL-3 module: it stays LGPL-3.
    'license': 'LGPL-3',
    'price': 49.00,
    'currency': 'USD',
    'installable': True,
    'auto_install': False,
    'application': False,
}
