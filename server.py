#!/usr/bin/env python3
"""Homewiki: a private, file-backed wiki. No third-party dependencies."""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

STATIC = Path(__file__).parent / 'static'
MAX_BODY = 2 * 1024 * 1024
LOCAL_HOSTS = frozenset(('localhost', '127.0.0.1', '::1'))

class Problem(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message

class Store:
    def __init__(self, root):
        self.root = Path(root).absolute()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.pages = self.root / 'pages'
        self.trash = self.root / '.trash'
        for p in (self.pages, self.trash):
            if p.is_symlink():
                raise ValueError('Storage directories cannot be symlinks')
            p.mkdir(mode=0o700, exist_ok=True)
        self.mutex = threading.RLock()

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

    def save(self, name, content, revision=None):
        path = self.path(name)
        if not isinstance(content, str) or len(content.encode()) > MAX_BODY - 4096:
            raise Problem(400, 'Page is too large or content is invalid')
        with self.locked():
            if path.exists():
                self.check(name, revision)
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
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dst)
            return self.read(target)

    def delete(self, name, revision):
        path = self.path(name)
        with self.locked():
            self.check(name, revision)
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
                if url.path == '/api/pages':
                    data = store.listing(args.get('q', [''])[0])
                elif url.path == '/api/page':
                    data = store.read(args.get('path', [''])[0])
                elif url.path == '/api/trash':
                    data = store.deleted()
                elif url.path in ('/', '/app.js', '/style.css', '/recently-edited') or url.path.startswith('/page/'):
                    file = STATIC / {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}.get(url.path, 'index.html')
                    kind = {'html': 'text/html', 'js': 'text/javascript', 'css': 'text/css'}[file.suffix[1:]]
                    self.reply(200, file.read_bytes(), kind + '; charset=utf-8')
                    return
                else:
                    raise Problem(404, 'Not found')
            elif self.command == 'POST':
                if self.headers.get('X-Homewiki') != '1' or self.headers.get('Content-Type') != 'application/json':
                    raise Problem(403, 'A Homewiki JSON request is required')
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= MAX_BODY:
                    raise Problem(413, 'Request too large or empty')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise Problem(400, 'Expected a JSON object')
                name, revision = body.get('path'), body.get('revision')
                if url.path == '/api/save':
                    data = store.save(name, body.get('content'), revision)
                elif url.path == '/api/rename':
                    data = store.rename(name, body.get('target'), revision)
                elif url.path == '/api/delete':
                    data = store.delete(name, revision)
                elif url.path == '/api/restore':
                    data = store.restore(body.get('id'))
                else:
                    raise Problem(404, 'Not found')
            self.reply(200, data)
        except Problem as e:
            self.reply(e.status, {'error': e.message})
        except (ValueError, TypeError, UnicodeError):
            self.reply(400, {'error': 'Invalid request or non-UTF-8 Markdown file'})
        except FileNotFoundError:
            self.reply(404, {'error': 'Page not found'})
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
