# -*- coding: utf-8 -*-
# odoo19
{
    'name': "Automatic Backup (Google Drive, Dropbox, Amazon S3, FTP, SFTP, Local)",
    'category': 'Extra Tools',
    'version': '19.0.1.6.0',

    'summary': 'Automatic Backup -(Google Drive, Dropbox, Amazon S3, FTP, SFTP, Local)',
    'description': "Automatic Backup -(Google Drive, Dropbox, Amazon S3, FTP, SFTP, Local)",
    'license': 'OPL-1',
    'price': 31.46,
	'currency': 'USD',

    'author': "Icon TechSoft Pvt. Ltd.",
    'website': "https://icontechnology.co.in",
    'support': 'team@icontechnology.in',
    'maintainer': "Icon TechSoft Pvt. Ltd.",
    'images': ['static/description/auto-backup-odoo-v19.png'],
    # any module necessary for this one to work correctly
    'depends': ['base', 'google_account', 'mail'],
    'external_dependencies': {
        'python': ['dropbox', 'ftplib', 'boto3', 'paramiko', 'pydrive']
    },
    # always loaded
    'data': [
        'security/ir.model.access.csv',
        'views/views.xml',
        'views/auto_backup_mail_templates.xml',
        'data/data.xml',
        'wizard/wiz.xml',
    ],
    'installable': True,
    'application': True,
}
