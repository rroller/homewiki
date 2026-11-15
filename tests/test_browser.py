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
        p.locator('#more').click();p.locator('#rename-page').click();p.locator('#page-path').fill('Projects/Renamed');p.get_by_role('button',name='Rename page',exact=True).click();expect(p.locator('#page-title')).to_have_text('My notebook');expect(p.locator('#breadcrumb')).to_have_text('Projects/Renamed')
        p.locator('#more').click();p.locator('#delete-page').click();p.get_by_role('button',name='Move to trash',exact=True).click();expect(p.locator('#modal')).not_to_be_visible()
        p.locator('#trash-open').click();p.locator('.result').filter(has_text='Projects/Renamed').click();expect(p.locator('#page-title')).to_have_text('My notebook');expect(p.locator('#breadcrumb')).to_have_text('Projects/Renamed')
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
        other=self.context.new_page();other.goto(self.url+'/#/page/Projects%2FRenamed');expect(other.locator('#page-title')).to_have_text('Merged')
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

    def test_web_links_preview_save_and_reload(self):
        p=self.page
        content='# Links\n\nexample.com\n\nwww.example.com\n\nhttps://www.example.com\n\n[https://www.example.com](https://www.example.com)\n\n[Named link](https://www.example.com/path?q=one&x=two)\n\n<https://www.example.com/angle>\n'
        expected=['https://example.com','https://www.example.com','https://www.example.com','https://www.example.com','https://www.example.com/path?q=one&x=two','https://www.example.com/angle']
        p.locator('#edit-page').click();p.locator('#source').fill(content);p.locator('#tab-preview').click()
        expect(p.locator('#preview a')).to_have_count(6)
        self.assertEqual(p.locator('#preview a').evaluate_all('links=>links.map(a=>a.getAttribute("href"))'),expected)
        expect(p.locator('#preview a a')).to_have_count(0)
        for link in p.locator('#preview a').all():
            expect(link).to_have_attribute('target','_blank')
            expect(link).to_have_attribute('rel','noopener noreferrer')
        p.locator('#save-page').click();expect(p.locator('#editor')).to_be_hidden()
        p.reload();expect(p.locator('#content a')).to_have_count(6)
        self.assertEqual(p.locator('#content a').evaluate_all('links=>links.map(a=>a.getAttribute("href"))'),expected)
        self.assertEqual(self.store.read('Home')['content'],content)
    def test_web_link_boundaries_code_and_escaping(self):
        p=self.page
        cases=[
            ('Visit example.com.', ['https://example.com']),
            ('"example.com" and “https://example.com”', ['https://example.com','https://example.com']),
            ('See (https://example.com/path_(part)).', ['https://example.com/path_(part)']),
            ('Try https://example.com/path?q=1&x=2, then example.org/docs#heading.', ['https://example.com/path?q=1&x=2','https://example.org/docs#heading']),
            ('**https://example.com/bold**', ['https://example.com/bold']),
            ('HTTP://example.com:8080 and www.example.org/help', ['HTTP://example.com:8080','https://www.example.org/help']),
            ('[example.com](https://www.example.com) [[Games]]', ['https://www.example.com','/page/Games']),
            ('\u0060https://example.com\u0060 name@example.com notes.md config.json /docs/example.com', []),
            ('javascript:example.com ftp://example.com https://example.com:99999', []),
            ('![example.com](https://example.com/tracker.png)', []),
        ]
        for source,expected in cases:
            with self.subTest(source=source):
                p.locator('#content').evaluate('(node,source)=>node.innerHTML=markdown(source)',source)
                self.assertEqual(p.locator('#content a').evaluate_all('links=>links.map(a=>a.getAttribute("href"))'),expected)
                expect(p.locator('#content a a')).to_have_count(0)
        source='\u0060\u0060\u0060\nhttps://example.com\nexample.org\n\u0060\u0060\u0060\n\nhttps://example.com/?q=<script>alert(1)</script>'
        p.locator('#content').evaluate('(node,source)=>node.innerHTML=markdown(source)',source)
        expect(p.locator('#content pre a')).to_have_count(0)
        expect(p.locator('#content script')).to_have_count(0)
        expect(p.locator('#content [onerror], #content [onclick]')).to_have_count(0)
        expect(p.locator('#content a')).to_have_count(1)
    def test_sidebar_brand_returns_home_and_preserves_draft(self):
        p=self.page
        p.locator('#tree').get_by_role('link',name='Games I own',exact=False).click()
        expect(p.locator('#page-title')).to_have_text('Games I own')
        p.locator('#edit-page').click();p.locator('#source').fill('# A draft to keep')
        brand=p.locator('#brand-home');expect(brand).to_have_attribute('href','/page/Home')
        brand.focus();p.keyboard.press('Enter')
        expect(p).to_have_url(self.url+'/page/Home');expect(p.locator('#page-title')).to_have_text('A home for your thoughts')
        p.go_back();expect(p.locator('#source')).to_have_value('# A draft to keep')
        p.locator('#brand-home').click();expect(p.locator('#page-title')).to_have_text('A home for your thoughts')

    def test_incomplete_table_rows_render_empty_cells(self):
        p=self.page
        content="""# Games I own

| Game | System | Completed |
| --- | --- | --- |
| Team Fortress 2 | PC
Portal | PC
| Portal 2 |
| Bloons TD 6 | PC | |
| Crysis | PC | Yes | extra |

After the table.
"""
        expected=[['Team Fortress 2','PC',''],['Portal','PC',''],['Portal 2','',''],['Bloons TD 6','PC',''],['Crysis','PC','Yes']]
        p.locator('#edit-page').click();p.locator('#source').fill(content);p.locator('#tab-preview').click()
        expect(p.locator('#preview th')).to_have_count(3)
        expect(p.locator('#preview tbody tr')).to_have_count(5)
        actual=p.locator('#preview tbody tr').evaluate_all('rows=>rows.map(row=>Array.from(row.cells,cell=>cell.textContent))')
        self.assertEqual(actual,expected)
        expect(p.locator('#preview > p').last).to_have_text('After the table.')
        p.locator('#save-page').click();expect(p.locator('#editor')).to_be_hidden()
        p.reload();expect(p.locator('#content tbody tr')).to_have_count(5)
        self.assertEqual(p.locator('#content tbody tr').evaluate_all('rows=>rows.map(row=>Array.from(row.cells,cell=>cell.textContent))'),expected)
        self.assertEqual(self.store.read('Home')['content'],content)

    def test_table_sorting_in_preview_and_reader(self):
        p=self.page
        content="""# Sortable games

| Game | Hours | Completed |
| --- | --- | --- |
| Game 10 | 10 | Yes |
| Game 2 | 2 |
| Alpha | 1.5 | No |
| Beta | 1.10 | Yes |
| Gamma | -2 | No |
| Another 2 | 2 | Yes |
| Long game | 1,000 | Yes |
| Empty |

| Other | Value |
| --- | --- |
| Unchanged | 7 |
"""
        p.locator('#edit-page').click();p.locator('#source').fill(content);p.locator('#tab-preview').click()
        table=p.locator('#preview table').first
        hours=table.get_by_role('button',name='Hours',exact=True)
        hours.click()
        self.assertEqual(table.locator('tbody tr').evaluate_all('rows=>rows.map(r=>r.cells[1].textContent)'),['-2','1.10','1.5','2','2','10','1,000',''])
        self.assertEqual(table.locator('tbody tr').evaluate_all('rows=>rows.filter(r=>r.cells[1].textContent==="2").map(r=>r.cells[0].textContent)'),['Game 2','Another 2'])
        expect(table.locator('th[aria-sort]')).to_have_attribute('aria-sort','ascending')
        hours.focus();p.keyboard.press('Enter')
        self.assertEqual(table.locator('tbody tr').evaluate_all('rows=>rows.map(r=>r.cells[1].textContent)'),['1,000','10','2','2','1.5','1.10','-2',''])
        expect(table.locator('th[aria-sort]')).to_have_attribute('aria-sort','descending')
        p.keyboard.press('Space')
        expect(table.locator('th[aria-sort]')).to_have_attribute('aria-sort','ascending')
        table.get_by_role('button',name='Game',exact=True).click()
        games=table.locator('tbody tr').evaluate_all('rows=>rows.map(r=>r.cells[0].textContent)')
        self.assertLess(games.index('Game 2'),games.index('Game 10'))
        expect(table.locator('th[aria-sort]')).to_have_count(1)
        completed=table.get_by_role('button',name='Completed',exact=True)
        completed.click()
        self.assertEqual(table.locator('tbody tr').evaluate_all('rows=>rows.map(r=>r.cells[2].textContent)'),['No','No','Yes','Yes','Yes','Yes','',''])
        completed.click()
        self.assertEqual(table.locator('tbody tr').evaluate_all('rows=>rows.map(r=>r.cells[2].textContent)'),['Yes','Yes','Yes','Yes','No','No','',''])
        expect(p.locator('#preview table').nth(1).locator('tbody tr')).to_have_text('Unchanged7')
        self.assertEqual(p.locator('#source').input_value(),content)
        p.locator('#save-page').click();expect(p.locator('#editor')).to_be_hidden()
        self.assertEqual(self.store.read('Home')['content'],content)
        p.reload()
        reader=p.locator('#content table').first
        expect(reader.locator('tbody tr').first.locator('td').first).to_have_text('Game 10')
        revision=self.store.read('Home')['revision']
        reader.get_by_role('button',name='Hours',exact=True).click()
        expect(reader.locator('tbody tr').first.locator('td').first).to_have_text('Gamma')
        self.assertEqual(self.store.read('Home')['revision'],revision)
    def test_table_headers_stay_on_one_line_on_mobile(self):
        p=self.page
        source='| Game | System | **Completed** | Additional details, notes, and comments |\n| --- | --- | --- | --- |\n| Portal | PC |\n'
        p.locator('#content').evaluate('(node,source)=>node.innerHTML=markdown(source)',source)
        p.locator('#collapse').click();p.set_viewport_size({'width':390,'height':844})
        button=p.locator('#content').get_by_role('button',name='Completed',exact=True)
        expect(button).to_have_css('white-space','nowrap')
        expect(p.locator('#content th').nth(2)).to_have_css('overflow-wrap','normal')
        label=button.locator('span').first
        self.assertLessEqual(label.bounding_box()['height'],float(label.evaluate('node=>parseFloat(getComputedStyle(node).lineHeight)'))+1)
        self.assertTrue(p.locator('#content table').evaluate('node=>node.scrollWidth>node.clientWidth'))
        self.assertTrue(p.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        # Formatting remains, but links inside sortable headings cannot create nested controls.
        p.locator('#content').evaluate('(node,source)=>node.innerHTML=markdown(source)','| [Completed](https://example.com) | Empty |\n| --- | --- |\n')
        expect(p.locator('#content th button a')).to_have_count(0)
        p.locator('#content').get_by_role('button',name='Completed',exact=True).click()
        expect(p.locator('#content th').first).to_have_attribute('aria-sort','ascending')

    def test_leading_markdown_heading_controls_visible_title(self):
        p=self.page
        p.locator('#edit-page').click();p.locator('#source').fill('# My **dashboard**\n\n## First section\n\nA note.')
        p.locator('#tab-preview').click();expect(p.locator('#preview h1')).to_have_text('My dashboard')
        p.locator('#save-page').click();expect(p.locator('#editor')).to_be_hidden()
        expect(p.locator('#page-title')).to_have_text('My dashboard')
        expect(p.locator('#page-title strong')).to_have_text('dashboard')
        expect(p).to_have_title('My dashboard · Homewiki')
        expect(p.locator('#content h1')).to_have_count(0)
        expect(p.locator('#content h2')).to_have_text('First section')
        expect(p.locator('#breadcrumb')).to_have_text('Home')
        expect(p.locator('#tree a[aria-current=page]')).to_have_text('Home')
        self.assertEqual(self.store.read('Home')['content'],'# My **dashboard**\n\n## First section\n\nA note.')
        p.reload();expect(p.locator('#page-title')).to_have_text('My dashboard')
        # Changing the level makes it a content heading, and removing it falls back to the filename.
        p.locator('#edit-page').click();p.locator('#source').fill('## A section instead\n\nA note.');p.locator('#save-page').click()
        expect(p.locator('#page-title')).to_have_text('Home');expect(p.locator('#content h2')).to_have_text('A section instead')
        p.locator('#edit-page').click();p.locator('#source').fill('An introduction.\n\n# A later heading');p.locator('#save-page').click()
        expect(p.locator('#page-title')).to_have_text('Home');expect(p.locator('#content h1')).to_have_text('A later heading')
        # Leading blank lines are fine, and heading text remains escaped.
        p.locator('#edit-page').click();p.locator('#source').fill('\n\n# <script>unsafe</script>\n\nBody');p.locator('#save-page').click()
        expect(p.locator('#page-title')).to_have_text('<script>unsafe</script>')
        expect(p.locator('#page-title script')).to_have_count(0)
        p.locator('#edit-page').click();p.locator('#source').fill('#   \n\nBody');p.locator('#save-page').click()
        expect(p.locator('#page-title')).to_have_text('Home')

    def test_favicon_and_compact_sidebar_toolbar(self):
        p=self.page
        expect(p.locator('link[rel=icon][href="/favicon.png"]')).to_have_count(1)
        expect(p.locator('link[rel=icon][href="/favicon.ico"]')).to_have_count(1)
        p.locator('body').evaluate("body=>{const image=document.createElement('img');image.id='favicon-check';image.hidden=true;image.src='/favicon.png';body.append(image);}")
        expect(p.locator('#favicon-check')).to_have_js_property('naturalWidth',128)
        p.locator('#favicon-check').evaluate('node=>node.remove()')
        toolbar=p.locator('.sidebar-tools')
        expect(toolbar.get_by_role('button',name='New page',exact=True)).to_be_visible()
        expect(toolbar.get_by_role('link',name='Recently edited',exact=True)).to_be_visible()
        self.assertEqual(p.locator('#new-page').inner_text(),'')
        self.assertEqual(p.locator('#recently-edited').inner_text(),'')
        p.locator('#new-page').click();expect(p.locator('#modal-title')).to_have_text('Create a page');p.locator('#modal-close').click()
        p.locator('#recently-edited').click();expect(p.locator('#page-title')).to_have_text('Recently edited')
        expect(p.locator('#recently-edited')).to_have_attribute('aria-current','page')
        for width in [1440,1000,800,390]:
            p.set_viewport_size({'width':width,'height':900})
            p.reload()
            if width<721:p.locator('#expand').click()
            sidebar=p.locator('#sidebar').bounding_box();brand=p.locator('.brand').bounding_box()
            buttons=toolbar.locator('button,a').all()
            self.assertEqual(len(buttons),4)
            previous=None
            for button in buttons:
                box=button.bounding_box()
                self.assertGreaterEqual(box['x'],brand['x'])
                self.assertLessEqual(box['x']+box['width'],sidebar['x']+sidebar['width'])
                if previous:self.assertGreaterEqual(box['x'],previous['x']+previous['width'])
                previous=box
            self.assertTrue(p.evaluate('document.documentElement.scrollWidth<=innerWidth'))
    def test_only_top_level_page_groups_collapse(self):
        p=self.page
        self.store.save('Home/Unraid','# Unraid')
        self.store.save('Home/Unraid/DDNS','# DDNS')
        p.evaluate("localStorage.setItem('homewiki:folder:Home/Unraid','closed')")
        p.reload()
        root=p.locator('#tree').get_by_role('button',name='Toggle Home',exact=True)
        expect(root).to_have_attribute('aria-expanded','true')
        expect(p.locator('#tree').get_by_role('button',name='Toggle Unraid',exact=True)).to_have_count(0)
        child=p.locator('#tree').get_by_role('link',name='DDNS',exact=False)
        expect(child).to_be_visible()
        root.click();expect(child).not_to_be_visible()
        p.reload();expect(root).to_have_attribute('aria-expanded','false');expect(child).not_to_be_visible()
        root.click();expect(child).to_be_visible()
        child.click();expect(p.locator('#page-title')).to_have_text('DDNS')
        # Nested items remain readable inside a modestly denser tree.
        visible=p.locator('#tree .tree-row').filter(visible=True)
        boxes=[row.bounding_box() for row in visible.all()]
        for first,second in zip(boxes,boxes[1:]):
            self.assertGreaterEqual(second['y'],first['y']+first['height'])
            self.assertLessEqual(second['y']-first['y'],40)

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
