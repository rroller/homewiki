import concurrent.futures
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError

spec = importlib.util.spec_from_file_location('homewiki', Path(__file__).resolve().parents[1] / 'server.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = app.Store(self.temp.name)
    def tearDown(self):
        self.temp.cleanup()
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
        req=Request(self.url+path,data=json.dumps(body).encode() if body is not None else None,headers=headers or {})
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
    def test_bad_payload(self):
        self.assertEqual(self.request('/api/save',[],{'Content-Type':'application/json','X-Homewiki':'1'})[0],400)

if __name__=='__main__':unittest.main()
