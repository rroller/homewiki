"""Bookmark storage and extraction tests use temporary files and mocked HTTP."""
import concurrent.futures
from email.message import Message
import importlib.util
import json
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bookmarks as app


class BookmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = app.BookmarkStore(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def save(self, **values):
        return self.store.save({'url': 'https://example.com/guide', 'title': 'Useful guide',
                                'description': 'Notes for next weekend', 'tags': ['Python', 'reading'], **values})

    def test_crud_tags_domain_recency_and_restart(self):
        first = self.save()
        second = self.save(url='https://www.example.com/other', tags=['PYTHON', 'home lab', 'python'])
        self.assertEqual(first['domain'], second['domain'])
        self.assertEqual(second['tags'], ['home lab', 'python'])
        self.assertEqual([b['id'] for b in self.store.listing()['items']], [second['id'], first['id']])
        self.assertEqual(self.store.listing(tags=['python'], domain='example.com')['total'], 2)
        self.assertEqual(self.store.listing(tags=['python', 'home lab'])['total'], 1)
        self.assertEqual(self.store.groups('domains'), [{'name': 'example.com', 'count': 2}])
        edited = self.save(id=first['id'], revision=first['revision'], description='An edited description', tags=['wiki'])
        self.assertEqual(edited['created_at'], first['created_at'])
        self.assertNotEqual(edited['revision'], first['revision'])
        restarted = app.BookmarkStore(self.temp.name)
        self.assertEqual(restarted.get(first['id']), edited)
        self.assertEqual(restarted.listing(tags=['reading'])['total'], 0)
        self.assertEqual(restarted.listing('edited description')['total'], 1)
        self.assertEqual(Path(self.temp.name, 'bookmarks.sqlite3').stat().st_mode & 0o777, 0o600)

    def test_search_saved_text_and_manual_metadata(self):
        with patch.object(app, 'fetch_page_text', return_value={'title': 'Extracted title', 'text': 'Café telescope astronomy research'}):
            saved = self.save(title='', extract=True, description='My own notes')
        self.assertEqual(saved['title'], 'Extracted title')
        self.assertEqual(saved['description'], 'My own notes')
        self.assertEqual(saved['extraction_status'], 'saved')
        self.assertTrue(saved['extracted_at'])
        self.assertEqual(self.store.listing('cafe astro')['total'], 1)
        self.assertIn('telescope', self.store.listing('telescope')['items'][0]['excerpt'])
        self.assertNotIn('page_text', self.store.listing()['items'][0])
        for query in ['") OR *', 'title:foo NOT bar', '!!!']:
            self.assertEqual(self.store.listing(query)['total'], 0)
        updated = self.save(id=saved['id'], revision=saved['revision'], url='https://example.net/new')
        self.assertEqual(updated['page_text'], '')
        self.assertIsNone(updated['extracted_at'])
        self.assertEqual(self.store.listing('telescope')['total'], 0)

    def test_extraction_failure_keeps_link_and_previous_saved_text(self):
        with patch.object(app, 'fetch_page_text', side_effect=app.BookmarkError(400, 'Site unavailable')):
            failed = self.save(extract=True)
        self.assertEqual(failed['extraction_status'], 'failed')
        self.assertEqual(failed['description'], 'Notes for next weekend')
        with patch.object(app, 'fetch_page_text', return_value={'title': 'Other title', 'text': 'Searchable old archive'}):
            good = self.store.refresh_text(failed['id'], failed['revision'])
        self.assertEqual(good['title'], failed['title'])
        with patch.object(app, 'fetch_page_text', side_effect=app.BookmarkError(400, 'Timeout')):
            retry = self.store.refresh_text(good['id'], good['revision'])
        self.assertEqual(retry['page_text'], good['page_text'])
        self.assertEqual(retry['extracted_at'], good['extracted_at'])
        self.assertEqual(self.store.listing('archive')['total'], 1)

    def test_duplicate_links_and_conflicts_do_not_overwrite(self):
        first = self.save()
        for action in [lambda: self.save(), lambda: self.save(id=first['id'], revision='stale'),
                       lambda: self.store.remove(first['id'], 'stale')]:
            with self.assertRaises(app.BookmarkError) as caught:
                action()
            self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.store.get(first['id']), first)
        self.assertEqual(self.store.listing()['total'], 1)

    def test_soft_delete_restore_and_counts(self):
        saved = self.save()
        deleted = self.store.remove(saved['id'], saved['revision'])
        self.assertEqual(self.store.listing()['total'], 0)
        self.assertEqual(self.store.listing('guide', deleted=True)['total'], 1)
        self.assertEqual(self.store.groups('tags'), [])
        with self.assertRaises(app.BookmarkError):
            self.save()
        restored = self.store.remove(deleted['id'], deleted['revision'], restore=True)
        self.assertEqual(restored['created_at'], saved['created_at'])
        self.assertIsNone(restored['deleted_at'])
        self.assertEqual(self.store.listing(tags=['python'])['total'], 1)
        self.assertEqual(self.store.listing(deleted=True)['total'], 0)

    def test_simultaneous_writers_are_checked(self):
        first = self.save()
        def edit(i):
            try:
                self.save(id=first['id'], revision=first['revision'], description=str(i))
                return 200
            except app.BookmarkError as error:
                return error.status
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(edit, range(6)))
        self.assertEqual(results.count(200), 1)
        self.assertEqual(results.count(409), 5)
        self.assertEqual(self.store.listing()['total'], 1)

    def test_refresh_checks_revision_after_network_fetch(self):
        first = self.save()
        def fetch(url):
            self.save(id=first['id'], revision=first['revision'], description='Another browser')
            return {'title': 'Fetched', 'text': 'This must not overwrite newer data'}
        with patch.object(app, 'fetch_page_text', side_effect=fetch):
            with self.assertRaises(app.BookmarkError) as caught:
                self.store.refresh_text(first['id'], first['revision'])
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.store.get(first['id'])['description'], 'Another browser')
        self.assertEqual(self.store.get(first['id'])['page_text'], '')

    def test_pagination_sorting_and_validation(self):
        for i in range(4):
            self.save(url='https://example.com/'+str(i), title='Title '+str(i))
        self.assertEqual([item['title'] for item in self.store.listing(sort='title', limit=2, offset=1)['items']], ['Title 1', 'Title 2'])
        for kwargs in [{'limit': 101}, {'offset': -1}, {'sort': 'DROP TABLE bookmarks'}, {'tags': ['a'*65]}]:
            with self.assertRaises(app.BookmarkError):
                self.store.listing(**kwargs)
        for data in [{'url': 'javascript:alert(1)'}, {'tags': 'python'}, {'title': 'x'*301}, {'extract': 'yes'}, {'id': ''}]:
            with self.assertRaises(app.BookmarkError):
                self.save(**data)

    def test_database_symlink_and_existing_foreign_file_are_protected(self):
        other = Path(self.temp.name, 'other')
        other.write_text('do not replace')
        Path(self.temp.name, 'bookmarks.sqlite3').symlink_to(other)
        with self.assertRaises(app.BookmarkError):
            self.store.listing()
        self.assertEqual(other.read_text(), 'do not replace')
        Path(self.temp.name, 'bookmarks.sqlite3').unlink()
        with sqlite3.connect(Path(self.temp.name, 'bookmarks.sqlite3')) as connection:
            connection.execute('CREATE TABLE personal(value TEXT)')
            connection.execute("INSERT INTO personal VALUES('keep')")
        with self.assertRaises(app.BookmarkError):
            self.store.listing()
        with sqlite3.connect(Path(self.temp.name, 'bookmarks.sqlite3')) as connection:
            self.assertEqual(connection.execute('SELECT value FROM personal').fetchone()[0], 'keep')


