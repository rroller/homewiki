"""Stream Homewiki ZIPs and validate restores before replacing saved data."""
import contextlib
import hashlib
from itertools import chain
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import tempfile
import threading
import zipfile
import zlib

from bookmarks import BookmarkError, BookmarkStore, normalize_tags, normalize_url

MAX_ARCHIVE = 2 * 1024**3
MAX_EXPANDED = 8 * 1024**3
MAX_FILES = 50_000
COPY_BLOCK = 1024 * 1024
BOOKMARK_DB = 'bookmarks.sqlite3'
PREVIOUS = '.before-restore.zip'
TRANSACTION = '.restore-transaction'
DIRECTORIES = ('pages', 'uploads', '.trash', '.history')
START_PAGE_FILE = '.start-page'
LIVE_NAMES = (*DIRECTORIES, '.sidebar-order.json', START_PAGE_FILE, '.initialized', BOOKMARK_DB,
              BOOKMARK_DB + '-journal', BOOKMARK_DB + '-wal', BOOKMARK_DB + '-shm')
BOOKMARK_FIELDS = ('id', 'url', 'title', 'domain', 'tags', 'page_text', 'extraction_status',
                   'extraction_error', 'extracted_at', 'created_at', 'updated_at', 'revision')
MANIFEST = 'homewiki-backup.json'
BOOKMARKS = 'bookmarks.jsonl'


class BackupError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


def invalid():
    return BackupError(400, 'Invalid or unsupported Homewiki backup. Your current data is intact.')


def remove_entry(path):
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def managed_member(name):
    parts = name.split('/')
    if (len(name) > 1024 or any(not p or p in ('.', '..') or
                               any(c in '\\:' or ord(c) < 32 for c in p) for p in parts)):
        return False
    if name in ('.sidebar-order.json', START_PAGE_FILE, BOOKMARKS):
        return True
    if parts[0] == 'pages':
        page = parts[1:]
        if not page or not page[-1].endswith('.md'):
            return False
        page[-1] = page[-1][:-3]
        return len('/'.join(page)) <= 400 and all(p and p == p.strip() and not p.startswith('.') for p in page)
    if parts[0] == 'uploads':
        return len(parts) == 2 and bool(re.fullmatch(r'[0-9a-f]{32}\.(png|jpg|gif|webp)', parts[1]))
    if parts[0] in ('.trash', '.history'):
        ids = ('[0-9a-f]{32}',) if parts[0] == '.trash' else ('[0-9a-f]{64}', '[0-9a-f]{32}')
        return (len(parts) == len(ids) + 2 and parts[-1] in ('metadata.json', 'page.md')
                and all(re.fullmatch(pattern, value) for pattern, value in zip(ids, parts[1:-1])))
    return False


