import concurrent.futures
import base64
import importlib.util
import json
import struct
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError

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

    def test_bad_payload(self):
        self.assertEqual(self.request('/api/save',[],{'Content-Type':'application/json','X-Homewiki':'1'})[0],400)

if __name__=='__main__':unittest.main()
