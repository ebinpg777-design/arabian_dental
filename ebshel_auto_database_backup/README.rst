.. image:: https://img.shields.io/badge/licence-LGPL--3-green.svg
    :target: https://www.gnu.org/licenses/lgpl-3.0-standalone.html
    :alt: License: LGPL-3

=========================
Automatic Database Backup
=========================

Scheduled backups of your Odoo 20 databases, stored in local storage, on an
FTP or SFTP server, in Google Drive, Dropbox, OneDrive, Nextcloud or Amazon S3.

* Zip (SQL dump + filestore + manifest, restorable from the database manager)
  or PostgreSQL custom-format dump.
* Backups are taken with ``pg_dump`` directly, so they also work on servers
  where the database manager is disabled (``list_db = False``).
* Old backups are removed after a number of days, on every destination, and
  only this configuration's own backup files are ever deleted.
* An e-mail tells a user when a backup succeeded or failed; administrators
  also see failed backups and a stopped backup job on the home menu, when a
  theme that shows home alerts is installed.
* Large databases: streamed ``pg_dump``, resumable Google Drive uploads,
  chunked OneDrive and Dropbox uploads, multipart Amazon S3 uploads.

Configuration
=============

Database Backup > Backup Configuration, an app on the home screen for
administrators. The scheduled action *Backup : Automatic Database Backup*
runs once a day; change its interval there.

The PostgreSQL client tools (``pg_dump``) must be installed on the Odoo
server, or their directory set with the ``pg_path`` option.

Remote destinations need one Python package each, installed on the Odoo
server only when that destination is used:

==============  ======================
Destination     Package
==============  ======================
SFTP            ``pip install paramiko``
Dropbox         ``pip install dropbox``
Amazon S3       ``pip install boto3``
Nextcloud       ``pip install pyncclient``
==============  ======================

Local storage, FTP, Google Drive and OneDrive need nothing extra.

Company
=======

* `Ebshel Technologies <https://ebshel.com>`__

Support: ebinpg777@gmail.com

Credits
=======

Ported to Odoo 20 and modified by Ebshel Technologies.

Based on ``auto_database_backup`` by Cybrosys Techno Solutions
(https://www.cybrosys.com), licensed under the LGPL-3:

* Midilaj (v15)
* Anfas Faisal K (v15 Amazon S3 and Nextcloud features)

License
=======

GNU Lesser General Public License, Version 3 (LGPL v3).
(https://www.gnu.org/licenses/lgpl-3.0-standalone.html)