class FetchTests(unittest.TestCase):
    def test_url_normalization_and_domain_grouping(self):
        self.assertEqual(app.normalize_url('HTTPS://WWW.Example.COM:443/hello world#chapter'), ('https://www.example.com/hello%20world#chapter', 'example.com'))
        self.assertEqual(app.normalize_url('192.168.1.2:8080/wiki')[0], 'https://192.168.1.2:8080/wiki')
        self.assertEqual(app.normalize_url('https://[::1]/')[1], '::1')
        for value in ['file:///etc/passwd', 'javascript:alert(1)', 'https://user:pass@example.com', 'http://example.com:0', 'https://example.com/\nHeader:x', 'https://bad%host/x']:
            with self.assertRaises(app.BookmarkError):
                app.normalize_url(value)

    def test_html_parser_keeps_article_and_ignores_scripts_and_navigation(self):
        parser = app.PageText()
        parser.feed('<html><head><title>A &amp; B</title><style>secret-style</style></head><body><nav>menu</nav><p>Intro</p><main><h1>Heading</h1><p>Useful &amp; readable</p><script>secret-script</script></main><footer>footer</footer></body></html>')
        self.assertEqual(parser.result(), {'title': 'A & B', 'text': 'Heading Useful & readable'})

    def test_public_dns_rejects_private_and_mixed_targets(self):
        for ip in ['127.0.0.1', '192.168.1.2', '169.254.169.254', '::1', 'fc00::1', '224.0.0.1', '::ffff:127.0.0.1']:
            records = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 80))]
            with patch.object(app.socket, 'getaddrinfo', return_value=records):
                with self.assertRaises(app.BookmarkError):
                    app.public_addresses('example.com', 80)
        records = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 80)),
                   (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.1', 80))]
        with patch.object(app.socket, 'getaddrinfo', return_value=records):
            with self.assertRaises(app.BookmarkError):
                app.public_addresses('example.com', 80)
        for host in ['localhost', 'server.lan', 'wiki.local']:
            with self.assertRaises(app.BookmarkError):
                app.public_addresses(host, 80)

    def response(self, body=b'<title>Guide</title><main>Useful astronomy notes</main>', status=200, kind='text/html', location=None):
        result = Mock()
        result.status = status
        result.headers = Message()
        result.headers['Content-Type'] = kind
        result.getheader.side_effect = lambda key, default=None: location if key == 'Location' else default
        chunks = [body, b'']
        result.read1.side_effect = lambda size: chunks.pop(0)
        return result

    def test_fetch_pins_validated_ip_and_tls_hostname(self):
        response = self.response()
        connection = Mock()
        connection.getresponse.return_value = response
        transport = Mock()
        tls = Mock()
        tls.wrap_socket.return_value = transport
        with patch.object(app, 'public_addresses', return_value=['8.8.8.8']), \
             patch.object(app.socket, 'create_connection', return_value=transport) as connect, \
             patch.object(app.http.client, 'HTTPConnection', return_value=connection), \
             patch.object(app.ssl, 'create_default_context', return_value=tls):
            result = app.fetch_page_text('https://example.com/guide')
        self.assertEqual(result, {'title': 'Guide', 'text': 'Useful astronomy notes'})
        self.assertEqual(connect.call_args.args[0], ('8.8.8.8', 443))
        self.assertEqual(tls.wrap_socket.call_args.kwargs['server_hostname'], 'example.com')
        self.assertEqual(connection.request.call_args.kwargs['headers']['Host'], 'example.com')
        connection.close.assert_called_once()

    def test_redirect_is_checked_before_connecting_to_private_target(self):
        connection = Mock()
        connection.getresponse.return_value = self.response(status=302, location='http://127.0.0.1/private')
        with patch.object(app, 'public_addresses', side_effect=[['8.8.8.8'], app.BookmarkError(400, 'Private target')]) as addresses, \
             patch.object(app.socket, 'create_connection', return_value=Mock()) as connect, \
             patch.object(app.http.client, 'HTTPConnection', return_value=connection):
            with self.assertRaises(app.BookmarkError):
                app.fetch_page_text('http://example.com/')
        self.assertEqual(addresses.call_count, 2)
        self.assertEqual(connect.call_count, 1)

    def test_size_type_and_port_limits(self):
        for response in [self.response(kind='image/png'), self.response(body=b'x'*20)]:
            connection = Mock()
            connection.getresponse.return_value = response
            with patch.object(app, 'MAX_FETCH', 16), \
                 patch.object(app, 'public_addresses', return_value=['8.8.8.8']), \
                 patch.object(app.socket, 'create_connection', return_value=Mock()), \
                 patch.object(app.http.client, 'HTTPConnection', return_value=connection):
                with self.assertRaises(app.BookmarkError):
                    app.fetch_page_text('http://example.com/')
        with patch.object(app, 'public_addresses') as resolve:
            with self.assertRaises(app.BookmarkError):
                app.fetch_page_text('http://example.com:8080/')
            resolve.assert_not_called()


if __name__ == '__main__':
    unittest.main()
