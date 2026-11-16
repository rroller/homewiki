import concurrent.futures
import base64
import importlib.util
import sys
import json
import struct
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
spec = importlib.util.spec_from_file_location('homewiki', Path(__file__).resolve().parents[1] / 'server.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aL1kAAAAASUVORK5CYII=')

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = app.Store(self.temp.name)
    def tearDown(self):
        self.temp.cleanup()
    def test_sidebar_order_persists_without_changing_pages(self):
        for name in ['Games', 'Games/Owned', 'Games/Completed', 'Home', 'Notes/Child']:
            self.store.save(name, '# '+name)
        before={p['path']: self.store.read(p['path']) for p in self.store.listing()}
        root=self.store.reorder_pages('', ['Notes', 'Home', 'Games'], None)
        nested=self.store.reorder_pages('Games', ['Games/Owned', 'Games/Completed'], root['revision'])
        restarted=app.Store(self.temp.name)
        self.assertEqual(restarted.page_order(), nested)
        self.assertEqual(nested['parents'][''], ['Notes', 'Home', 'Games'])
        self.assertEqual({p['path']: restarted.read(p['path']) for p in restarted.listing()}, before)
        # Deleted entries disappear naturally; new siblings can be ordered.
        page=self.store.read('Games/Completed')
        self.store.delete(page['path'], page['revision'])
        self.store.save('Games/New', '# New')
        new=self.store.reorder_pages('Games', ['Games/New', 'Games/Owned'], nested['revision'])
        self.assertEqual(new['parents']['Games'], ['Games/New', 'Games/Owned'])

    def test_sidebar_order_rejects_invalid_and_concurrent_changes(self):
        for name in ['A', 'B', 'A/Child']:self.store.save(name, name)
        for parent,children in [('', ['A']), ('', ['A','A']), ('', ['A','A/Child']), ('Missing', [])]:
            with self.assertRaises(app.Problem) as caught:self.store.reorder_pages(parent, children, None)
            self.assertEqual(caught.exception.status,409)
        with self.assertRaises(app.Problem) as caught:self.store.reorder_pages('', 'A', None)
        self.assertEqual(caught.exception.status,400)
        def write(i):
            try:self.store.reorder_pages('', ['A','B'] if i%2 else ['B','A'], None);return 200
            except app.Problem as error:return error.status
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results=list(pool.map(write,range(6)))
        self.assertEqual(results.count(200),1)
        self.assertEqual(results.count(409),5)

    def test_history_saves_compares_restores_and_persists(self):
        first=self.store.save('Notes/Example', '# First\nOne\n')
        self.assertEqual(self.store.history(first['path'])['versions'],[])
        second=self.store.save(first['path'], '# Second\nTwo\n', first['revision'])
        versions=self.store.history(first['path'])['versions']
        self.assertEqual(len(versions),1)
        original=self.store.read_version(first['path'],versions[0]['id'])
        self.assertEqual(original['content'],first['content'])
        self.assertEqual(original['modified'],first['modified'])
        self.assertEqual(app.Store(self.temp.name).read_version(first['path'],original['id']),original)
        comparison=self.store.compare_version(first['path'],original['id'])
        self.assertIn('-One',comparison['diff']);self.assertIn('+Two',comparison['diff'])
        # An unchanged save creates no duplicate history entry or edit date.
        self.assertEqual(self.store.save(second['path'],second['content'],second['revision']),second)
        self.assertEqual(len(self.store.history(first['path'])['versions']),1)
        restored=self.store.restore_version(first['path'],original['id'],second['revision'])
        self.assertEqual(restored['content'],first['content'])
        versions=self.store.history(first['path'])['versions']
        self.assertEqual(len(versions),2)
        self.assertEqual(self.store.read_version(first['path'],versions[0]['id'])['content'],second['content'])
        self.store.restore_version(first['path'],original['id'],restored['revision'])
        self.assertEqual(len(self.store.history(first['path'])['versions']),2)

    def test_history_follows_rename_and_trash_and_keeps_external_content(self):
        first=self.store.save('Old','# First')
        second=self.store.save('Old','# Second',first['revision'])
        self.store.path('Old').write_text('# External',encoding='utf-8')
        external=self.store.read('Old')
        latest=self.store.save('Old','# Latest',external['revision'])
        moved=self.store.rename('Old','Folder/New',latest['revision'])
        versions=self.store.history(moved['path'])['versions']
        self.assertEqual([self.store.read_version(moved['path'],v['id'])['content'] for v in versions],['# External','# First'])
        deleted=self.store.delete(moved['path'],moved['revision'])
        restored=self.store.restore(deleted['id'])
        versions=app.Store(self.temp.name).history(restored['path'])['versions']
        self.assertEqual(len(versions),3)
        self.assertEqual(self.store.read_version(restored['path'],versions[0]['id'])['content'],'# Latest')
        self.assertEqual(self.store.listing('First'),[])

    def test_history_conflicts_and_concurrent_restores(self):
        first=self.store.save('Note','one')
        current=self.store.save('Note','two',first['revision'])
        token=self.store.history('Note')['versions'][0]['id']
        with self.assertRaises(app.Problem) as caught:self.store.restore_version('Note',token,first['revision'])
        self.assertEqual(caught.exception.status,409)
        def restore(i):
            try:self.store.restore_version('Note',token,current['revision']);return 200
            except app.Problem as error:return error.status
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results=list(pool.map(restore,range(6)))
        self.assertEqual(results.count(200),1);self.assertEqual(results.count(409),5)
        self.assertEqual(self.store.read('Note')['content'],'one')
        self.assertEqual(len(self.store.history('Note')['versions']),2)

    def test_history_paths_symlinks_and_damaged_snapshots(self):
        first=self.store.save('Note','one')
        current=self.store.save('Note','two',first['revision'])
        token=self.store.history('Note')['versions'][0]['id']
        for bad in ['../escape','x',None,'A'*32]:
            with self.assertRaises(app.Problem):self.store.read_version('Note',bad)
        with self.assertRaises(app.Problem):self.store.history('../escape')
        item=self.store.history_item('Note',token)
        snapshot=item/'page.md'
        snapshot.write_text('tampered')
        with self.assertRaises(app.Problem) as caught:self.store.restore_version('Note',token,current['revision'])
        self.assertEqual(caught.exception.status,500)
        self.assertEqual(self.store.read('Note'),current)
        snapshot.unlink();snapshot.symlink_to(self.store.path('Note'))
        with self.assertRaises(app.Problem):self.store.read_version('Note',token)
        snapshot.unlink();snapshot.write_text('one')
        metadata=item/'metadata.json';original_metadata=metadata.read_text()
        metadata.write_text('{')
        with self.assertRaises(app.Problem) as caught:self.store.history('Note')
        self.assertEqual(caught.exception.status,500)
        metadata.write_text(original_metadata)
        directory=self.store.history_dir('Note')
        destination=Path(self.temp.name)/'outside-history'
        directory.rename(destination);directory.symlink_to(destination,target_is_directory=True)
        with self.assertRaises(app.Problem):self.store.history('Note')

    def test_history_write_failure_prevents_page_overwrite(self):
        from unittest.mock import patch
        first=self.store.save('Note','one')
        atomic=self.store.atomic
        def fail_history(path,content):
            if self.store.history_root in path.parents:raise OSError('Storage full')
            return atomic(path,content)
        with patch.object(self.store,'atomic',side_effect=fail_history):
            with self.assertRaises(OSError):self.store.save('Note','two',first['revision'])
        self.assertEqual(self.store.read('Note'),first)
        self.assertEqual(self.store.history('Note')['versions'],[])
        self.assertEqual(list(self.store.history_dir('Note').iterdir()),[])

    def test_crud_search_and_restart(self):
        page = self.store.save('Games/Completed games', '# Finished\nCafé and Zelda')
        self.assertEqual(Path(self.temp.name, 'pages/Games/Completed games.md').read_text(), page['content'])
        self.assertEqual(self.store.listing('CAFÉ')[0]['path'], page['path'])
        page = self.store.save(page['path'], '# Changed', page['revision'])
        self.assertEqual(app.Store(self.temp.name).read(page['path']), page)
        renamed = self.store.rename(page['path'], 'Archive/Done', page['revision'])
        item = self.store.delete(renamed['path'], renamed['revision'])
        self.assertEqual(self.store.deleted()[0]['path'], 'Archive/Done')
        self.assertEqual(self.store.restore(item['id'])['content'], '# Changed')
        self.assertEqual(self.store.deleted(), [])
    def test_images_persist_through_page_moves_trash_and_restart(self):
        image=self.store.upload_image(PNG)
        self.assertEqual(image['type'],'image/png')
        name=image['url'].split('/')[-1]
        self.assertEqual(self.store.read_image(name),(PNG,'image/png'))
        page=self.store.save('Photos/Note','![Photo]('+image['url']+')')
        page=self.store.rename(page['path'],'Archive/Note',page['revision'])
        token=self.store.delete(page['path'],page['revision'])['id']
        self.assertEqual(self.store.read_image(name)[0],PNG)
        self.assertIn(image['url'],self.store.restore(token)['content'])
        self.assertEqual(app.Store(self.temp.name).read_image(name)[0],PNG)
        self.assertNotEqual(image['url'],self.store.upload_image(PNG)['url'])
    def test_invalid_images_and_upload_paths(self):
        for raw in [b'<svg xmlns="http://www.w3.org/2000/svg"></svg>',b'<script>alert(1)</script>',b'fake PNG']:
            with self.assertRaises(app.Problem) as caught:self.store.upload_image(raw)
            self.assertEqual(caught.exception.status,415)
        with self.assertRaises(app.Problem):self.store.upload_image(b'x'*(app.MAX_IMAGE+1))
        for name in ['../Home.md','../../secret','a.png','0'*32+'.svg']:
            with self.assertRaises(app.Problem):self.store.read_image(name)
        outside=Path(self.temp.name)/'outside.png';outside.write_bytes(PNG)
        name='0'*32+'.png';(self.store.uploads/name).symlink_to(outside)
        with self.assertRaises(app.Problem):self.store.read_image(name)

    def test_conflicts_and_existing_paths_never_overwritten(self):
        first = self.store.save('Note', 'one')
        self.store.save('Note', 'two', first['revision'])
        for action in [lambda: self.store.save('Note','bad',first['revision']), lambda:self.store.rename('Note','Moved',first['revision']),lambda:self.store.delete('Note',first['revision']),lambda:self.store.save('Note','new')]:
            with self.assertRaises(app.Problem) as caught:
                action()
            self.assertEqual(caught.exception.status, 409)
        current=self.store.read('Note')
        self.store.save('Taken','existing')
        with self.assertRaises(app.Problem):self.store.rename('Note','Taken',current['revision'])
        self.assertEqual(self.store.read('Note')['content'],'two')
        token=self.store.delete('Note',current['revision'])['id']
        self.store.save('Note','replacement')
        with self.assertRaises(app.Problem):self.store.restore(token)
        self.assertTrue(self.store.deleted())
    def test_external_edits_and_delete(self):
        page=self.store.save('Note','original')
        self.store.path('Note').write_text('external')
        with self.assertRaises(app.Problem):self.store.save('Note','stale',page['revision'])
        self.store.path('Note').unlink()
        with self.assertRaises(app.Problem):self.store.save('Note','stale',page['revision'])
    def test_simultaneous_writers(self):
        page=self.store.save('Note','original')
        def write(i):
            try:self.store.save('Note',str(i),page['revision']);return 200
            except app.Problem as e:return e.status
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(write,range(8)))
        self.assertEqual(results.count(200),1)
        self.assertEqual(results.count(409),7)
    def test_paths_and_symlinks(self):
        for name in ['', '../escape','/absolute','A/../B','A//B','.hidden','A\\B','A\x00B','A/ B','A\nB']:
            with self.assertRaises(app.Problem):self.store.save(name,'no')
        outside=Path(self.temp.name)/'outside';outside.mkdir()
        (self.store.pages/'link').symlink_to(outside,target_is_directory=True)
        with self.assertRaises(app.Problem):self.store.save('link/escape','no')
        (self.store.pages/'note.md').symlink_to(outside/'target')
        with self.assertRaises(app.Problem):self.store.save('note','no')
        self.assertEqual(self.store.listing(),[])
    def test_nested_files_independent(self):
        parent=self.store.save('Games','parent')
        self.store.save('Games/Child','child')
        self.store.rename('Games','Hobbies',parent['revision'])
        self.assertEqual(self.store.read('Games/Child')['content'],'child')
    def test_seed_preserves_existing_and_stays_deleted(self):
        self.store.save('Existing','mine');self.store.seed()
        self.assertEqual([p['path'] for p in self.store.listing()],['Existing'])
        self.store.delete('Existing',self.store.read('Existing')['revision'])
        self.store.seed();self.assertEqual(self.store.listing(),[])

