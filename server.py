#!/usr/bin/env python3
"""Homewiki: a private, file-backed wiki. No third-party dependencies."""
import argparse
import contextlib
import difflib
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import sqlite3
import subprocess
from bookmarks import BookmarkStore, BookmarkError, normalize_url
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

STATIC = Path(__file__).parent / 'static'
MAX_BODY = 2 * 1024 * 1024
MAX_IMAGE = 10 * 1024 * 1024
IMAGE_TYPES = {'png': 'image/png', 'jpg': 'image/jpeg', 'gif': 'image/gif', 'webp': 'image/webp'}
LOCAL_HOSTS = frozenset(('localhost', '127.0.0.1', '::1'))

def version_info(source=None):
    directory = Path(source or Path(__file__).parent).resolve()
    revision, dirty = '', False
    try:
        revision = (directory / 'REVISION').read_text(encoding='ascii').strip().lower()
    except (OSError, UnicodeError):
        pass
    if not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', revision):
        revision = ''
        try:
            def git(*args):
                return subprocess.run(['git', '-C', str(directory), *args], check=True, capture_output=True, text=True, timeout=2).stdout.strip()
            if Path(git('rev-parse', '--show-toplevel')).resolve() == directory:
                revision = git('rev-parse', 'HEAD').lower()
                dirty = bool(git('status', '--porcelain', '--untracked-files=no'))
        except (OSError, subprocess.SubprocessError):
            revision = ''
    if not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', revision):
        return {'revision': None, 'commit_url': None, 'dirty': False}
    return {'revision': revision, 'commit_url': 'https://github.com/rroller/homewiki/commit/' + revision, 'dirty': dirty}


RUNNING_VERSION = version_info()


