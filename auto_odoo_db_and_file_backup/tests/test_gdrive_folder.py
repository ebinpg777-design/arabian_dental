# -*- coding: utf-8 -*-
"""Which Google Drive folder the backups go into.

Every backup landed in the root of the Drive because a rule had nowhere to name a
folder. The rule now takes a name, a path, an id or the folder's address, creates
it if it is not there, uploads into it and prunes inside it. No test reaches
Google: the drive is a double. (client, 2026-09-16)
"""
from odoo.tests import TransactionCase, tagged

FOLDER_MIME = 'application/vnd.google-apps.folder'


class FakeFile(dict):
    """What PyDrive hands back: a dict that can be uploaded and fetched."""

    def __init__(self, meta, drive):
        super().__init__(meta)
        self.drive = drive
        self.content = None

    def FetchMetadata(self, fields=None):
        stored = self.drive.by_id.get(self.get('id'))
        if stored is None:
            raise ValueError('no such file')
        self.update(stored)

    def SetContentFile(self, path):
        self.content = path

    def Upload(self):
        self.setdefault('id', 'id-%s' % (len(self.drive.by_id) + 1))
        self.drive.by_id[self['id']] = dict(self)
        self.drive.uploaded.append(dict(self))


class FakeList(list):
    def GetList(self):
        return list(self)


class FakeDrive:
    def __init__(self, existing=()):
        self.by_id = {f['id']: dict(f) for f in existing}
        self.uploaded = []
        self.queries = []

    def CreateFile(self, meta=None):
        return FakeFile(dict(meta or {}), self)

    def ListFile(self, params):
        # PyDrive answers with a list object you call GetList() on, not a list.
        query = params['q']
        self.queries.append(query)
        out = []
        for stored in self.by_id.values():
            parent = (stored.get('parents') or [{}])[0].get('id')
            if "'%s' in parents" % parent not in query:
                continue
            if "mimeType='%s'" % FOLDER_MIME in query \
                    and stored.get('mimeType') != FOLDER_MIME:
                continue
            if "title='" in query:
                wanted = query.split("title='", 1)[1].rsplit("'", 1)[0]
                if stored.get('title', '').replace("'", "\\'") != wanted:
                    continue
            out.append(dict(stored))
        return FakeList(out)


@tagged('post_install', '-at_install')
class TestGdriveFolder(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        config = cls.env['auto.database.backup'].create({
            'name': 'Drive folder backups', 'bkup_email': False, 'bkup_fail_email': False})
        cls.rule = cls.env['database.backup'].create({
            'backup_id': config.id, 'backup_destination': 'g_drive',
            'backup_type': 'dump', 'backup': 'db_only'})

    def test_no_folder_named_means_the_drive_root(self):
        drive = FakeDrive()
        self.assertFalse(self.rule._gdrive_folder_id(drive))
        self.assertFalse(drive.uploaded, "nothing is created for the root")

    def test_a_name_is_created_once_and_then_reused(self):
        drive = FakeDrive()
        self.rule.google_drive_folder = 'Lab Backups'
        first = self.rule._gdrive_folder_id(drive)
        self.assertTrue(first)
        created = [f for f in drive.uploaded if f.get('mimeType') == FOLDER_MIME]
        self.assertEqual([f['title'] for f in created], ['Lab Backups'])
        self.assertEqual(self.rule._gdrive_folder_id(drive), first,
                         "the second backup finds the folder it made")
        self.assertEqual(len([f for f in drive.uploaded
                              if f.get('mimeType') == FOLDER_MIME]), 1)

    def test_a_path_becomes_nested_folders(self):
        drive = FakeDrive()
        self.rule.google_drive_folder = 'Lab/Backups'
        leaf = self.rule._gdrive_folder_id(drive)
        made = [f for f in drive.uploaded if f.get('mimeType') == FOLDER_MIME]
        self.assertEqual([f['title'] for f in made], ['Lab', 'Backups'])
        self.assertEqual(made[1]['parents'][0]['id'], made[0]['id'],
                         "the second folder is inside the first")
        self.assertEqual(leaf, made[1]['id'])

    def test_an_id_is_taken_as_the_folder_itself(self):
        drive = FakeDrive([{'id': '1a2b3c4d5e6f7g8h9i0jKLMNOP', 'title': 'Given',
                            'mimeType': FOLDER_MIME}])
        self.rule.google_drive_folder = '1a2b3c4d5e6f7g8h9i0jKLMNOP'
        self.assertEqual(self.rule._gdrive_folder_id(drive), '1a2b3c4d5e6f7g8h9i0jKLMNOP')
        self.assertFalse([f for f in drive.uploaded if f.get('mimeType') == FOLDER_MIME],
                         "an existing folder is not created again")

    def test_the_address_of_a_folder_is_accepted(self):
        drive = FakeDrive([{'id': '1a2b3c4d5e6f7g8h9i0jKLMNOP', 'title': 'Given',
                            'mimeType': FOLDER_MIME}])
        self.rule.google_drive_folder = \
            'https://drive.google.com/drive/folders/1a2b3c4d5e6f7g8h9i0jKLMNOP?usp=sharing'
        self.assertEqual(self.rule._gdrive_folder_id(drive), '1a2b3c4d5e6f7g8h9i0jKLMNOP')

    def test_a_quote_in_the_name_cannot_rewrite_the_search(self):
        drive = FakeDrive()
        self.rule.google_drive_folder = "Lab's Backups"
        self.rule._gdrive_folder_id(drive)
        self.assertTrue(drive.queries)
        self.assertIn("title='Lab\\'s Backups'", drive.queries[0])

    # --------------------------------------- an error is not an answer about the id
    def test_an_id_that_cannot_be_read_stops_instead_of_making_a_folder(self):
        """Google refusing the lookup says nothing about whether the id is a folder.

        With the Drive API switched off in the project every call comes back 403. The
        fallback would then search for a folder TITLED with the id, find none and
        create one, so the backups would quietly land in a new folder called
        1WvhbK4A... instead of the folder that was asked for. (client, 2026-09-16)
        """
        class Refused(Exception):
            resp = type('Resp', (), {'status': 403})()

        class RefusingDrive(FakeDrive):
            def CreateFile(self, meta=None):
                handed = super().CreateFile(meta)
                if (meta or {}).get('id'):
                    handed.FetchMetadata = self._refuse
                return handed

            @staticmethod
            def _refuse(fields=None):
                raise Refused('Google Drive API has not been used in project 1033 '
                              'before or it is disabled')

        drive = RefusingDrive()
        self.rule.google_drive_folder = '1WvhbK4AsYOKOe04C8A3Y5yk592uyPwmr'
        with self.assertRaises(Refused):
            self.rule._gdrive_folder_id(drive)
        self.assertFalse(drive.uploaded, "nothing is created when Google refused to say")
        self.assertFalse(drive.queries, "and it does not go looking for that title")

    def test_an_id_that_is_really_a_long_name_still_falls_back(self):
        """A 'no such file' IS an answer: the value was a name, not an id."""
        drive = FakeDrive()
        self.rule.google_drive_folder = 'ArabianDentalNightlyBackups'
        made = self.rule._gdrive_folder_id(drive)
        self.assertTrue(made)
        self.assertEqual([f['title'] for f in drive.uploaded
                          if f.get('mimeType') == FOLDER_MIME],
                         ['ArabianDentalNightlyBackups'])
