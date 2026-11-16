"""Private bookmark storage and optional public-page text extraction. Standard library only."""
import contextlib
from datetime import datetime, timezone
from html.parser import HTMLParser
import http.client
import ipaddress
import json
from pathlib import Path
import re
import socket
import sqlite3
import ssl
import threading
import time
import unicodedata
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
import uuid

MAX_FETCH = 2 * 1024 * 1024
MAX_TEXT = 300_000
FETCH_SLOTS = threading.BoundedSemaphore(2)


class BookmarkError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def normalize_url(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise BookmarkError(400, 'Enter a web address of up to 4096 characters.')
    value = value.strip()
    if any(ord(c) < 32 or ord(c) == 127 for c in value) or '\\' in value:
        raise BookmarkError(400, 'The web address contains invalid characters.')
    if value.startswith('//'):
        value = 'https:' + value
    elif not re.match(r'^[a-z][a-z0-9+.-]*://', value, re.I):
        if re.match(r'^[a-z][a-z0-9+.-]*:', value, re.I) and not re.match(r'^[^/:]+:\d+(?:[/?#]|$)', value):
            raise BookmarkError(400, 'Use an HTTP or HTTPS web address.')
        value = 'https://' + value
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError()
        host = parsed.hostname.rstrip('.').encode('idna').decode('ascii').lower()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not re.fullmatch(r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', host):
                raise ValueError()
        if '%' in host:
            raise ValueError()
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError()
    except (ValueError, UnicodeError):
        raise BookmarkError(400, 'Enter a valid web address without a username or password.')
    scheme = parsed.scheme.lower()
    netloc = '[' + host + ']' if ':' in host else host
    if port is not None and port != (443 if scheme == 'https' else 80):
        netloc += ':' + str(port)
    url = urlunsplit((scheme, netloc, quote(parsed.path or '/', safe="/%:@!$&'()*+,;=-._~"),
                     quote(parsed.query, safe="/?%:@!$&'()*+,;=-._~"), quote(parsed.fragment, safe="/?%:@!$&'()*+,;=-._~")))
    return url, host[4:] if host.startswith('www.') else host


def normalize_tags(value):
    if not isinstance(value, list) or len(value) > 32 or any(not isinstance(tag, str) for tag in value):
        raise BookmarkError(400, 'Use up to 32 tags.')
    tags = set()
    for item in value:
        tag = unicodedata.normalize('NFKC', item).strip().casefold()
        if not tag:
            continue
        if len(tag) > 64 or any(ord(c) < 32 or ord(c) == 127 for c in tag) or ',' in tag:
            raise BookmarkError(400, 'Tags must be at most 64 characters, without commas or control characters.')
        tags.add(tag)
    return sorted(tags)


class PageText(HTMLParser):
    ignored = {'script', 'style', 'noscript', 'nav', 'footer', 'header', 'svg', 'form', 'iframe', 'object', 'canvas', 'template', 'head'}
    blocks = {'p', 'div', 'li', 'br', 'h1', 'h2', 'h3', 'h4', 'section', 'article', 'main', 'td'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = []
        self.title_depth = 0
        self.main_depth = 0
        self.title = []
        self.parts = []
        self.main = []

    def handle_starttag(self, tag, attrs):
        if tag == 'title':
            self.title_depth += 1
        if tag in self.ignored:
            self.skip.append(tag)
        if tag in ('main', 'article'):
            self.main_depth += 1
        if tag in self.blocks and not self.skip:
            self.parts.append(' ')
            if self.main_depth:
                self.main.append(' ')

    def handle_endtag(self, tag):
        if tag == 'title':
            self.title_depth = max(0, self.title_depth - 1)
        if tag in self.skip:
            self.skip = self.skip[:self.skip.index(tag)]
        if tag in ('main', 'article'):
            self.main_depth = max(0, self.main_depth - 1)
        if tag in self.blocks and not self.skip:
            self.parts.append(' ')
            if self.main_depth:
                self.main.append(' ')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data):
        if self.title_depth:
            self.title.append(data)
        elif not self.skip:
            self.parts.append(data)
            if self.main_depth:
                self.main.append(data)

    def result(self):
        main = ' '.join(''.join(self.main).split())
        text = main if main else ' '.join(''.join(self.parts).split())
        return {'title': ' '.join(''.join(self.title).split())[:300], 'text': text[:MAX_TEXT]}


def public_addresses(host, port):
    if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal', '.home', '.lan')):
        raise BookmarkError(400, 'Page text can only be fetched from public web addresses.')
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses:
        raise BookmarkError(400, 'This site could not be resolved.')
    result = []
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if (not ip.is_global or ip.is_multicast or
                (isinstance(ip, ipaddress.IPv6Address) and (ip.ipv4_mapped or ip.sixtofour or ip.teredo))):
            raise BookmarkError(400, 'Page text can only be fetched from public web addresses.')
        result.append(str(ip))
    return list(dict.fromkeys(result))


def fetch_page_text(value, *, allow_title_only=False):
    """No proxies, credentials, cookies, subresources, scripts or unvalidated redirects."""
    if not FETCH_SLOTS.acquire(blocking=False):
        raise BookmarkError(429, 'Text extraction is busy. Try again shortly.')
    deadline = time.monotonic() + 15
    try:
        url, _ = normalize_url(value)
        for redirect in range(5):
            parsed = urlsplit(url)
            port = parsed.port or (443 if parsed.scheme == 'https' else 80)
            if port not in (80, 443):
                raise BookmarkError(400, 'Page text extraction supports public web ports 80 and 443.')
            addresses = public_addresses(parsed.hostname, port)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BookmarkError(408, 'Text extraction timed out.')
            transport = socket.create_connection((addresses[0], port), timeout=min(4, remaining))
            connection = http.client.HTTPConnection(parsed.hostname, port, timeout=min(4, remaining))
            try:
                if parsed.scheme == 'https':
                    transport = ssl.create_default_context().wrap_socket(transport, server_hostname=parsed.hostname)
                # Pin the validated address while retaining the original Host and TLS hostname.
                connection.sock = transport
                connection.request('GET', urlunsplit(('', '', parsed.path or '/', parsed.query, '')),
                                   headers={'Host': parsed.netloc, 'User-Agent': 'Homewiki/1.0 (bookmark text)',
                                            'Accept': 'text/html, text/plain, application/xhtml+xml', 'Accept-Encoding': 'identity',
                                            'Connection': 'close'})
                response = connection.getresponse()
                if response.status in (301, 302, 303, 307, 308):
                    location = response.getheader('Location')
                    if not location or redirect == 4:
                        raise BookmarkError(400, 'This site redirected too many times.')
                    url, _ = normalize_url(urljoin(url, location))
                    continue
                if not 200 <= response.status < 300:
                    raise BookmarkError(400, 'The site returned HTTP ' + str(response.status) + '.')
                kind = response.headers.get_content_type()
                if kind not in ('text/html', 'application/xhtml+xml', 'text/plain'):
                    raise BookmarkError(400, 'Only HTML and plain-text pages can be saved for search.')
                if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
                    raise BookmarkError(400, 'This site did not provide an uncompressed page.')
                raw = bytearray()
                while True:
                    if time.monotonic() >= deadline:
                        raise BookmarkError(408, 'Text extraction timed out.')
                    chunk = response.read1(min(65536, MAX_FETCH + 1 - len(raw)))
                    raw.extend(chunk)
                    if len(raw) > MAX_FETCH:
                        raise BookmarkError(400, 'This page is larger than the 2 MiB text extraction limit.')
                    if not chunk:
                        break
                charset = response.headers.get_content_charset() or 'utf-8'
                try:
                    source = bytes(raw).decode(charset, errors='replace')
                except LookupError:
                    source = bytes(raw).decode('utf-8', errors='replace')
                if kind == 'text/plain':
                    result = {'title': '', 'text': ' '.join(source.split())[:MAX_TEXT]}
                else:
                    parser = PageText()
                    parser.feed(source)
                    result = parser.result()
                if not result['text'] and not (allow_title_only and result['title']):
                    raise BookmarkError(400, 'No readable page text was found.')
                return result
            finally:
                connection.close()
                transport.close()
        raise BookmarkError(400, 'This site redirected too many times.')
    except (OSError, http.client.HTTPException):
        raise BookmarkError(400, 'The site could not be fetched. You can retry saving its text later.')
    finally:
        FETCH_SLOTS.release()


class BookmarkStore:
    fields = 'b.id,b.url,b.title,b.description,b.domain,b.tags,b.created_at,b.updated_at,b.revision,b.deleted_at,b.extraction_status,b.extraction_error,b.extracted_at'

    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / 'bookmarks.sqlite3'
        self.init_lock = threading.Lock()
        self.ready = False

    @contextlib.contextmanager
    def connection(self):
        if any((self.root / ('bookmarks.sqlite3' + suffix)).is_symlink() for suffix in ('', '-journal', '-wal', '-shm')):
            raise BookmarkError(400, 'Bookmark database symlinks are not supported.')
        with self.init_lock:
            if not self.ready:
                # Reserve a new database privately without replacing an existing file.
                try:
                    with self.path.open('xb') as file:
                        import os
                        os.chmod(self.path, 0o600)
                except FileExistsError:
                    pass
                with contextlib.closing(sqlite3.connect(self.path, timeout=10)) as setup:
                    application = setup.execute('PRAGMA application_id').fetchone()[0]
                    if application not in (0, 1213678157) or (application == 0 and setup.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]):
                        raise BookmarkError(409, 'bookmarks.sqlite3 already exists and is not a Homewiki bookmark database.')
                    setup.executescript("""
                        BEGIN IMMEDIATE;
                        PRAGMA application_id=1213678157;
                        CREATE TABLE IF NOT EXISTS bookmarks(
                            rowid INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE, url TEXT NOT NULL UNIQUE,
                            title TEXT NOT NULL, description TEXT NOT NULL, domain TEXT NOT NULL, tags TEXT NOT NULL,
                            page_text TEXT NOT NULL DEFAULT '', extraction_status TEXT NOT NULL DEFAULT 'not_requested',
                            extraction_error TEXT NOT NULL DEFAULT '', extracted_at TEXT,
                            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, revision TEXT NOT NULL, deleted_at TEXT
                        );
                        CREATE TABLE IF NOT EXISTS bookmark_tags(
                            bookmark_id TEXT NOT NULL REFERENCES bookmarks(id) ON DELETE CASCADE,
                            tag TEXT NOT NULL, PRIMARY KEY(bookmark_id,tag)
                        );
                        CREATE INDEX IF NOT EXISTS bookmark_tag_lookup ON bookmark_tags(tag);
                        CREATE INDEX IF NOT EXISTS bookmark_recent ON bookmarks(deleted_at,created_at);
                        CREATE INDEX IF NOT EXISTS bookmark_domains ON bookmarks(domain,deleted_at);
                        CREATE VIRTUAL TABLE IF NOT EXISTS bookmark_search USING fts5(
                            title,url,description,tags,page_text,tokenize='unicode61 remove_diacritics 2'
                        );
                        DELETE FROM bookmark_search WHERE rowid IN (SELECT rowid FROM bookmarks WHERE deleted_at IS NOT NULL);
                        DELETE FROM bookmark_tags WHERE bookmark_id IN (SELECT id FROM bookmarks WHERE deleted_at IS NOT NULL);
                        DELETE FROM bookmarks WHERE deleted_at IS NOT NULL;
                        COMMIT;
                    """)
                    setup.commit()
                self.ready = True
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def serialize(self, row):
        value = dict(row)
        value['tags'] = json.loads(value['tags'])
        return value

    def item(self, connection, token):
        if not isinstance(token, str) or not re.fullmatch(r'[0-9a-f]{32}', token):
            raise BookmarkError(400, 'Invalid bookmark ID.')
        row = connection.execute('SELECT * FROM bookmarks WHERE id=?', (token,)).fetchone()
        if row is None:
            raise BookmarkError(404, 'Bookmark not found.')
        return row

    def get(self, token):
        with self.connection() as connection:
            return self.serialize(self.item(connection, token))

    def check(self, row, revision):
        if row['revision'] != revision:
            raise BookmarkError(409, 'This bookmark changed in another tab. Reopen it to see the latest version; your draft is kept.')

    def index(self, connection, token):
        row = self.item(connection, token)
        connection.execute('DELETE FROM bookmark_search WHERE rowid=?', (row['rowid'],))
        connection.execute('INSERT INTO bookmark_search(rowid,title,url,description,tags,page_text) VALUES(?,?,?,?,?,?)',
                           (row['rowid'], row['title'], row['url'], row['description'], ' '.join(json.loads(row['tags'])), row['page_text']))
        connection.execute('DELETE FROM bookmark_tags WHERE bookmark_id=?', (token,))
        connection.executemany('INSERT INTO bookmark_tags(bookmark_id,tag) VALUES(?,?)', [(token, tag) for tag in json.loads(row['tags'])])

    def extract(self, url):
        try:
            result = fetch_page_text(url, allow_title_only=True)
            if not result['text']:
                return {'title': result['title'], 'extraction_status': 'failed', 'extraction_error': 'No readable page text was found.'}
            return {'page_text': result['text'], 'extraction_status': 'saved', 'extraction_error': '',
                    'extracted_at': datetime.now(timezone.utc).isoformat(), 'title': result['title']}
        except BookmarkError as error:
            return {'extraction_status': 'failed', 'extraction_error': error.message}

    def resolve_title(self, url):
        try:
            return fetch_page_text(url, allow_title_only=True)['title']
        except BookmarkError:
            return ''

    def save(self, body):
        url, domain = normalize_url(body.get('url'))
        title, description = body.get('title', ''), body.get('description', '')
        if not isinstance(title, str) or len(title) > 300 or not isinstance(description, str) or len(description) > 2000:
            raise BookmarkError(400, 'Use a title up to 300 characters and a description up to 2000 characters.')
        tags = normalize_tags(body.get('tags', []))
        token = body.get('id')
        if type(body.get('extract', False)) is not bool:
            raise BookmarkError(400, 'Invalid text extraction setting.')
        extracted = self.extract(url) if body.get('extract', False) else {}
        resolved_title = self.resolve_title(url) if token is None and not title.strip() and not body.get('extract', False) else ''
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self.connection() as connection:
                connection.execute('BEGIN IMMEDIATE')
                previous = self.item(connection, token) if token is not None else None
                if previous:
                    self.check(previous, body.get('revision'))
                    if previous['deleted_at']:
                        raise BookmarkError(409, 'Restore this bookmark before editing it.')
                elif body.get('revision') is not None:
                    raise BookmarkError(409, 'This bookmark no longer exists. Your draft is kept.')
                saved_title = title.strip() or (previous['title'] if previous and previous['url'] == url else '') or extracted.get('title') or resolved_title or domain
                values = {'url': url, 'title': saved_title, 'description': description.strip(), 'domain': domain,
                          'tags': json.dumps(tags, ensure_ascii=False), 'updated_at': now, 'revision': uuid.uuid4().hex}
                if previous is None or previous['url'] != url:
                    values.update(page_text='', extraction_status='not_requested', extraction_error='', extracted_at=None)
                values.update({key: value for key, value in extracted.items() if key != 'title'})
                if previous:
                    connection.execute('UPDATE bookmarks SET '+','.join(key+'=?' for key in values)+' WHERE id=?', [*values.values(), token])
                else:
                    token = uuid.uuid4().hex
                    values.update(id=token, created_at=now)
                    connection.execute('INSERT INTO bookmarks('+','.join(values)+') VALUES('+','.join('?' for _ in values)+')', list(values.values()))
                self.index(connection, token)
                return self.serialize(self.item(connection, token))
        except sqlite3.IntegrityError:
            raise BookmarkError(409, 'This link is already bookmarked. Edit the existing bookmark instead.')

    def remove(self, token, revision):
        with self.connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = self.item(connection, token)
            self.check(row, revision)
            connection.execute('DELETE FROM bookmark_search WHERE rowid=?', (row['rowid'],))
            connection.execute('DELETE FROM bookmarks WHERE id=?', (token,))
            return {'id': token}

    def refresh_text(self, token, revision):
        row = self.get(token)
        self.check(row, revision)
        if row['deleted_at']:
            raise BookmarkError(409, 'Restore this bookmark before saving its page text.')
        extracted = self.extract(row['url'])
        # A failed refresh retains previously saved text; changing URL clears text in save().
        with self.connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            latest = self.item(connection, token)
            self.check(latest, revision)
            values = {key: value for key, value in extracted.items() if key != 'title'}
            values.update(updated_at=datetime.now(timezone.utc).isoformat(), revision=uuid.uuid4().hex)
            connection.execute('UPDATE bookmarks SET '+','.join(key+'=?' for key in values)+' WHERE id=?', [*values.values(), token])
            self.index(connection, token)
            return self.serialize(self.item(connection, token))

    def listing(self, query='', tags=None, domain='', limit=40, offset=0, sort='newest'):
        if not isinstance(query, str) or len(query) > 500:
            raise BookmarkError(400, 'Search is limited to 500 characters.')
        tags = normalize_tags(tags or [])
        if not isinstance(domain, str) or len(domain) > 253:
            raise BookmarkError(400, 'Invalid domain filter.')
        if not 1 <= limit <= 100 or not 0 <= offset <= 100000:
            raise BookmarkError(400, 'Invalid bookmark page.')
        order = {'newest': 'b.created_at DESC,b.id', 'oldest': 'b.created_at,b.id', 'title': 'b.title COLLATE NOCASE,b.id'}
        if sort not in order:
            raise BookmarkError(400, 'Invalid bookmark sort order.')
        where, args = ['b.deleted_at IS NULL'], []
        for tag in tags:
            where.append('EXISTS(SELECT 1 FROM bookmark_tags t WHERE t.bookmark_id=b.id AND t.tag=?)')
            args.append(tag)
        if domain:
            where.append('b.domain=?')
            args.append(domain.lower())
        terms = re.findall(r'[^\W_]+', query, re.UNICODE)[:24]
        join = ''
        excerpt = "substr(b.page_text,1,240)"
        if query.strip() and not terms:
            where.append('0')
        if terms:
            join = ' JOIN bookmark_search ON bookmark_search.rowid=b.rowid'
            where.append('bookmark_search MATCH ?')
            args.append(' AND '.join('"'+term+'"'+'*' for term in terms))
            excerpt = "snippet(bookmark_search,4,'','',' … ',32)"
        clause = ' AND '.join(where)
        with self.connection() as connection:
            total = connection.execute('SELECT count(*) FROM bookmarks b'+join+' WHERE '+clause, args).fetchone()[0]
            rows = connection.execute('SELECT '+self.fields+','+excerpt+' AS excerpt FROM bookmarks b'+join+' WHERE '+clause+' ORDER BY '+order[sort]+' LIMIT ? OFFSET ?', [*args, limit, offset]).fetchall()
            return {'items': [self.serialize(row) for row in rows], 'total': total, 'limit': limit, 'offset': offset}

    def groups(self, kind):
        with self.connection() as connection:
            if kind == 'tags':
                rows = connection.execute('SELECT t.tag AS name,count(*) AS count FROM bookmark_tags t JOIN bookmarks b ON b.id=t.bookmark_id WHERE b.deleted_at IS NULL GROUP BY t.tag ORDER BY t.tag COLLATE NOCASE').fetchall()
            else:
                rows = connection.execute('SELECT domain AS name,count(*) AS count FROM bookmarks WHERE deleted_at IS NULL GROUP BY domain ORDER BY domain').fetchall()
            return [dict(row) for row in rows]
