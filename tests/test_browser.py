"""Optional browser integration tests. Install playwright and its Chromium to run."""
import importlib.util
import os
import re
from pathlib import Path
import tempfile
import threading
import time
import unittest
try:
    from playwright.sync_api import sync_playwright, expect
except ImportError:
    sync_playwright=None

spec=importlib.util.spec_from_file_location('homewiki_browser',Path(__file__).resolve().parents[1]/'server.py')
app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)

@unittest.skipUnless(sync_playwright, 'Optional Playwright is not installed')
class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw=sync_playwright().start();cls.browser=cls.pw.chromium.launch()
    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.pw.stop()
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        self.store=self.server.store=app.Store(self.temp.name);self.store.seed()
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.context=self.browser.new_context(viewport={'width':1440,'height':1000},color_scheme='light')
        self.page=self.context.new_page();self.errors=[];self.page.on('pageerror',lambda error:self.errors.append(str(error)))
        self.url='http://127.0.0.1:'+str(self.server.server_port);self.page.goto(self.url)
        expect(self.page.locator('#content')).to_contain_text('Welcome to Homewiki')
    def tearDown(self):
        self.context.close();self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()
        self.assertEqual(self.errors,[])
    def test_full_wiki_workflow(self):
        p=self.page
        # Tree and nested example pages.
        p.locator('#tree').get_by_role('link',name='Completed games',exact=False).click()
        expect(p.locator('#page-title')).to_have_text('Completed games')
        # Create nested Markdown page.
        p.locator('#new-page').click();p.locator('#page-path').fill('Projects/My notebook');p.get_by_role('button',name='Create page',exact=True).click()
        expect(p.locator('#page-title')).to_have_text('My notebook')
        p.locator('#edit-page').click();p.locator('#source').fill('# My notebook\n\nA **useful** draft.\n\n')
        p.locator('#link-picker').click();p.get_by_role('searchbox').fill('Completed games');p.locator('.result').filter(has_text='Games/Completed games').first.click()
        self.assertIn('[[Games/Completed games]]',p.locator('#source').input_value())
        # Put the link on its own line, then exercise preview.
        p.locator('#source').fill('# My notebook\n\nA **useful** draft.\n\n[[Games/Completed games]]')
        p.locator('#tab-preview').click();expect(p.locator('#preview strong')).to_have_text('useful');expect(p.locator('#preview a')).to_have_text('Games/Completed games')
        # Reload restores local draft and expected revision.
        p.reload();expect(p.locator('#source')).to_be_visible();self.assertIn('useful',p.locator('#source').input_value())
        expect(p.locator('#notice')).to_contain_text('Recovered')
        p.locator('#save-page').click();expect(p.locator('#editor')).to_be_hidden();expect(p.locator('#content strong')).to_have_text('useful')
        self.assertIn('useful',self.store.read('Projects/My notebook')['content'])
        # Internal links, full-text search, rename/move, soft deletion and restore.
        p.locator('#content a').click();expect(p.locator('#page-title')).to_have_text('Completed games')
        p.locator('#search-open').click();p.get_by_role('searchbox').fill('useful');p.locator('.result').filter(has_text='Projects/My notebook').click();expect(p.locator('#page-title')).to_have_text('My notebook')
        p.locator('#more').click();p.locator('#rename-page').click();p.locator('#page-path').fill('Projects/Renamed');p.get_by_role('button',name='Rename page',exact=True).click();expect(p.locator('#page-title')).to_have_text('Renamed')
        p.locator('#more').click();p.locator('#delete-page').click();p.get_by_role('button',name='Move to trash',exact=True).click();expect(p.locator('#modal')).not_to_be_visible()
        p.locator('#trash-open').click();p.locator('.result').filter(has_text='Projects/Renamed').click();expect(p.locator('#page-title')).to_have_text('Renamed')
        # External changes cannot overwrite an unsaved draft.
        p.locator('#edit-page').click();p.locator('#source').fill('# Local draft\nMy local changes')
        self.store.path('Projects/Renamed').write_text('# External edit\nChanged outside the app')
        p.locator('#save-page').click();expect(p.locator('#notice')).to_contain_text('changed on disk')
        self.assertIn('External edit',self.store.read('Projects/Renamed')['content'])
        p.get_by_role('button',name='Compare versions').click();expect(p.locator('.compare').first).to_contain_text('External edit')
        p.locator('#modal textarea').fill('# Merged\nMy local changes\nChanged outside the app')
        p.get_by_role('button',name='Use merged draft').click();p.locator('#save-page').click();expect(p.locator('#editor')).to_be_hidden()
        self.assertIn('Merged',self.store.read('Projects/Renamed')['content'])
        # Two independent browser tabs get a revision conflict and preserve their drafts.
        other=self.context.new_page();other.goto(self.url+'/#/page/Projects%2FRenamed');expect(other.locator('#page-title')).to_have_text('Renamed')
        p.locator('#edit-page').click();other.locator('#edit-page').click();p.locator('#source').fill('# First tab');other.locator('#source').fill('# Second tab')
        p.locator('#save-page').click();expect(p.locator('#editor')).to_be_hidden();other.locator('#save-page').click();expect(other.locator('#notice')).to_contain_text('changed on disk')
        other.reload();expect(other.locator('#source')).to_have_value('# Second tab');expect(other.locator('#notice')).to_contain_text('file has changed')
        other.close()
    def image_drop(self, target='#main', mime='image/png', name='Screenshot.png'):
        transfer=self.page.evaluate_handle("""({mime,name})=>{
            const canvas=document.createElement('canvas');canvas.width=12;canvas.height=8;
            const context=canvas.getContext('2d');context.fillStyle='#397361';context.fillRect(0,0,12,8);
            const raw=atob(canvas.toDataURL(mime).split(',')[1]);const bytes=Uint8Array.from(raw,c=>c.charCodeAt(0));
            const transfer=new DataTransfer();transfer.items.add(new File([bytes],name,{type:mime}));return transfer;
        }""",{'mime':mime,'name':name})
        self.page.locator(target).dispatch_event('drop',{'dataTransfer':transfer})
        transfer.dispose()
    def test_dragged_image_preview_save_and_restore(self):
        p=self.page
        p.locator('#tree').get_by_role('link',name='Completed games',exact=False).click()
        expect(p.locator('#page-title')).to_have_text('Completed games')
        self.image_drop()
        expect(p.locator('#editor')).to_be_visible()
        expect(p.locator('#source')).to_have_value(re.compile(r'!\[Screenshot\]\(/uploads/[0-9a-f]{32}\.png\)'))
        p.locator('#tab-preview').click();expect(p.locator('#preview img')).to_have_attribute('alt','Screenshot')
        expect(p.locator('#preview img')).to_have_js_property('naturalWidth',12)
        image=p.locator('#preview img').get_attribute('src')
        p.locator('#save-page').click();expect(p.locator('#content img')).to_have_attribute('src',image)
        p.reload();expect(p.locator('#content img')).to_have_attribute('src',image)
        expect(p.locator('#content img')).to_have_js_property('naturalWidth',12)
        self.assertEqual(self.store.read_image(image.split('/')[-1])[1],'image/png')
        # Reader drops start the editor, and multiple formats work.
        self.image_drop(mime='image/jpeg',name='Photo.jpg')
        expect(p.locator('#source')).to_have_value(re.compile(r'/uploads/[0-9a-f]{32}\.jpg'))
        p.locator('#save-page').click();expect(p.locator('#content img')).to_have_count(2)
        # The picker and clipboard share the same upload path.
        p.locator('#edit-page').click()
        data=self.store.read_image(image.split('/')[-1])[0]
        p.locator('#image-files').set_input_files({'name':'Picked.png','mimeType':'image/png','buffer':data})
        expect(p.locator('#source')).to_have_value(re.compile(r'!\[Picked\]\(/uploads/'))
        p.locator('#source').evaluate("""(source,bytes)=>{
            const transfer=new DataTransfer();transfer.items.add(new File([new Uint8Array(bytes)],'Pasted.png',{type:'image/png'}));
            source.dispatchEvent(new ClipboardEvent('paste',{clipboardData:transfer,bubbles:true,cancelable:true}));
        }""",list(data))
        expect(p.locator('#source')).to_have_value(re.compile(r'!\[Pasted\]\(/uploads/'))
        p.locator('#save-page').click();expect(p.locator('#content img')).to_have_count(4)
        # Remote and executable image sources never become image requests.
        html=p.evaluate("inline('![x](https://attacker.example/pixel.png) ![x](javascript:alert) ![x](/uploads/a.svg)')")
        self.assertNotIn('<img',html)
    def test_upload_completion_after_navigation_and_failure(self):
        p=self.page
        held=[]
        p.route('**/api/upload',lambda route:held.append(route))
        self.image_drop()
        expect(p.locator('#save-page')).to_be_disabled()
        p.locator('.header-home').click()
        # Navigate to a different page while the upload is still pending.
        p.locator('#tree').get_by_role('link',name='Games I own',exact=False).click()
        expect(p.locator('#page-title')).to_have_text('Games I own')
        self.assertEqual(len(held),1)
        held.pop().continue_()
        p.locator('.header-home').click()
        expect(p.locator('#source')).to_have_value(re.compile(r'!\[Screenshot\]\(/uploads/'))
        self.assertNotIn('homewiki-upload:',p.locator('#source').input_value())
        original=p.locator('#source').input_value()
        p.unroute('**/api/upload')
        p.route('**/api/upload',lambda route:route.fulfill(status=500,content_type='application/json',body='{"error":"Disk full"}'))
        self.image_drop()
        expect(p.locator('#toast')).to_contain_text('Disk full')
        self.assertIn(original.strip(),p.locator('#source').input_value())
        self.assertNotIn('homewiki-upload:',p.locator('#source').input_value())
        expect(p.locator('#toast')).to_contain_text('Disk full')
        expect(p.locator('#save-page')).to_be_enabled()

    def test_interrupted_image_upload_recovers_text_draft(self):
        p=self.page
        p.locator('#edit-page').click();p.locator('#source').fill('# My preserved text\n\n![Uploading photo…](homewiki-upload:interrupted-123)')
        p.reload();expect(p.locator('#source')).to_have_value('# My preserved text\n\n')
        expect(p.locator('#notice')).to_contain_text('interrupted')
        expect(p.locator('#save-page')).to_be_enabled()

    def test_nested_lists_preview_save_and_reload(self):
        p=self.page
        content="""# Nested lists

- Games
  - Completed
    - **Celeste**
    - [[Home]]
  - Up next
    3. Choose a game
    4. Start playing
- [ ] Reading
  - [x] Chapter one
  - [ ] Chapter two

5. Planned
   - Nested bullet
6. Finished

A paragraph after the lists.
"""
        p.locator('#edit-page').click();p.locator('#source').fill(content);p.locator('#tab-preview').click()
        preview=p.locator('#preview')
        expect(preview.locator(':scope > ul > li')).to_have_count(2)
        expect(preview.locator(':scope > ul > li').first.locator(':scope > ul > li')).to_have_count(2)
        expect(preview.locator('ul ul ul > li')).to_have_count(2)
        expect(preview.locator('ul ul ul strong')).to_have_text('Celeste')
        expect(preview.locator('ul ul ul a')).to_have_attribute('href','/page/Home')
        expect(preview.locator('ul ol')).to_have_attribute('start','3')
        expect(preview.locator(':scope > ol')).to_have_attribute('start','5')
        expect(preview.locator(':scope > ol > li').first.locator('ul > li')).to_have_text('Nested bullet')
        expect(preview.locator('input[type=checkbox]')).to_have_count(3)
        expect(preview.locator('ul ul input[checked]')).to_have_count(1)
        expect(preview.locator(':scope > p').last).to_have_text('A paragraph after the lists.')
        p.locator('#save-page').click();expect(p.locator('#editor')).to_be_hidden()
        p.reload();expect(p.locator('#content ul ul ul > li')).to_have_count(2)
        expect(p.locator('#content ul ol')).to_have_attribute('start','3')
        self.assertEqual(self.store.read('Home')['content'],content)
    def test_list_indentation_continuations_and_code(self):
        p=self.page
        cases=[
            ('- Parent\n    - Child\n        - Grandchild\n- Sibling','ul ul ul > li','Grandchild'),
            ('- Parent\n\t- Child\n\t\t- Grandchild\n- Sibling','ul ul ul > li','Grandchild'),
            ('1) Parent\n  - Child\n    7) Grandchild\n2) Sibling','ol ul ol[start="7"] > li','Grandchild'),
        ]
        for source,selector,text in cases:
            with self.subTest(source=source):
                p.locator('#content').evaluate('(node,source)=>node.innerHTML=markdown(source)',source)
                expect(p.locator('#content').locator(selector)).to_have_text(text)
                expect(p.locator('#content > ul > li, #content > ol > li')).to_have_count(2)
        p.locator('#content').evaluate('(node,source)=>node.innerHTML=markdown(source)','- Parent\n  wrapped text\n\n  Second paragraph\n\n  ```\n  - code only <script>\n  ```\n- Sibling\n\nOutside paragraph')
        expect(p.locator('#content > ul > li')).to_have_count(2)
        expect(p.locator('#content > ul > li').first.locator(':scope > p')).to_have_count(2)
        expect(p.locator('#content li pre code')).to_have_text('- code only <script>')
        expect(p.locator('#content script')).to_have_count(0)
        expect(p.locator('#content > p')).to_have_text('Outside paragraph')
        p.locator('#content').evaluate('(node,source)=>node.innerHTML=markdown(source)','- One\n\n- Two\n\n1. Three\n2. Four\n\n---\n\nAfter')
        expect(p.locator('#content > ul > li')).to_have_count(2)
        expect(p.locator('#content > ol > li')).to_have_count(2)
        expect(p.locator('#content > hr')).to_have_count(1)
        expect(p.locator('#content > p')).to_have_text('After')

    def test_recent_edits_and_footer(self):
        p=self.page
        now=time.time()
        for file in self.store.pages.rglob('*.md'):os.utime(file,(now-7200,now-7200))
        self.store.save('Recent test/Old','# Older note')
        self.store.save('Recent test/New','# Newer note')
        os.utime(self.store.path('Recent test/Old'),(now-3600,now-3600))
        os.utime(self.store.path('Recent test/New'),(now-1800,now-1800))
        p.locator('#recently-edited').click()
        expect(p).to_have_url(self.url+'/recently-edited')
        expect(p.locator('#page-title')).to_have_text('Recently edited')
        expect(p.locator('.recent-page strong').first).to_have_text('New')
        expect(p.locator('.recent-page strong').nth(1)).to_have_text('Old')
        expect(p.locator('#more')).to_be_hidden()
        p.reload();expect(p.locator('.recent-page strong').first).to_have_text('New')
        p.locator('.recent-page strong').filter(has_text='Old').click()
        expect(p.locator('#last-edited')).to_have_attribute('datetime',self.store.read('Recent test/Old')['modified'])
        expect(p.locator('#last-edited')).to_contain_text('Last edited')
        self.assertNotIn('A little knowledge',p.locator('#page-footer').inner_text())
        p.locator('#edit-page').click();p.locator('#source').fill('# Updated older note')
        p.locator('#recently-edited').click();p.locator('.recent-page strong').filter(has_text='Old').click()
        expect(p.locator('#source')).to_have_value('# Updated older note')
        p.locator('#save-page').click();expect(p.locator('#editor')).to_be_hidden()
        expect(p.locator('#last-edited')).to_have_attribute('datetime',self.store.read('Recent test/Old')['modified'])
        p.locator('#recently-edited').click();expect(p.locator('.recent-page strong').first).to_have_text('Old')
        # External edits also change the ordering; deleted pages disappear.
        self.store.path('Recent test/New').write_text('# External change')
        p.reload();expect(p.locator('.recent-page strong').first).to_have_text('New')
        page=self.store.read('Recent test/New');self.store.delete(page['path'],page['revision'])
        p.reload();expect(p.locator('.recent-page strong').first).to_have_text('Old')
        expect(p.locator('.recent-page')).to_have_count(6)

    def test_appearance_markdown_and_mobile(self):
        p=self.page
        # Escaping and links: raw HTML and script URLs must never become active HTML.
        rendered=p.evaluate("markdown('# Title\\n\\n<script>alert(1)</script>\\n\\n[x](javascript:alert)\\n\\n| A | B |\\n| --- | --- |\\n| a | b |\\n\\n- [x] Done\\n\\n```\\n<b>code</b>\\n```')")
        self.assertNotIn('<script>',rendered);self.assertNotIn('href="javascript:',rendered);self.assertIn('<table>',rendered);self.assertIn('type="checkbox"',rendered);self.assertIn('&lt;b&gt;code',rendered)
        self.assertEqual(p.evaluate('document.documentElement.scrollWidth <= innerWidth'),True)
        if os.getenv('HOMEWIKI_SCREENSHOTS'):
            target=Path(os.environ['HOMEWIKI_SCREENSHOTS']);target.mkdir(parents=True,exist_ok=True);p.screenshot(path=str(target/'homewiki-light.png'),full_page=True)
        p.locator('#theme-toggle').click();expect(p.locator('html')).to_have_attribute('data-theme','dark');p.reload();expect(p.locator('html')).to_have_attribute('data-theme','dark')
        if os.getenv('HOMEWIKI_SCREENSHOTS'):p.screenshot(path=str(target/'homewiki-dark.png'),full_page=True)
        p.locator('#collapse').click();expect(p.locator('#sidebar')).not_to_be_visible();p.locator('#expand').click();expect(p.locator('#sidebar')).to_be_visible()
        p.set_viewport_size({'width':390,'height':844});p.reload();expect(p.locator('#sidebar')).not_to_be_visible();p.locator('#expand').click();expect(p.locator('#sidebar')).to_be_visible();p.locator('#tree').get_by_role('link',name='Games I own',exact=False).click();expect(p.locator('#page-title')).to_have_text('Games I own');expect(p.locator('#sidebar')).not_to_be_visible()
        self.assertEqual(p.evaluate('document.documentElement.scrollWidth <= innerWidth'),True)
        p.locator('#edit-page').click();p.locator('#source').fill('# Mobile draft');p.locator('#cancel-edit').click();p.get_by_role('button',name='Discard draft').click();p.locator('#modal').get_by_role('button',name='Discard draft',exact=True).click();expect(p.locator('#notice')).to_be_hidden()

if __name__=='__main__':unittest.main()