class Problem(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message

class Store:
    def __init__(self, root):
        self.root = Path(root).absolute()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.pages = self.root / 'pages'
        self.trash = self.root / '.trash'
        self.uploads = self.root / 'uploads'
        self.history_root = self.root / '.history'
        for p in (self.pages, self.trash, self.uploads, self.history_root):
            if p.is_symlink():
                raise ValueError('Storage directories cannot be symlinks')
            p.mkdir(mode=0o700, exist_ok=True)
        self.mutex = threading.RLock()
        self.bookmarks = BookmarkStore(self.root)

    @contextlib.contextmanager
    def locked(self):
        with self.mutex, (self.root / '.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def path(self, name):
        if not isinstance(name, str) or len(name) > 400:
            raise Problem(400, 'Invalid page path')
        parts = name.split('/')
        if any(not p or p.strip() != p or p in ('.', '..') or p.startswith('.') or any(c in p for c in '\\:\x00\r\n') or any(ord(c) < 32 for c in p) for p in parts):
            raise Problem(400, 'Use names separated by /, without hidden names or special path characters')
        path = self.pages.joinpath(*parts).with_name(parts[-1] + '.md')
        for candidate in (path, *path.parents):
            if candidate.is_symlink():
                raise Problem(400, 'Symlinks are not supported')
            if candidate == self.pages:
                break
        return path

    def read(self, name):
        path = self.path(name)
        try:
            with path.open('rb') as file:
                raw = file.read()
                modified = datetime.fromtimestamp(os.fstat(file.fileno()).st_mtime, timezone.utc).isoformat()
        except FileNotFoundError:
            raise Problem(404, 'Page not found')
        return {'path': name, 'content': raw.decode('utf-8'), 'revision': hashlib.sha256(raw).hexdigest(), 'modified': modified}

    def upload_image(self, raw):
        if not raw or len(raw) > MAX_IMAGE:
            raise Problem(413, 'Images must be between 1 byte and 10 MB')
        if len(raw) >= 24 and raw.startswith(b'\x89PNG\r\n\x1a\n') and raw[12:16] == b'IHDR':
            extension = 'png'
        elif len(raw) >= 4 and raw.startswith(b'\xff\xd8\xff'):
            extension = 'jpg'
        elif len(raw) >= 13 and raw[:6] in (b'GIF87a', b'GIF89a'):
            extension = 'gif'
        elif len(raw) >= 16 and raw[:4] == b'RIFF' and raw[8:12] == b'WEBP' and raw[12:16] in (b'VP8 ', b'VP8L', b'VP8X'):
            extension = 'webp'
        else:
            raise Problem(415, 'Choose a PNG, JPEG, GIF, or WebP image. SVG and other file types are not supported.')
        name = uuid.uuid4().hex + '.' + extension
        path = self.uploads / name
        # Exclusive creation prevents replacing any existing upload.
        with path.open('xb') as file:
            try:
                os.chmod(path, 0o600)
                file.write(raw)
                file.flush()
                os.fsync(file.fileno())
            except BaseException:
                path.unlink(missing_ok=True)
                raise
        return {'url': '/uploads/' + name, 'type': IMAGE_TYPES[extension], 'size': len(raw)}

    def read_image(self, name):
        if not re.fullmatch(r'[0-9a-f]{32}\.(png|jpg|gif|webp)', name):
            raise Problem(404, 'Image not found')
        path = self.uploads / name
        if path.is_symlink() or not path.is_file():
            raise Problem(404, 'Image not found')
        return path.read_bytes(), IMAGE_TYPES[path.suffix[1:]]

    def listing(self, query=''):
        result = []
        for p in sorted(self.pages.rglob('*.md')):
            name = p.relative_to(self.pages).as_posix()[:-3]
            try:
                page = self.read(name)
            except (Problem, UnicodeError, OSError):
                continue
            content = page['content']
            if query.casefold() not in (name + '\n' + content).casefold():
                continue
            result.append({'path': name, 'excerpt': ' '.join(content.split())[:180], 'modified': page['modified']})
        return result

    def page_order(self):
        path = self.root / '.sidebar-order.json'
        if path.is_symlink():
            raise Problem(400, 'Symlinks are not supported')
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return {'parents': {}, 'revision': None}
        try:
            value = json.loads(raw)
            parents = value['parents']
            if not isinstance(parents, dict) or any(not isinstance(k, str) or not isinstance(v, list) or any(not isinstance(item, str) for item in v) for k, v in parents.items()):
                raise ValueError('Invalid order file')
        except (ValueError, KeyError, TypeError):
            raise Problem(500, 'Sidebar order data is invalid. Restore its backup or correct .sidebar-order.json.')
        return {'parents': parents, 'revision': hashlib.sha256(raw).hexdigest()}

    def reorder_pages(self, parent, children, revision):
        if not isinstance(parent, str) or not isinstance(children, list) or any(not isinstance(child, str) for child in children):
            raise Problem(400, 'Invalid page order')
        with self.locked():
            siblings = set()
            for page in self.listing():
                parts = page['path'].split('/')
                for index in range(len(parts)):
                    if '/'.join(parts[:index]) == parent:
                        siblings.add('/'.join(parts[:index+1]))
            if not siblings or len(children) != len(siblings) or set(children) != siblings:
                raise Problem(409, 'The page list changed. Refresh and try reordering again.')
            current = self.page_order()
            if current['revision'] != revision:
                raise Problem(409, 'The sidebar order changed in another tab. Refresh and try again.')
            parents = dict(current['parents'])
            parents[parent] = children
            self.atomic(self.root / '.sidebar-order.json', json.dumps({'version': 1, 'parents': parents}, ensure_ascii=False, indent=2))
            return self.page_order()

    def check(self, name, revision):
        current = self.read(name)
        if revision != current['revision']:
            raise Problem(409, 'This page changed on disk. Your draft is safe. Compare with the current page before saving.')
        return current

    def atomic(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix='.homewiki-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def history_dir(self, name):
        self.path(name)
        directory = self.history_root / hashlib.sha256(name.encode()).hexdigest()
        if self.history_root.is_symlink() or directory.is_symlink():
            raise Problem(400, 'Symlinks are not supported')
        return directory

    def history_item(self, name, token):
        if not isinstance(token, str) or not re.fullmatch(r'[0-9a-f]{32}', token):
            raise Problem(400, 'Invalid version ID')
        item = self.history_dir(name) / token
        if item.is_symlink() or any((item / file).is_symlink() for file in ('page.md', 'metadata.json')):
            raise Problem(400, 'Symlinks are not supported')
        return item

    def history_metadata(self, item):
        try:
            info = json.loads((item / 'metadata.json').read_text(encoding='utf-8'))
        except (ValueError, UnicodeError):
            raise Problem(500, 'Page history metadata is invalid. Check the data folder backup.')
        if (not isinstance(info, dict) or info.get('id') != item.name
                or not isinstance(info.get('revision'), str) or not re.fullmatch(r'[0-9a-f]{64}', info['revision'])
                or any(not isinstance(info.get(key), str) for key in ('path', 'modified', 'recorded'))
                or not isinstance(info.get('bytes'), int)):
            raise Problem(500, 'Page history metadata is invalid. Check the data folder backup.')
        return info

    def archive(self, page):
        # Publish both files together before allowing the current page to change.
        directory = self.history_dir(page['path'])
        directory.mkdir(mode=0o700, exist_ok=True)
        token = uuid.uuid4().hex
        pending = directory / ('.pending-' + token)
        pending.mkdir(mode=0o700)
        try:
            self.atomic(pending / 'page.md', page['content'])
            info = {key: page[key] for key in ('path', 'revision', 'modified')}
            info.update(id=token, recorded=datetime.now(timezone.utc).isoformat(), bytes=len(page['content'].encode()))
            self.atomic(pending / 'metadata.json', json.dumps(info, ensure_ascii=False))
            pending.rename(directory / token)
        finally:
            if pending.exists():
                shutil.rmtree(pending)

    def history(self, name):
        with self.locked():
            current = self.read(name)
            directory = self.history_dir(name)
            versions = []
            if directory.exists():
                for child in directory.iterdir():
                    if re.fullmatch(r'[0-9a-f]{32}', child.name):
                        item = self.history_item(name, child.name)
                        versions.append(self.history_metadata(item))
            return {'current': current, 'versions': sorted(versions, key=lambda item: (item['recorded'], item['id']), reverse=True)}

    def read_version(self, name, token):
        item = self.history_item(name, token)
        info = self.history_metadata(item)
        content = (item / 'page.md').read_bytes().decode('utf-8')
        if hashlib.sha256(content.encode()).hexdigest() != info['revision']:
            raise Problem(500, 'This saved version is damaged. Check the data folder backup.')
        return {**info, 'content': content}

    def compare_version(self, name, token):
        with self.locked():
            current = self.read(name)
            version = self.read_version(name, token)
        # Comparing large pages should not hold the mutation lock.
        diff = list(difflib.unified_diff(version['content'].splitlines(), current['content'].splitlines(),
                    fromfile='Selected version', tofile='Current page', lineterm=''))
        return {'current': current, 'version': version, 'diff': diff}

    def restore_version(self, name, token, revision):
        with self.locked():
            current = self.check(name, revision)
            version = self.read_version(name, token)
            if version['revision'] == current['revision']:
                return current
            if len(version['content'].encode()) > MAX_BODY - 4096:
                raise Problem(400, 'This saved version is too large to restore through Homewiki.')
            self.archive(current)
            self.atomic(self.path(name), version['content'])
            return self.read(name)

    def copy_history(self, name, target):
        source, destination = self.history_dir(name), self.history_dir(target)
        if not source.exists():
            return
        for entry in source.iterdir():
            if not re.fullmatch(r'[0-9a-f]{32}', entry.name):
                continue
            # Verify snapshots before copying, and retain originals if a move fails.
            version = self.read_version(name, entry.name)
            item = self.history_item(target, entry.name)
            if item.exists():
                if self.read_version(target, entry.name)['revision'] != version['revision']:
                    raise Problem(500, 'A saved version conflicts at the destination.')
                continue
            destination.mkdir(mode=0o700, exist_ok=True)
            pending = destination / ('.pending-' + uuid.uuid4().hex)
            try:
                pending.mkdir(mode=0o700)
                self.atomic(pending / 'page.md', version['content'])
                self.atomic(pending / 'metadata.json', json.dumps({key: value for key, value in version.items() if key != 'content'}, ensure_ascii=False))
                pending.rename(item)
            finally:
                if pending.exists():
                    shutil.rmtree(pending)

    def save(self, name, content, revision=None):
        path = self.path(name)
        if not isinstance(content, str) or len(content.encode()) > MAX_BODY - 4096:
            raise Problem(400, 'Page is too large or content is invalid')
        with self.locked():
            if path.exists():
                current = self.check(name, revision)
                if current['content'] == content:
                    return current
                self.archive(current)
            elif revision is not None:
                raise Problem(409, 'This page was moved or deleted. Your draft is safe.')
            self.atomic(path, content)
            return self.read(name)

    def rename(self, name, target, revision):
        src, dst = self.path(name), self.path(target)
        with self.locked():
            self.check(name, revision)
            # A rename moves only this Markdown file; nested pages remain independent.
            if src == dst:
                return self.read(name)
            if dst.exists():
                raise Problem(409, 'A page already exists at that path')
            self.copy_history(name, target)
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dst)
            return self.read(target)

    def delete(self, name, revision):
        path = self.path(name)
        with self.locked():
            current = self.check(name, revision)
            self.archive(current)
            token = uuid.uuid4().hex
            item = self.trash / token
            item.mkdir()
            self.atomic(item / 'metadata.json', json.dumps({'id': token, 'path': name, 'deleted': datetime.now(timezone.utc).isoformat()}))
            path.rename(item / 'page.md')
            return {'id': token}

    def deleted(self):
        result = []
        for item in self.trash.iterdir():
            if item.is_symlink() or not (item / 'page.md').is_file():
                continue
            try:
                result.append(json.loads((item / 'metadata.json').read_text()))
            except (OSError, ValueError):
                pass
        return sorted(result, key=lambda p: p['deleted'], reverse=True)

    def restore(self, token):
        if not isinstance(token, str) or len(token) != 32 or any(c not in '0123456789abcdef' for c in token):
            raise Problem(400, 'Invalid trash ID')
        with self.locked():
            item = self.trash / token
            if item.is_symlink() or not (item / 'page.md').exists():
                raise Problem(404, 'Deleted page not found')
            info = json.loads((item / 'metadata.json').read_text())
            dst = self.path(info['path'])
            if dst.exists():
                raise Problem(409, 'A page already uses this path. Rename it before restoring.')
            dst.parent.mkdir(parents=True, exist_ok=True)
            (item / 'page.md').rename(dst)
            shutil.rmtree(item)
            return self.read(info['path'])

    def seed(self):
        with self.locked():
            marker = self.root / '.initialized'
            if marker.exists():
                return
            if not any(self.pages.iterdir()):
                for name, content in {
                    'Home': '# A home for your thoughts\n\nWelcome to **Homewiki**. A quiet place for notes, lists, and the things you want to remember.\n\n## Make yourself at home\n\n- Create a page with **New page**\n- Organize pages with paths like `Games/Completed games`\n- Connect ideas with [[Games]] or use the link picker in the editor\n- Your pages are ordinary Markdown files. They belong to you.\n\n## On the shelf\n\n[[Games|Games & adventures]] · [[Reading list]]\n\n> Start small. A useful wiki grows one page at a time.\n',
                    'Games': '# Games\n\nA little space for adventures, finished and yet to come.\n\n- [[Games/Completed games|Completed games]]\n- [[Games/Games I own|Games I own]]\n',
                    'Games/Completed games': '# Completed games\n\nKeep a record of the worlds you have explored.\n\n| Game | Finished | Notes |\n| --- | --- | --- |\n| Add your first game | — | A favorite moment, perhaps |\n',
                    'Games/Games I own': '# Games I own\n\n- [ ] Add a game to your collection\n- [ ] Pick your next adventure\n',
                    'Reading list': '# Reading list\n\n## Up next\n\n- [ ] A book you have been meaning to read\n\n## Notes & discoveries\n\nGive a good idea a place to stay.\n'
                }.items():
                    self.atomic(self.path(name), content)
            marker.touch(exist_ok=True)

class Handler(BaseHTTPRequestHandler):
    server_version = 'Homewiki/1.0'

    def reply(self, status, data, kind='application/json; charset=utf-8'):
        raw = json.dumps(data).encode() if kind.startswith('application/json') else data
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(raw)

    def dispatch(self):
        try:
            host = self.headers.get('Host', '')
            parsed_host = urlparse('http://' + host)
            if parsed_host.hostname not in getattr(self.server, 'allowed_hosts', LOCAL_HOSTS):
                raise Problem(403, 'Host is not allowed by this Homewiki server')
            origin = self.headers.get('Origin')
            if origin and origin != 'http://' + host:
                raise Problem(403, 'Cross-origin requests are blocked')
            url = urlparse(self.path)
            args = parse_qs(url.query)
            store = self.server.store
            if self.command == 'GET':
                if url.path == '/api/version':
                    data = RUNNING_VERSION
                elif url.path == '/api/bookmarks':
                    data = store.bookmarks.listing(query=args.get('q', [''])[0], tags=args.get('tag', []),
                        domain=args.get('domain', [''])[0],
                        limit=int(args.get('limit', ['40'])[0]), offset=int(args.get('offset', ['0'])[0]),
                        sort=args.get('sort', ['newest'])[0])
                elif url.path == '/api/bookmarks/item':
                    data = store.bookmarks.get(args.get('id', [''])[0])
                elif url.path in ('/api/bookmarks/tags', '/api/bookmarks/domains'):
                    data = store.bookmarks.groups(url.path.rsplit('/', 1)[1])
                elif url.path == '/api/pages':
                    data = store.listing(args.get('q', [''])[0])
                elif url.path == '/api/page':
                    data = store.read(args.get('path', [''])[0])
                elif url.path.startswith('/uploads/'):
                    raw, kind = store.read_image(url.path[len('/uploads/'):])
                    self.reply(200, raw, kind)
                    return
                elif url.path == '/api/history':
                    data = store.history(args.get('path', [''])[0])
                elif url.path == '/api/history-version':
                    data = store.compare_version(args.get('path', [''])[0], args.get('version', [''])[0])
                elif url.path == '/api/order':
                    data = store.page_order()
                elif url.path == '/api/trash':
                    data = store.deleted()
                elif url.path in ('/', '/app.js', '/bookmarks.js', '/style.css', '/favicon.png', '/favicon.ico', '/recently-edited', '/bookmarks', '/bookmarks/tags', '/bookmarks/domains') or url.path.startswith('/page/'):
                    file = STATIC / {'/': 'index.html', '/app.js': 'app.js', '/bookmarks.js': 'bookmarks.js', '/style.css': 'style.css', '/favicon.png': 'favicon.png', '/favicon.ico': 'favicon.ico'}.get(url.path, 'index.html')
                    kind = {'html': 'text/html', 'js': 'text/javascript', 'css': 'text/css', 'png': 'image/png', 'ico': 'image/x-icon'}[file.suffix[1:]]
                    self.reply(200, file.read_bytes(), kind + ('; charset=utf-8' if kind.startswith('text/') else ''))
                    return
                elif url.path == '/api' or url.path.startswith('/api/') or url.path == '/uploads':
                    raise Problem(404, 'Not found')
                else:
                    # Root-level wiki URLs serve only the app shell, never arbitrary files.
                    name = unquote(url.path.lstrip('/').rstrip('/'))
                    status = 200 if store.path(name).is_file() else 404
                    self.reply(status, (STATIC / 'index.html').read_bytes(), 'text/html; charset=utf-8')
                    return
            elif self.command == 'POST':
                if url.path == '/api/upload':
                    if self.headers.get('X-Homewiki') != '1':
                        raise Problem(403, 'A Homewiki upload request is required')
                    content_type = self.headers.get('Content-Type', '').split(';')[0].strip().lower()
                    if content_type not in {*IMAGE_TYPES.values(), 'image/jpg', 'application/octet-stream'}:
                        raise Problem(415, 'Choose a PNG, JPEG, GIF, or WebP image')
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= MAX_IMAGE:
                        raise Problem(413, 'Images must be between 1 byte and 10 MB')
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        raise Problem(400, 'Image upload was interrupted')
                    self.reply(201, store.upload_image(raw))
                    return
                if self.headers.get('X-Homewiki') != '1' or self.headers.get('Content-Type') != 'application/json':
                    raise Problem(403, 'A Homewiki JSON request is required')
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= MAX_BODY:
                    raise Problem(413, 'Request too large or empty')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise Problem(400, 'Expected a JSON object')
                name, revision = body.get('path'), body.get('revision')
                if url.path == '/api/bookmarks/save':
                    data = store.bookmarks.save(body)
                elif url.path == '/api/bookmarks/title':
                    target, _ = normalize_url(body.get('url'))
                    data = {'url': target, 'title': store.bookmarks.resolve_title(target)}
                elif url.path == '/api/bookmarks/delete':
                    data = store.bookmarks.remove(body.get('id'), revision)
                elif url.path == '/api/bookmarks/extract':
                    data = store.bookmarks.refresh_text(body.get('id'), revision)
                elif url.path == '/api/save':
                    data = store.save(name, body.get('content'), revision)
                elif url.path == '/api/history-restore':
                    data = store.restore_version(name, body.get('version'), revision)
                elif url.path == '/api/order':
                    data = store.reorder_pages(body.get('parent'), body.get('children'), revision)
                elif url.path == '/api/rename':
                    data = store.rename(name, body.get('target'), revision)
                elif url.path == '/api/delete':
                    data = store.delete(name, revision)
                elif url.path == '/api/restore':
                    data = store.restore(body.get('id'))
                else:
                    raise Problem(404, 'Not found')
            self.reply(200, data)
        except (Problem, BookmarkError) as e:
            self.reply(e.status, {'error': e.message})
        except (ValueError, TypeError, UnicodeError):
            self.reply(400, {'error': 'Invalid request or non-UTF-8 Markdown file'})
        except FileNotFoundError:
            self.reply(404, {'error': 'Page not found'})
        except sqlite3.Error:
            self.reply(500, {'error': 'Bookmark database operation failed. Check storage permissions, free space, and SQLite FTS5 support.'})
        except OSError:
            self.reply(500, {'error': 'File operation failed. Check storage permissions and available disk space.'})

    do_GET = dispatch
    do_POST = dispatch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', default=os.getenv('HOMEWIKI_DATA', str(Path(__file__).parent / 'data')))
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--no-seed', action='store_true')
    parser.add_argument('--allow-host', action='append', default=[], help='Additional exact hostname or IP allowed for private LAN access')
    args = parser.parse_args()
    store = Store(args.data)
    if not args.no_seed:
        store.seed()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.store = store
    extra_hosts = args.allow_host + os.getenv('HOMEWIKI_ALLOWED_HOSTS', '').split(',')
    server.allowed_hosts = LOCAL_HOSTS | frozenset(h.strip().lower() for h in extra_hosts if h.strip())
    print(f'Homewiki: http://localhost:{args.port}  |  Pages: {store.pages}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == '__main__':
    main()
