import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import test_server
from backup import BackupError, BOOKMARKS, MANIFEST, PREVIOUS, TRANSACTION

app = test_server.app


class BackupTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = app.Store(directory.name)

    def snapshot(self):
        output = io.BytesIO()
        self.store.backups.write_zip(output)
        return output.getvalue()

    def restore(self, data):
        self.store.backups.restore(io.BytesIO(data), len(data))

    def test_round_trip_all_saved_data_and_previous_backup(self):
        first = self.store.save('Notes/Nested', '# Original')
        saved = self.store.save('Notes/Nested', '# Saved', first['revision'])
        trash = self.store.delete('Old', self.store.save('Old', '# Old')['revision'])
        order = self.store.reorder_pages('', ['Notes'], None)
        self.store.set_start_page('Notes/Nested')
        image = self.store.upload_image(test_server.PNG)
        with patch('bookmarks.fetch_page_text', return_value={'title': 'Article', 'text': 'A searchable orchid'}):
            bookmark = self.store.bookmarks.save({
                'url': 'https://example.com/orchid', 'title': 'Orchids', 'tags': ['gardening'], 'extract': True,
            })
        snapshot = self.snapshot()
        self.store.save('Notes/Nested', '# Later', saved['revision'])
        self.store.save('Only later', '# Later')
        self.store.set_start_page('Only later')
        self.store.bookmarks.remove(bookmark['id'], bookmark['revision'])
        self.restore(snapshot)
        self.assertEqual(self.store.read('Notes/Nested'), saved)
        self.assertEqual(self.store.history('Notes/Nested')['versions'][0]['revision'], first['revision'])
        self.assertEqual(self.store.deleted()[0]['id'], trash['id'])
        self.assertEqual(self.store.page_order(), order)
        self.assertEqual(self.store.start_page(), {'path': 'Notes/Nested'})
        self.assertEqual(self.store.read_image(image['url'].split('/')[-1])[0], test_server.PNG)
        self.assertEqual(self.store.bookmarks.get(bookmark['id']), bookmark)
        self.assertEqual(self.store.bookmarks.listing(query='orchid', tags=['gardening'])['total'], 1)
        self.assertFalse(self.store.path('Only later').exists())
        with zipfile.ZipFile(self.store.root / PREVIOUS) as previous:
            self.assertEqual(previous.read('pages/Only later.md'), b'# Later')
        self.assertFalse((self.store.root / TRANSACTION).exists())

    def test_rejects_traversal_and_bad_records_before_replacing_data(self):
        self.store.save('Keep', '# Keep')
        for name, record in [('pages/../../elsewhere.md', b''), ('pages/New.md', b'{"id":"invalid"}\n')]:
            output = io.BytesIO()
            with zipfile.ZipFile(output, 'w') as archive:
                archive.writestr(MANIFEST, json.dumps({
                    'format': 'homewiki', 'version': 1, 'modified_ns': {name: 0},
                }))
                archive.writestr(BOOKMARKS, record)
                archive.writestr(name, 'text')
            with self.assertRaises(BackupError):
                self.restore(output.getvalue())
            self.assertEqual(self.store.read('Keep')['content'], '# Keep')
            self.assertFalse((self.store.root / PREVIOUS).exists())

    def test_rolls_back_if_install_fails_midway(self):
        self.store.save('Restored', '# Restored')
        snapshot = self.snapshot()
        self.store.save('Newer', '# Newer')
        rename = Path.rename

        def fail_on_uploads(path, target):
            if path.name == 'uploads' and path.parent.name.startswith('.backup-stage-'):
                raise OSError('simulated staged rename failure')
            return rename(path, target)

        with patch.object(Path, 'rename', fail_on_uploads), self.assertRaises(OSError):
            self.restore(snapshot)
        self.assertEqual(self.store.read('Newer')['content'], '# Newer')
        self.assertTrue(self.store.uploads.is_dir())
        self.assertFalse((self.store.root / TRANSACTION).exists())

    def test_startup_rolls_back_interrupted_restore(self):
        saved = self.store.save('Keep', '# Keep')
        transaction = self.store.root / TRANSACTION
        old = transaction / 'old'
        old.mkdir(parents=True)
        (transaction / 'original.json').write_text(json.dumps(['pages', 'uploads', '.trash', '.history']))
        self.store.pages.rename(old / 'pages')
        self.store.pages.mkdir()
        (self.store.pages / 'Interrupted.md').write_text('Partial restore')
        reopened = app.Store(self.store.root)
        self.assertEqual(reopened.read('Keep'), saved)
        self.assertFalse(reopened.path('Interrupted').exists())
        self.assertFalse(transaction.exists())
