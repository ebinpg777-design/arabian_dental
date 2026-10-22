## Module <ebshel_auto_database_backup>

Based on <auto_database_backup> by Cybrosys Techno Solutions (history below).

#### 07.05.2022
#### Version 15.0.1.0.0
#### ADD
- Initial commit for auto_database_backup

#### 14.06.2022
#### Version 15.0.2.0.1
#### ADD
- Dropbox integration added. Backup can be stored in to dropbox.

#### 20.08.2022
#### Version 15.0.3.0.1
#### ADD
- Onedrive integration added. Backup can be stored in to onedrive.

#### 15.11.2022
#### Version 15.0.3.1.1
#### ADD
- Google Drive authentication updated.

#### 16.10.2023
#### Version 15.0.4.1.2
#### ADD
- Nextcloud and Amazon S3 integration added. Backup can be stored into Nextcloud and Amazon S3.

#### 16.02.2024
#### Version 15.0.5.1.0
#### UPDT
- Fixed internal server error in Onedrive and Google Drive integration.

#### 24.04.2024
#### Version 15.0.6.1.0
#### UPDT

- Fixed the errors while inputting list_db = False in odoo conf file.

#### 30.09.2026
#### Version 20.0.1.0.0
#### UPDT
- Ported to Odoo 20 (ir.access, python view modifiers, odoo.modules.db, get_str/get_base_url).
- Access restricted to Settings (base.group_system); OAuth callbacks need a logged in
  administrator and a signed state.
- Destination libraries (paramiko, dropbox, boto3, pyncclient) are optional and imported
  only when used; nextcloud-api-wrapper is no longer needed.
- One failing configuration no longer stops the others; failed uploads are reported as failures.
- Old-backup removal only deletes this configuration's backups, on every destination, with paging.
- Large backups: resumable Google Drive upload, chunked OneDrive/Dropbox uploads, streamed pg_dump.

#### 06.10.2026
#### Version 19.0.1.0.0
#### UPDT
- Ported to Odoo 19 by Ebshel Technologies and renamed ebshel_auto_database_backup.
- Odoo 19 APIs: ir.model.access, odoo.service.db, Font Awesome icons. The zip
  manifest is written by the module, so zip backups work with list_db = False.
- Tests for zip and dump backups, old-backup removal, failure e-mails,
  scheduler-only dumps, credential checks and the signed OAuth state.

#### 06.10.2026
#### Version 20.0.1.0.0
#### UPDT
- Ported ebshel_auto_database_backup to Odoo 20: ir.access, odoo.modules.db
  (verify_admin_password, list_dbs), Odoo UI icons. The module keeps writing
  its own zip manifest.