class BackupManager:
    def __init__(self, store):
        self.store = store
        self.root = store.root
        self.condition = threading.Condition()
        self.requests = 0
        self.maintenance = False
        self.transfer = threading.Lock()

    @contextlib.contextmanager
    def regular(self):
        with self.condition:
            if self.maintenance:
                raise BackupError(503, 'Homewiki is finishing a backup or restore. Try again shortly.')
            self.requests += 1
        try:
            yield
        finally:
            with self.condition:
                self.requests -= 1
                self.condition.notify_all()

    @contextlib.contextmanager
    def exclusive(self):
        with self.condition:
            self.maintenance = True
            self.condition.wait_for(lambda: self.requests == 0)
        try:
            with self.store.locked():
                yield
        finally:
            with self.condition:
                self.maintenance = False
                self.condition.notify_all()

    @contextlib.contextmanager
    def one_transfer(self):
        if not self.transfer.acquire(blocking=False):
            raise BackupError(409, 'Another backup or restore is in progress.')
        try:
            yield
        finally:
            self.transfer.release()

    def recover(self):
        """Undo an interrupted restore before serving the wiki."""
        transaction = self.root / TRANSACTION
        if not transaction.exists():
            return
        journal = transaction / 'original.json'
        if journal.exists():
            original = set(json.loads(journal.read_text()))
            for name in LIVE_NAMES:
                old = transaction / 'old' / name
                live = self.root / name
                if old.exists() or old.is_symlink():
                    remove_entry(live)
                    old.rename(live)
                elif name not in original:
                    remove_entry(live)
        shutil.rmtree(transaction)

    def write_zip(self, output):
        manifest = {'format': 'homewiki', 'version': 1,
                    'created_at': datetime.now(timezone.utc).isoformat(), 'modified_ns': {}}
        files = manifest['modified_ns']
        total = 0
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, strict_timestamps=False) as archive:
            candidates = (path for directory in DIRECTORIES for path in (self.root / directory).rglob('*'))
            for path in chain(candidates, (self.root / '.sidebar-order.json', self.root / START_PAGE_FILE)):
                name = path.relative_to(self.root).as_posix()
                if not managed_member(name) or not path.is_file():
                    continue
                if any(parent.is_symlink() for parent in (path, *path.parents) if parent != self.root):
                    raise BackupError(400, 'Homewiki cannot back up symlinked data files.')
                info = path.stat()
                total += info.st_size
                if len(files) >= MAX_FILES - 2 or total > MAX_EXPANDED:
                    raise BackupError(413, 'The wiki exceeds the ZIP backup size limit.')
                archive.write(path, name)
                files[name] = info.st_mtime_ns
            with archive.open(BOOKMARKS, 'w', force_zip64=True) as records:
                if self.store.bookmarks.path.exists():
                    with self.store.bookmarks.connection() as connection:
                        columns = ','.join(BOOKMARK_FIELDS)
                        for row in connection.execute('SELECT ' + columns + ' FROM bookmarks WHERE deleted_at IS NULL'):
                            data = (json.dumps(self.store.bookmarks.serialize(row), ensure_ascii=False) + '\n').encode()
                            total += len(data)
                            if total > MAX_EXPANDED:
                                raise BackupError(413, 'The wiki exceeds the ZIP backup size limit.')
                            records.write(data)
            archive.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False))
        if output.tell() > MAX_ARCHIVE:
            raise BackupError(413, 'The wiki exceeds the ZIP backup size limit.')

    def extract(self, archive_file, stage):
        try:
            with zipfile.ZipFile(archive_file) as archive:
                entries = archive.infolist()
                names = {entry.filename for entry in entries}
                if len(entries) != len(names) or len(entries) > MAX_FILES or not {MANIFEST, BOOKMARKS} <= names:
                    raise invalid()
                if sum(entry.file_size for entry in entries) > MAX_EXPANDED:
                    raise BackupError(413, 'The expanded backup exceeds 8 GiB.')
                for entry in entries:
                    mode = stat.S_IFMT(entry.external_attr >> 16)
                    if (entry.orig_filename != entry.filename or
                            (entry.filename != MANIFEST and not managed_member(entry.filename)) or
                            mode not in (0, stat.S_IFREG) or entry.flag_bits & 1 or
                            entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                        raise invalid()
                if archive.getinfo(MANIFEST).file_size > 16 * 1024**2:
                    raise invalid()
                manifest = json.loads(archive.read(MANIFEST))
                if manifest.get('format') != 'homewiki' or manifest.get('version') != 1:
                    raise invalid()
                times = manifest['modified_ns']
                if not isinstance(times, dict) or set(times) != names - {MANIFEST, BOOKMARKS}:
                    raise invalid()
                for directory in DIRECTORIES:
                    (stage / directory).mkdir()
                for name, timestamp in times.items():
                    if type(timestamp) is not int or not -2**63 <= timestamp <= 2**63 - 1:
                        raise invalid()
                    dest = stage / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(name) as source, dest.open('xb') as output:
                        shutil.copyfileobj(source, output, COPY_BLOCK)
                    os.utime(dest, ns=(timestamp, timestamp))
                    if name.endswith('.md'):
                        with dest.open(encoding='utf-8') as markdown:
                            while markdown.read(COPY_BLOCK):
                                pass
                    elif name.endswith('.json') and dest.stat().st_size > 16 * 1024**2:
                        raise invalid()
                self.validate_metadata(stage, times)
                with archive.open(BOOKMARKS) as records:
                    self.restore_bookmarks(records, stage)
                (stage / '.initialized').touch()
        except (zipfile.BadZipFile, ValueError, OverflowError, KeyError, AttributeError, TypeError,
                NotImplementedError, EOFError, FileExistsError, BookmarkError, sqlite3.IntegrityError, zlib.error, RecursionError) as error:
            raise invalid() from error

    def validate_metadata(self, stage, files):
        for name in files:
            if name == START_PAGE_FILE:
                with (stage / name).open(encoding='utf-8') as setting:
                    target = setting.read(401)
                if not managed_member('pages/' + target + '.md'):
                    raise invalid()
            if name == '.sidebar-order.json':
                value = json.loads((stage / name).read_text(encoding='utf-8'))
                parents = value['parents']
                if (value.get('version') != 1 or not isinstance(parents, dict)
                        or any(not isinstance(items, list) or any(not isinstance(item, str) for item in items)
                               for items in parents.values())):
                    raise invalid()
            if not name.startswith(('.trash/', '.history/')):
                continue
            parent, _, filename = name.rpartition('/')
            if parent + '/metadata.json' not in files or parent + '/page.md' not in files:
                raise invalid()
            if filename != 'metadata.json':
                continue
            info = json.loads((stage / name).read_text(encoding='utf-8'))
            if info['id'] != parent.rsplit('/', 1)[1] or not managed_member('pages/' + info['path'] + '.md'):
                raise invalid()
            fields = ('deleted',) if name.startswith('.trash/') else ('recorded', 'modified')
            if any(datetime.fromisoformat(info[key]).tzinfo is None for key in fields):
                raise invalid()
            if name.startswith('.history/'):
                with (stage / parent / 'page.md').open('rb') as page:
                    checksum = hashlib.sha256()
                    for chunk in iter(lambda: page.read(COPY_BLOCK), b''):
                        checksum.update(chunk)
                    digest = checksum.hexdigest()
                    size = os.fstat(page.fileno()).st_size
                if info['revision'] != digest or info['bytes'] != size:
                    raise invalid()

    @staticmethod
    def restore_bookmarks(records, stage):
        bookmarks = BookmarkStore(stage)
        columns = ','.join(BOOKMARK_FIELDS)
        placeholders = ','.join('?' for _ in BOOKMARK_FIELDS)
        with bookmarks.connection() as connection:
            while raw := records.readline(4 * 1024**2):
                if not raw.endswith(b'\n'):
                    raise invalid()
                record = json.loads(raw)
                if not isinstance(record, dict):
                    raise invalid()
                for key in BOOKMARK_FIELDS:
                    value = record[key]
                    if key not in ('tags', 'extracted_at') and not isinstance(value, str):
                        raise invalid()
                if any(not re.fullmatch(r'[0-9a-f]{32}', record[key]) for key in ('id', 'revision')):
                    raise invalid()
                if normalize_url(record['url'], max_length=12 * 4096 + 16) != (record['url'], record['domain']):
                    raise invalid()
                tags = normalize_tags(record['tags'])
                record['tags'] = json.dumps(tags, ensure_ascii=False)
                for key in ('created_at', 'updated_at', 'extracted_at'):
                    if key != 'extracted_at' or record[key] is not None:
                        if datetime.fromisoformat(record[key]).tzinfo is None:
                            raise invalid()
                connection.execute('INSERT INTO bookmarks(' + columns + ') VALUES(' + placeholders + ')',
                                   [record[key] for key in BOOKMARK_FIELDS])
                bookmarks.index(connection, record['id'])

    def restore(self, source, length):
        with self.one_transfer(), tempfile.TemporaryFile(dir=self.root) as upload:
            while length:
                data = source.read(min(length, COPY_BLOCK))
                if not data:
                    raise BackupError(400, 'The backup upload was interrupted.')
                upload.write(data)
                length -= len(data)
            upload.seek(0)
            with tempfile.TemporaryDirectory(prefix='.backup-stage-', dir=self.root) as name:
                stage = Path(name)
                self.extract(upload, stage)
                with self.exclusive():
                    self.install(stage)

    def install(self, stage):
        transaction = self.root / TRANSACTION
        transaction.mkdir(mode=0o700)
        try:
            old = transaction / 'old'
            old.mkdir()
            with (transaction / 'previous.zip').open('xb') as previous:
                os.fchmod(previous.fileno(), 0o600)
                self.write_zip(previous)
                previous.flush()
                os.fsync(previous.fileno())
            (transaction / 'previous.zip').replace(self.root / PREVIOUS)
            original = [name for name in LIVE_NAMES if (self.root / name).exists() or (self.root / name).is_symlink()]
            self.store.atomic(transaction / 'original.json', json.dumps(original))
            for name in original:
                (self.root / name).rename(old / name)
            for name in LIVE_NAMES:
                if (stage / name).exists():
                    (stage / name).rename(self.root / name)
            discard = stage / 'completed-transaction'
            transaction.rename(discard)
        except BaseException:
            self.recover()
            raise
        finally:
            self.store.bookmarks = BookmarkStore(self.root)