class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        cls.server.store=app.Store(cls.temp.name)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.url='http://127.0.0.1:'+str(cls.server.server_port)
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.temp.cleanup()
    def request(self,path,body=None,headers=None):
        req=Request(self.url+path,data=body if isinstance(body,bytes) else json.dumps(body).encode() if body is not None else None,headers=headers or {})
        try:
            with urlopen(req) as response:return response.status,response.read(),response.headers
        except HTTPError as e:
            with e:return e.code,e.read(),e.headers
    def test_http_assets_and_lifecycle(self):
        for path in ['/','/app.js','/style.css','/page/Home','/page/Games/Games%20I%20own','/recently-edited']:
            status,body,headers=self.request(path);self.assertEqual(status,200);self.assertIn("default-src 'self'",headers['Content-Security-Policy']);self.assertTrue(body)
        headers={'Content-Type':'application/json','X-Homewiki':'1'}
        status,raw,_=self.request('/api/save',{'path':'Test/Nested','content':'hello'},headers);self.assertEqual(status,200)
        page=json.loads(raw)
        self.assertEqual(self.request('/api/save',{'path':page['path'],'content':'oops'},headers)[0],409)
        self.assertEqual(self.request('/api/delete',page,headers)[0],200)
        status,raw,_=self.request('/api/trash');self.assertEqual(len(json.loads(raw)),1)
        self.assertEqual(self.request('/api/restore',{'id':json.loads(raw)[0]['id']},headers)[0],200)
    def test_favicon_assets(self):
        status,png,headers=self.request('/favicon.png')
        self.assertEqual(status,200);self.assertEqual(headers['Content-Type'],'image/png')
        self.assertEqual(png[:8],b'\x89PNG\r\n\x1a\n')
        self.assertEqual(struct.unpack('>II',png[16:24]),(128,128))
        status,ico,headers=self.request('/favicon.ico')
        self.assertEqual(status,200);self.assertEqual(headers['Content-Type'],'image/x-icon')
        self.assertEqual(struct.unpack('<HHH',ico[:6]),(0,1,1))
        self.assertEqual(ico[22:],png)

    def test_root_page_urls_preserve_application_routes(self):
        self.server.store.save('Route checks/Owned','# Owned')
        self.server.store.save('api/Notes','# Notes')
        status,body,headers=self.request('/Route%20checks/Owned')
        self.assertEqual(status,200);self.assertTrue(headers['Content-Type'].startswith('text/html'))
        self.assertIn(b'<title>Homewiki</title>',body)
        self.assertEqual(self.request('/page/Route%20checks/Owned')[0],200)
        self.assertEqual(self.request('/page/api/Notes')[0],200)
        status,body,_=self.request('/Missing')
        self.assertEqual(status,404);self.assertIn(b'<!doctype html>',body)
        status,body,_=self.request('/api/missing')
        self.assertEqual(status,404);self.assertIn('error',json.loads(body))
        self.assertEqual(self.request('/uploads/missing.png')[0],404)
        status,body,_=self.request('/server.py')
        self.assertEqual(status,404);self.assertNotIn(b'import argparse',body)

    def test_private_request_guards(self):
        self.assertEqual(self.request('/api/pages',headers={'Host':'attacker.example'})[0],403)
        self.assertEqual(self.request('/api/pages',headers={'Origin':'https://attacker.example'})[0],403)
        self.assertEqual(self.request('/api/save',{'path':'Bad','content':'bad'})[0],403)
        self.assertEqual(self.request('/api/save',{'path':'Bad','content':'bad'},{'Content-Type':'application/json','X-Homewiki':'1','Origin':'null'})[0],403)
        self.assertEqual(self.request('/server.py')[0],404)
        self.assertEqual(self.request('/api/page?path=../secret')[0],400)
    def test_explicit_lan_host(self):
        self.server.allowed_hosts = app.LOCAL_HOSTS | {'192.0.2.10'}
        try:
            headers={'Host':'192.0.2.10:18080','Origin':'http://192.0.2.10:18080'}
            self.assertEqual(self.request('/api/pages',headers=headers)[0],200)
            self.assertEqual(self.request('/api/pages',headers={'Host':'192.0.2.11:18080'})[0],403)
            headers.update({'Origin':'http://attacker.example','Content-Type':'application/json','X-Homewiki':'1'})
            self.assertEqual(self.request('/api/save',{'path':'Attack','content':'bad'},headers)[0],403)
        finally:
            self.server.allowed_hosts=app.LOCAL_HOSTS
    def test_upload_http_and_private_guards(self):
        headers={'Content-Type':'application/octet-stream','X-Homewiki':'1'}
        status,raw,_=self.request('/api/upload',PNG,headers);self.assertEqual(status,201)
        image=json.loads(raw)
        status,raw,response_headers=self.request(image['url'])
        self.assertEqual((status,raw),(200,PNG))
        self.assertEqual(response_headers['Content-Type'],'image/png')
        self.assertEqual(response_headers['X-Content-Type-Options'],'nosniff')
        self.assertEqual(self.request('/api/upload',PNG,{'Content-Type':'image/png'})[0],403)
        self.assertEqual(self.request('/api/upload',PNG,{**headers,'Origin':'http://attacker.example'})[0],403)
        self.assertEqual(self.request('/api/upload',b'<svg/>',headers)[0],415)
        self.assertEqual(self.request('/uploads/../server.py')[0],404)

    def test_sidebar_order_http(self):
        self.server.store.save('Ordering/Child', '# Child')
        status,raw,_=self.request('/api/order')
        self.assertEqual(status,200)
        revision=json.loads(raw)['revision']
        roots=sorted({page['path'].split('/')[0] for page in self.server.store.listing()},reverse=True)
        body={'parent':'','children':roots,'revision':revision}
        self.assertEqual(self.request('/api/order',body)[0],403)
        headers={'Content-Type':'application/json','X-Homewiki':'1'}
        status,raw,_=self.request('/api/order',body,headers)
        self.assertEqual(status,200)
        self.assertEqual(json.loads(raw)['parents'][''],roots)
        self.assertEqual(self.request('/api/order',body,headers)[0],409)
        self.assertEqual(json.loads(self.request('/api/order')[1]),json.loads(raw))

    def test_history_endpoints_and_private_guards(self):
        first=self.server.store.save('History HTTP/Example','# Original')
        current=self.server.store.save(first['path'],'# Current',first['revision'])
        query='?path=History%20HTTP%2FExample'
        status,raw,_=self.request('/api/history'+query)
        self.assertEqual(status,200)
        result=json.loads(raw);token=result['versions'][0]['id']
        self.assertEqual(result['current']['content'],'# Current')
        status,raw,_=self.request('/api/history-version'+query+'&version='+token)
        self.assertEqual(status,200)
        self.assertEqual(json.loads(raw)['version']['content'],'# Original')
        body={'path':first['path'],'version':token,'revision':current['revision']}
        self.assertEqual(self.request('/api/history-restore',body)[0],403)
        headers={'Content-Type':'application/json','X-Homewiki':'1'}
        self.assertEqual(self.request('/api/history-restore',body,{**headers,'Origin':'https://attacker.example'})[0],403)
        status,raw,_=self.request('/api/history-restore',body,headers)
        self.assertEqual(status,200);self.assertEqual(json.loads(raw)['content'],'# Original')
        self.assertEqual(self.request('/api/history-restore',body,headers)[0],409)
        self.assertEqual(self.request('/api/history?path=../secret')[0],400)
        self.assertEqual(self.request('/api/history?path=Missing%20history')[0],404)
        self.assertEqual(self.request('/api/history-version'+query+'&version=../secret')[0],400)
        self.assertEqual(self.request('/api/history-version'+query+'&version='+'0'*32)[0],404)
        self.assertEqual(self.request('/.history/'+token+'/page.md')[0],400)

    def test_bookmark_http_lifecycle_search_and_request_guards(self):
        headers={'Content-Type':'application/json','X-Homewiki':'1'}
        body={'url':'https://example.com/http-bookmark','title':'HTTP bookmark','description':'A searchable bookmark','tags':['web','reading']}
        self.assertEqual(self.request('/api/bookmarks/save',body)[0],403)
        self.assertEqual(self.request('/api/bookmarks/save',body,{**headers,'Origin':'https://attacker.example'})[0],403)
        status,raw,_=self.request('/api/bookmarks/save',body,headers)
        self.assertEqual(status,200);item=json.loads(raw)
        status,raw,_=self.request('/api/bookmarks?q=searchable&tag=web&domain=example.com')
        self.assertEqual(status,200);self.assertEqual(json.loads(raw)['total'],1)
        self.assertEqual(json.loads(self.request('/api/bookmarks/tags')[1]),[{'name':'reading','count':1},{'name':'web','count':1}])
        self.assertEqual(json.loads(self.request('/api/bookmarks/domains')[1]),[{'name':'example.com','count':1}])
        self.assertEqual(json.loads(self.request('/api/bookmarks/item?id='+item['id'])[1])['description'],body['description'])
        self.assertEqual(self.request('/api/bookmarks/save',body,headers)[0],409)
        status,raw,_=self.request('/api/bookmarks/delete',{'id':item['id'],'revision':item['revision']},headers)
        self.assertEqual(status,200);deleted=json.loads(raw)
        self.assertEqual(json.loads(self.request('/api/bookmarks')[1])['total'],0)
        self.assertEqual(json.loads(self.request('/api/bookmarks?deleted=1')[1])['total'],1)
        self.assertEqual(self.request('/api/bookmarks/restore',{'id':item['id'],'revision':deleted['revision']},headers)[0],200)
        for route in ['/bookmarks','/bookmarks/tags','/bookmarks/domains','/bookmarks.js']:
            self.assertEqual(self.request(route)[0],200)
        self.assertEqual(self.request('/api/bookmarks?limit=1000')[0],400)
        self.assertEqual(self.request('/api/bookmarks/item?id=../secret')[0],400)
        self.assertEqual(self.request('/bookmarks.sqlite3')[0],404)

    def test_bad_payload(self):
        self.assertEqual(self.request('/api/save',[],{'Content-Type':'application/json','X-Homewiki':'1'})[0],400)

if __name__=='__main__':unittest.main()
