"""Optional browser integration tests. Install playwright and its Chromium to run."""
import importlib.util
import sys
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

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
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
        self.page=self.context.new_page()
        self.page.route('**/api/bookmarks/title',lambda route:route.fulfill(json={'title':'','url':route.request.post_data_json['url']}))
        self.errors=[];self.page.on('pageerror',lambda error:self.errors.append(str(error)))
        self.url='http://127.0.0.1:'+str(self.server.server_port);self.page.goto(self.url)
        expect(self.page.locator('#content')).to_contain_text('Welcome to Homewiki')
    def tearDown(self):
        self.context.close();self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()
        self.assertEqual(self.errors,[])
    def test_drag_sidebar_order_and_reload(self):
        p=self.page
        rows=p.locator('.tree-row[data-parent=""]')
        def paths(locator):return locator.evaluate_all('(rows)=>rows.map(row=>row.dataset.path)')
        original={item['path']:self.store.read(item['path']) for item in self.store.listing()}
        self.assertEqual(paths(rows),['Games','Home','Reading list'])
        with p.expect_response(lambda response:response.url.endswith('/api/order') and response.request.method=='POST') as response:
            p.locator('.tree-row[data-path="Reading list"]').drag_to(p.locator('.tree-row[data-path="Games"]'),target_position={'x':60,'y':2})
        self.assertEqual(response.value.status,200)
        expect(p.locator('#toast')).to_have_text('Page order saved')
        self.assertEqual(paths(rows),['Reading list','Games','Home'])
        p.reload();expect(rows.first).to_have_attribute('data-path','Reading list')
        self.assertEqual(paths(rows),['Reading list','Games','Home'])
        children=p.locator('.tree-row[data-parent="Games"]')
        with p.expect_response(lambda response:response.url.endswith('/api/order') and response.request.method=='POST') as response:
            p.locator('.tree-row[data-path="Games/Games I own"]').drag_to(p.locator('.tree-row[data-path="Games/Completed games"]'),target_position={'x':80,'y':2})
        self.assertEqual(response.value.status,200)
        expect(children.first).to_have_attribute('data-path','Games/Games I own')
        p.reload();expect(children.first).to_have_attribute('data-path','Games/Games I own')
        revision=self.store.page_order()['revision']
        # Moving over a different parent must not reparent, navigate or save.
        p.locator('.tree-row[data-path="Home"]').drag_to(p.locator('.tree-row[data-path="Games/Games I own"]'))
        self.assertEqual(self.store.page_order()['revision'],revision)
        self.assertEqual(p.url,self.url+'/')
        self.assertEqual({item['path']:self.store.read(item['path']) for item in self.store.listing()},original)
        row_height=rows.first.bounding_box()['height']
        self.assertLessEqual(row_height,34)

    def test_sidebar_keyboard_order_and_conflict(self):
        p=self.page
        home=p.locator('.tree-row[data-path="Home"] a')
        with p.expect_response(lambda response:response.url.endswith('/api/order') and response.request.method=='POST') as response:
            home.focus();home.press('Alt+ArrowUp')
        self.assertEqual(response.value.status,200)
        expect(home).to_be_focused()
        expect(p.locator('.tree-row[data-parent=""]').first).to_have_attribute('data-path','Home')
        p.reload();expect(p.locator('.tree-row[data-parent=""]').first).to_have_attribute('data-path','Home')
        # Another client saves while this browser has an older order revision.
        self.store.reorder_pages('', ['Reading list','Games','Home'], self.store.page_order()['revision'])
        with p.expect_response(lambda response:response.url.endswith('/api/order') and response.request.method=='POST') as response:
            home.focus();home.press('Alt+ArrowDown')
        self.assertEqual(response.value.status,409)
        expect(p.locator('#toast')).to_contain_text('changed in another tab')
        expect(p.locator('.tree-row[data-parent=""]').first).to_have_attribute('data-path','Reading list')
        self.assertEqual(self.store.page_order()['parents'][''],['Reading list','Games','Home'])

    def test_page_menu_creates_subpages_and_protects_existing_pages(self):
        p=self.page
        p.locator('#tree').get_by_role('link',name='Games',exact=True).click()
        expect(p.locator('#breadcrumb')).to_have_text('Games')
        before=self.store.read('Games')
        count=len(self.store.listing())
        # A collapsed parent opens after creation so its new child is visible.
        p.get_by_role('button',name='Toggle Games',exact=True).click()
        p.locator('#more').click();p.get_by_role('button',name='New subpage',exact=True).click()
        expect(p.locator('#actions')).to_be_hidden()
        expect(p.locator('#more')).to_have_attribute('aria-expanded','false')
        expect(p.locator('#modal-title')).to_have_text('Create a subpage')
        expect(p.locator('#modal-body')).to_contain_text('Create under “Games”')
        expect(p.get_by_label('Subpage name')).to_have_value('')
        p.get_by_label('Subpage name').fill('Cancelled')
        p.get_by_role('button',name='Cancel',exact=True).click()
        self.assertEqual(len(self.store.listing()),count)
        p.locator('#more').click();p.locator('#new-subpage').click()
        p.get_by_label('Subpage name').fill('Wish list')
        p.get_by_role('button',name='Create page',exact=True).click()
        expect(p.locator('#page-title')).to_have_text('Wish list')
        self.assertEqual(p.url,self.url+'/Games/Wish%20list')
        expect(p.locator('.tree-row[data-path="Games/Wish list"]')).to_be_visible()
        self.assertEqual(self.store.read('Games'),before)
        self.assertEqual(self.store.read('Games/Wish list')['content'],'# Wish list\n\n')
        # Nested parents use their complete path.
        p.locator('#more').click();p.locator('#new-subpage').click()
        expect(p.locator('#modal-body')).to_contain_text('Create under “Games/Wish list”')
        p.get_by_label('Subpage name').fill('Co-op')
        p.get_by_role('button',name='Create page',exact=True).click()
        expect(p.locator('#breadcrumb')).to_have_text('Games/Wish list/Co-op')
        self.assertEqual(self.store.read('Games/Wish list/Co-op')['content'],'# Co-op\n\n')
        # Existing names cannot be overwritten, and traversal cannot escape the parent.
        p.locator('#tree').get_by_role('link',name='Games',exact=True).click()
        expect(p.locator('#breadcrumb')).to_have_text('Games')
        existing=self.store.read('Games/Completed games')
        p.locator('#more').click();p.locator('#new-subpage').click()
        p.get_by_label('Subpage name').fill('Completed games')
        p.get_by_role('button',name='Create page',exact=True).click()
        expect(p.locator('#modal .error-text')).to_be_visible()
        self.assertEqual(self.store.read('Games/Completed games'),existing)
        p.get_by_label('Subpage name').fill('../Outside')
        p.get_by_role('button',name='Create page',exact=True).click()
        expect(p.locator('#modal .error-text')).to_contain_text('Use names separated by /')
        self.assertFalse(self.store.path('Outside').exists())

    def test_subpage_creation_preserves_parent_draft_and_uses_file_path(self):
        p=self.page
        original=self.store.read('Home')
        p.locator('#edit-page').click()
        p.locator('#source').fill('# My new title\n\nAn unsaved parent draft.')
        p.locator('#more').click();p.locator('#new-subpage').click()
        expect(p.locator('#modal-body')).to_contain_text('Create under “Home”')
        p.get_by_label('Subpage name').fill('Scratch notes')
        p.get_by_role('button',name='Create page',exact=True).click()
        expect(p.locator('#page-title')).to_have_text('Scratch notes')
        self.assertEqual(self.store.read('Home'),original)
        p.locator('#breadcrumb').get_by_role('link',name='Home',exact=True).click()
        expect(p.locator('#source')).to_have_value('# My new title\n\nAn unsaved parent draft.')
        expect(p.locator('#notice')).to_contain_text('Recovered')
        self.assertEqual(self.store.read('Home'),original)

    def open_history(self):
        self.page.locator('#more').click()
        self.page.get_by_role('button',name='Page history',exact=True).click()
        expect(self.page.locator('#modal-title')).to_have_text('Page history')

    def test_history_compare_and_restore_workflow(self):
        p=self.page
        first=self.store.save('History/Nested','# First\nOriginal text\n')
        second=self.store.save(first['path'],'# Second\n<script>window.historyInjected=true</script>\n',first['revision'])
        latest=self.store.save(first['path'],'# Third\nCurrent text\n',second['revision'])
        versions=self.store.history(first['path'])['versions']
        p.goto(self.url+'/History/Nested');expect(p.locator('#page-title')).to_have_text('Third')
        self.open_history()
        expect(p.locator('#history-version option')).to_have_count(2)
        expect(p.locator('#history-diff .diff-removed')).to_contain_text(['-# Second','-<script>'])
        expect(p.locator('#history-diff .diff-added')).to_contain_text(['+# Third','+Current text'])
        self.assertFalse(p.evaluate('Boolean(window.historyInjected)'))
        self.assertEqual(p.locator('#modal script').count(),0)
        p.get_by_role('tab',name='Saved Markdown',exact=True).click()
        expect(p.locator('#history-content')).to_have_text(second['content'])
        p.get_by_role('tab',name='Changes',exact=True).click()
        original=next(version for version in versions if version['revision']==first['revision'])
        p.locator('#history-version').select_option(original['id'])
        expect(p.locator('#history-diff .diff-removed')).to_contain_text(['-# First','-Original text'])
        p.locator('#history-restore').click()
        expect(p.locator('#modal-title')).to_have_text('Restore this version?')
        p.get_by_role('button',name='Cancel',exact=True).click()
        expect(p.locator('#modal-title')).to_have_text('Page history')
        self.assertEqual(self.store.read(first['path']),latest)
        p.locator('#history-version').select_option(original['id'])
        expect(p.locator('#history-diff .diff-removed').first).to_have_text('-# First')
        p.locator('#history-restore').click();p.locator('#confirm-history-restore').click()
        expect(p.locator('#modal')).not_to_be_visible()
        expect(p.locator('#page-title')).to_have_text('First')
        self.assertEqual(self.store.read(first['path'])['content'],first['content'])
        self.assertEqual(len(self.store.history(first['path'])['versions']),3)
        p.reload();expect(p.locator('#page-title')).to_have_text('First')
        self.open_history()
        expect(p.locator('#history-version option')).to_have_count(3)
        expect(p.locator('#history-content')).to_have_text(latest['content'])

    def test_history_empty_state_and_parent_draft_protection(self):
        p=self.page
        self.open_history()
        expect(p.locator('#modal')).to_contain_text('No earlier versions yet')
        p.locator('#modal-close').click()
        before=self.store.read('Home')
        saved=self.store.save('Home','# Saved version',before['revision'])
        p.reload();expect(p.locator('#page-title')).to_have_text('Saved version')
        p.locator('#edit-page').click();p.locator('#source').fill('# Unsaved draft')
        self.open_history();expect(p.locator('#history-restore')).to_be_enabled()
        p.locator('#history-restore').click()
        expect(p.locator('#modal .error-text')).to_contain_text('Save or discard your draft')
        expect(p.locator('#modal-title')).to_have_text('Page history')
        p.locator('#modal-close').click()
        expect(p.locator('#source')).to_have_value('# Unsaved draft')
        self.assertEqual(self.store.read('Home'),saved)
        p.reload();expect(p.locator('#source')).to_have_value('# Unsaved draft')

    def test_history_restore_detects_newer_edits(self):
        p=self.page
        before=self.store.read('Home')
        saved=self.store.save('Home','# New saved version',before['revision'])
        p.reload();expect(p.locator('#page-title')).to_have_text('New saved version')
        self.open_history();expect(p.locator('#history-restore')).to_be_enabled()
        p.locator('#history-restore').click()
        expect(p.locator('#confirm-history-restore')).to_be_visible()
        external=self.store.save('Home','# Other browser edit',saved['revision'])
        p.locator('#confirm-history-restore').click()
        expect(p.locator('#modal .error-text')).to_contain_text('changed on disk')
        self.assertEqual(self.store.read('Home'),external)
        expect(p.locator('#confirm-history-restore')).to_be_enabled()
        p.get_by_role('button',name='Cancel',exact=True).click()
        expect(p.locator('#history-version option')).to_have_count(2)
        expect(p.locator('#history-content')).to_have_text(saved['content'])

    def add_bookmark(self, url, title, tags=''):
        p=self.page
        p.locator('#add-bookmark').click()
        p.get_by_label('Save page text for search',exact=True).uncheck()
        p.get_by_label('Web address',exact=True).fill(url)
        p.get_by_label('Title (optional)',exact=True).fill(title)
        expect(p.get_by_label('Short description',exact=True)).to_have_count(0)
        p.get_by_label('Tags',exact=True).fill(tags)
        p.get_by_role('button',name='Save bookmark',exact=True).click()
        expect(p.locator('#modal')).not_to_be_visible()
        expect(p.locator('.bookmark-title').filter(has_text=title)).to_be_visible()

    def test_bookmark_library_crud_tags_domains_search_and_recovery(self):
        p=self.page
        p.locator('#bookmarks-open').click()
        expect(p.locator('#page-title')).to_have_text('Bookmarks')
        expect(p.locator('#bookmarks-view')).to_contain_text('Save your first link')
        self.add_bookmark('https://example.com/python','Python handbook','Python, reference')
        created=self.store.bookmarks.listing()['items'][0]['created_at']
        self.add_bookmark('https://www.example.com/garden','Garden notes','garden, reference')
        self.add_bookmark('https://example.net/book','Weekend reading','reading')
        expect(p.locator('.bookmark-title').first).to_have_text('Weekend reading')
        p.locator('.bookmark-nav').get_by_role('link',name='Tags',exact=True).click()
        expect(p.locator('#page-title')).to_have_text('Bookmark tags')
        p.locator('.bookmark-group').filter(has_text='python').click()
        expect(p.locator('.bookmark-card')).to_have_count(1)
        expect(p.locator('.bookmark-title')).to_have_text('Python handbook')
        p.reload();expect(p.locator('.bookmark-title')).to_have_text('Python handbook')
        p.locator('.bookmark-domain').click()
        expect(p.locator('.bookmark-card')).to_have_count(2)
        p.locator('.bookmark-nav').get_by_role('link',name='Domains',exact=True).click()
        expect(p.locator('.bookmark-group')).to_have_count(2)
        p.locator('.bookmark-group').filter(has_text='example.com').click()
        expect(p.locator('.bookmark-card')).to_have_count(2)
        p.get_by_role('searchbox',name='Search bookmarks',exact=True).fill('handbook')
        expect(p.locator('.bookmark-card')).to_have_count(1)
        card=p.locator('.bookmark-card')
        card.get_by_role('button',name='Edit',exact=True).click()
        p.get_by_label('Title (optional)',exact=True).fill('Updated reference title')
        p.get_by_role('button',name='Save bookmark',exact=True).click()
        expect(p.locator('#modal')).not_to_be_visible()
        expect(p.locator('.bookmark-card')).to_have_count(0)
        p.get_by_role('searchbox',name='Search bookmarks',exact=True).fill('updated')
        expect(p.locator('.bookmark-title')).to_have_text('Updated reference title')
        self.assertEqual(self.store.bookmarks.listing('updated')['items'][0]['created_at'],created)
        p.locator('.bookmark-card').get_by_role('button',name='Delete',exact=True).click()
        p.get_by_role('button',name='Delete bookmark',exact=True).click()
        expect(p.locator('#modal')).not_to_be_visible()
        expect(p.locator('.bookmark-nav')).not_to_contain_text('Deleted')
        p.locator('.bookmark-nav').get_by_role('link',name='Recent',exact=True).click()
        expect(p.locator('.bookmark-card')).to_have_count(2)

    def test_bookmark_tags_autocomplete_the_current_tag_and_keep_new_tags(self):
        page = self.page
        self.store.bookmarks.save({'url':'https://example.com/existing','title':'Existing',
                                   'tags':['example', 'example project', 'reading']})
        page.locator('#bookmarks-open').click()
        page.locator('#add-bookmark').click()
        page.get_by_label('Web address').fill('https://example.com/new')
        page.get_by_label('Title (optional)').fill('New bookmark')
        page.get_by_label('Save page text for search').uncheck()
        tags = page.get_by_role('combobox', name='Tags')
        tags.fill('New tag, EX')
        suggestions = page.get_by_role('listbox', name='Existing tags')
        expect(suggestions.get_by_role('option')).to_have_text(['example', 'example project'])
        tags.press('ArrowDown')
        tags.press('Enter')
        expect(tags).to_have_value('New tag, example, ')
        expect(page.locator('#modal')).to_be_visible()
        tags.press_sequentially('ex')
        expect(suggestions.get_by_role('option')).to_have_text(['example project'])
        suggestions.get_by_role('option', name='example project').click()
        expect(tags).to_have_value('New tag, example, example project, ')
        tags.fill('New tag, rea, example, example project')
        tags.evaluate("(field) => field.setSelectionRange(12, 12)")
        tags.dispatch_event('click')
        suggestions.get_by_role('option', name='reading', exact=True).click()
        expect(tags).to_have_value('New tag, reading, example, example project')
        page.get_by_role('button', name='Save bookmark', exact=True).click()
        expect(page.locator('#modal')).not_to_be_visible()
        self.assertEqual(self.store.bookmarks.listing('New bookmark')['items'][0]['tags'],
                         ['example', 'example project', 'new tag', 'reading'])

    def test_clickable_tag_suggestions_and_new_bookmark_redirect_from_filter(self):
        page = self.page
        for index in range(2):
            self.store.bookmarks.save({
                'url':f'https://example.net/{index}', 'title':f'Existing {index}',
                'tags':['common'] + (['home lab'] if not index else [])
            })
        page.goto(self.url + '/bookmarks?tag=common&sort=oldest')
        page.locator('#add-bookmark').click()
        suggestions = page.get_by_role('group', name='Suggested tags')
        expect(suggestions.get_by_role('button').first).to_have_text('+ common')
        page.get_by_label('Web address', exact=True).fill('https://example.com/new')
        page.get_by_label('Title (optional)', exact=True).fill('Building a Home Lab')
        expect(suggestions.get_by_role('button').first).to_have_text('+ home lab')
        tags = page.get_by_role('combobox', name='Tags')
        expect(tags).to_have_value('')
        tags.fill('new tag')
        suggestions.get_by_role('button', name='Add tag home lab', exact=True).click()
        expect(tags).to_have_value('new tag, home lab')
        expect(suggestions.get_by_role('button', name='Add tag home lab', exact=True)).to_have_count(0)
        page.get_by_label('Save page text for search', exact=True).uncheck()
        page.get_by_role('button', name='Save bookmark', exact=True).click()
        expect(page).to_have_url(self.url + '/bookmarks')
        expect(page.locator('.bookmark-title').first).to_have_text('Building a Home Lab')
        self.assertEqual(self.store.bookmarks.listing()['items'][0]['tags'], ['home lab', 'new tag'])

    def test_bookmark_drafts_and_conflicting_edits(self):
        p=self.page
        p.locator('#bookmarks-open').click();p.locator('#add-bookmark').click()
        p.get_by_label('Save page text for search',exact=True).uncheck()
        p.get_by_label('Web address',exact=True).fill('example.com/draft')
        p.get_by_label('Title (optional)',exact=True).fill('My draft bookmark')
        p.evaluate("()=>{const key=Object.keys(localStorage).find(key=>key.startsWith('homewiki:bookmark-draft:'));const draft=JSON.parse(localStorage[key]);draft.description='Old draft notes';localStorage[key]=JSON.stringify(draft);}")
        p.reload();p.locator('#add-bookmark').click()
        expect(p.get_by_label('Title (optional)',exact=True)).to_have_value('My draft bookmark')
        expect(p.locator('#bookmark-draft-status')).to_contain_text('Recovered')
        expect(p.get_by_label('Short description',exact=True)).to_have_count(0)
        p.get_by_role('button',name='Save bookmark',exact=True).click()
        expect(p.locator('#modal')).not_to_be_visible()
        item=self.store.bookmarks.listing()['items'][0]
        p.locator('.bookmark-card').get_by_role('button',name='Edit',exact=True).click()
        p.get_by_label('Title (optional)',exact=True).fill('My local edit')
        other=self.store.bookmarks.save({**item,'title':'Other browser edit'})
        p.get_by_role('button',name='Save bookmark',exact=True).click()
        expect(p.locator('#modal .error-text')).to_contain_text('changed in another tab')
        expect(p.get_by_label('Title (optional)',exact=True)).to_have_value('My local edit')
        self.assertEqual(self.store.bookmarks.get(item['id'])['title'],'Other browser edit')
        p.get_by_role('button',name='Compare latest bookmark',exact=True).click()
        expect(p.locator('.bookmark-conflict')).to_contain_text('Other browser edit')
        p.get_by_label('Title (optional)',exact=True).fill('My local edit and other browser edit')
        p.get_by_role('button',name='Use my reviewed draft',exact=True).click()
        p.get_by_role('button',name='Save bookmark',exact=True).click()
        expect(p.locator('#modal')).not_to_be_visible()
        expect(p.locator('.bookmark-title')).to_have_text('My local edit and other browser edit')

    def test_discard_bookmark_draft_does_not_submit_the_form(self):
        p=self.page
        p.locator('#bookmarks-open').click();p.locator('#add-bookmark').click()
        p.get_by_label('Title (optional)',exact=True).fill('Discard me')
        p.get_by_label('Web address',exact=True).fill('https://example.com/discard')
        p.get_by_label('Save page text for search',exact=True).uncheck()
        p.get_by_role('button',name='Discard draft',exact=True).click()
        expect(p.locator('#modal')).not_to_be_visible()
        p.wait_for_timeout(100)
        self.assertEqual(self.store.bookmarks.listing()['total'],0)
        p.locator('#add-bookmark').click()
        expect(p.get_by_label('Web address',exact=True)).to_have_value('')

    def test_bookmark_title_fills_form_and_can_be_edited_before_save(self):
        p=self.page
        p.unroute('**/api/bookmarks/title')
        p.route('**/api/bookmarks/title',lambda route:route.fulfill(json={'title':'Fetched article title','url':route.request.post_data_json['url']}))
        p.locator('#bookmarks-open').click();p.locator('#add-bookmark').click()
        expect(p.get_by_label('Save page text for search',exact=True)).to_be_checked()
        self.assertIsNone(p.get_by_label('Tags',exact=True).get_attribute('placeholder'))
        p.get_by_label('Web address',exact=True).fill('https://example.com/autofill')
        expect(p.get_by_label('Title (optional)',exact=True)).to_have_value('Fetched article title')
        self.assertEqual(self.store.bookmarks.listing()['total'],0)
        p.get_by_label('Title (optional)',exact=True).fill('My hand-edited title')
        p.get_by_label('Save page text for search',exact=True).uncheck()
        p.get_by_role('button',name='Save bookmark',exact=True).click()
        expect(p.locator('#modal')).not_to_be_visible()
        expect(p.locator('.bookmark-title')).to_have_text('My hand-edited title')

    def test_late_bookmark_title_lookup_does_not_replace_manual_title(self):
        p=self.page;pending=[]
        p.unroute('**/api/bookmarks/title')
        p.route('**/api/bookmarks/title',lambda route:pending.append(route))
        p.locator('#bookmarks-open').click();p.locator('#add-bookmark').click()
        with p.expect_request(lambda request:request.url.endswith('/api/bookmarks/title')):
            p.get_by_label('Web address',exact=True).fill('https://example.com/slow')
        p.get_by_label('Title (optional)',exact=True).fill('Urban gardening guide')
        expect(p.get_by_role('button',name='Add tag gardening',exact=True)).to_be_visible()
        with p.expect_response(lambda response:response.url.endswith('/api/bookmarks/title')):
            pending[0].fulfill(json={'title':'Late automatic title','suggested_tags':['container gardening']})
        topic = p.get_by_role('button',name='Add tag container gardening',exact=True)
        expect(topic).to_be_visible()
        field = p.get_by_role('combobox',name='Tags')
        expect(field).to_have_value('')
        topic.click()
        expect(field).to_have_value('container gardening')
        expect(p.get_by_label('Title (optional)',exact=True)).to_have_value('Urban gardening guide')

    def test_bookmark_title_uses_latest_url_and_keeps_manual_title_on_url_change(self):
        p=self.page;pending=[]
        p.unroute('**/api/bookmarks/title')
        def handle(route):
            if route.request.post_data_json['url'].endswith('/old'):
                pending.append(route)
            else:route.fulfill(json={'title':'New URL title','suggested_tags':['new topic']})
        p.route('**/api/bookmarks/title',handle)
        p.locator('#bookmarks-open').click();p.locator('#add-bookmark').click()
        with p.expect_request(lambda request:request.url.endswith('/api/bookmarks/title')):
            p.get_by_label('Web address',exact=True).fill('https://example.com/old')
        p.get_by_label('Web address',exact=True).fill('https://example.com/new')
        p.wait_for_timeout(450)
        pending[0].fulfill(json={'title':'Stale old URL title','suggested_tags':['old topic']})
        expect(p.get_by_label('Title (optional)',exact=True)).to_have_value('New URL title')
        expect(p.get_by_role('button',name='Add tag new topic',exact=True)).to_be_visible()
        expect(p.get_by_role('button',name='Add tag old topic',exact=True)).to_have_count(0)
        p.get_by_label('Title (optional)',exact=True).fill('My custom title')
        p.get_by_label('Web address',exact=True).fill('https://example.com/third')
        p.wait_for_timeout(450)
        expect(p.get_by_label('Title (optional)',exact=True)).to_have_value('My custom title')

    def test_bookmark_automatically_reads_title_without_text_checkbox(self):
        from unittest.mock import patch
        import bookmarks
        p=self.page
        p.locator('#bookmarks-open').click();p.locator('#add-bookmark').click()
        p.get_by_label('Web address',exact=True).fill('https://example.com/automatic-title')
        expect(p.get_by_label('Save page text for search',exact=True)).to_be_checked()
        p.get_by_label('Save page text for search',exact=True).uncheck()
        with patch.object(bookmarks,'fetch_page_text',return_value={'title':'Automatically discovered title','text':'Do not save this body'}):
            p.get_by_role('button',name='Save bookmark',exact=True).click()
            expect(p.locator('#modal')).not_to_be_visible()
        expect(p.locator('.bookmark-title')).to_have_text('Automatically discovered title')
        saved=self.store.bookmarks.listing()['items'][0]
        self.assertEqual(saved['extraction_status'],'not_requested')
        self.assertEqual(self.store.bookmarks.get(saved['id'])['page_text'],'')
        p.locator('.bookmark-card').get_by_role('button',name='Edit',exact=True).click()
        expect(p.get_by_label('Title (optional)',exact=True)).to_have_value('Automatically discovered title')

    def test_bookmark_page_text_search_and_safe_rendering(self):
        from unittest.mock import patch
        import bookmarks
        p=self.page
        p.locator('#bookmarks-open').click();p.locator('#add-bookmark').click()
        p.get_by_label('Web address',exact=True).fill('https://example.com/extracted')
        p.get_by_label('Save page text for search',exact=True).check()
        with patch.object(bookmarks,'fetch_page_text',return_value={'title':'Article title','text':'A fascinating nebula and telescope guide <script>window.bookmarkInjected=true</script>'}):
            p.get_by_role('button',name='Save bookmark',exact=True).click()
            expect(p.locator('#modal')).not_to_be_visible()
        expect(p.locator('.bookmark-title')).to_have_text('Article title')
        p.get_by_role('searchbox',name='Search bookmarks',exact=True).fill('nebula')
        expect(p.locator('.bookmark-card')).to_have_count(1)
        expect(p.locator('.bookmark-excerpt')).to_contain_text('nebula')
        p.get_by_role('button',name='View text',exact=True).click()
        expect(p.locator('.bookmark-saved-text')).to_contain_text('<script>')
        self.assertEqual(p.locator('#modal script').count(),0)
        self.assertEqual(p.locator('.bookmark-card img').count(),0)
        self.assertFalse(p.evaluate('Boolean(window.bookmarkInjected)'))
        p.locator('#modal-close').click()
        with patch.object(bookmarks,'fetch_page_text',side_effect=bookmarks.BookmarkError(400,'Could not fetch this page')):
            p.get_by_role('button',name='Refresh text',exact=True).click()
            expect(p.locator('.bookmark-text-status')).to_contain_text('Previously saved text')
        expect(p.locator('.bookmark-card')).to_have_count(1)

    def test_bookmarks_preserve_wiki_draft_and_reserved_page_routes(self):
        p=self.page
        p.locator('#edit-page').click();p.locator('#source').fill('# My unsaved wiki work')
        p.locator('#bookmarks-open').click()
        expect(p.locator('#page-title')).to_have_text('Bookmarks')
        p.locator('#brand-home').click()
        expect(p.locator('#source')).to_have_value('# My unsaved wiki work')
        self.store.save('bookmarks','# A wiki page named bookmarks')
        p.goto(self.url+'/page/bookmarks')
        expect(p.locator('#page-title')).to_have_text('A wiki page named bookmarks')
        p.locator('#bookmarks-open').click()
        expect(p.locator('#page-title')).to_have_text('Bookmarks')
        p.locator('.bookmark-nav').get_by_role('link',name='Tags',exact=True).click()
        expect(p).to_have_url(self.url+'/bookmarks/tags')
        p.reload();expect(p.locator('#page-title')).to_have_text('Bookmark tags')
        p.go_back();expect(p.locator('#page-title')).to_have_text('Bookmarks')
        p.set_viewport_size({'width':390,'height':844})
        self.assertTrue(p.evaluate('document.documentElement.scrollWidth<=innerWidth'))

    def test_running_version_link_opens_the_exact_commit(self):
        p=self.page
        revision='0123456789abcdef0123456789abcdef01234567'
        p.route('**/api/version',lambda route:route.fulfill(json={'revision':revision,'dirty':False}))
        self.context.route('https://github.com/rroller/homewiki/commit/**',lambda route:route.fulfill(body='Commit preview',content_type='text/plain'))
        p.reload()
        link=p.locator('#build-version a')
        expect(link).to_have_text(revision[:7])
        expect(link).to_have_attribute('href','https://github.com/rroller/homewiki/commit/'+revision)
        expect(link).to_have_attribute('rel','noopener noreferrer')
        with p.expect_popup() as opened:
            link.click()
        expect(opened.value).to_have_url('https://github.com/rroller/homewiki/commit/'+revision)
        opened.value.close()
        self.assertNotIn('Markdown powered',p.locator('.local-note').inner_text())

    def test_missing_version_metadata_does_not_block_the_wiki(self):
        p=self.page
        p.route('**/api/version',lambda route:route.fulfill(json={'revision':None,'dirty':False}))
        p.reload();expect(p.locator('#build-version')).to_have_text('Development')
        expect(p.locator('#build-version a')).to_have_count(0)
        expect(p.locator('#content')).to_contain_text('Welcome to Homewiki')
        p.unroute('**/api/version')
        p.route('**/api/version',lambda route:route.fulfill(status=503,json={'error':'Unavailable'}))
        p.reload();expect(p.locator('#build-version')).to_have_text('Version unavailable')
        expect(p.locator('#content')).to_contain_text('Welcome to Homewiki')

    def test_capture_page_prefills_reviews_and_saves_without_a_modal(self):
        from urllib.parse import urlencode
        p=self.page
        target='https://example.com/article?x=a%2Bb&other=1#section'
        query=urlencode({'url':target,'title':'Article & notes','description':'Selected words <not HTML>','tags':'reading, reference','extract':'0','save':'1'})
        p.goto(self.url+'/bookmarks/add?'+query)
        expect(p.locator('#page-title')).to_have_text('Add bookmark')
        expect(p.locator('#modal')).not_to_be_visible()
        expect(p.get_by_label('Web address',exact=True)).to_have_value(target)
        expect(p.get_by_label('Title (optional)',exact=True)).to_have_value('Article & notes')
        expect(p.get_by_label('Short description',exact=True)).to_have_count(0)
        expect(p.get_by_label('Tags',exact=True)).to_have_value('reading, reference')
        expect(p.get_by_label('Save page text for search',exact=True)).not_to_be_checked()
        self.assertEqual(self.store.bookmarks.listing()['total'],0)
        p.get_by_label('Title (optional)',exact=True).fill('My reviewed title')
        p.get_by_role('button',name='Save bookmark',exact=True).click()
        expect(p).to_have_url(self.url+'/bookmarks')
        expect(p.locator('.bookmark-title')).to_have_text('My reviewed title')
        expect(p.get_by_role('link',name='Recent',exact=True)).to_have_attribute('aria-current','page')
        saved=self.store.bookmarks.listing()['items'][0]
        self.assertEqual(saved['url'],target)
        self.assertEqual(saved['title'],'My reviewed title')
        self.assertEqual(saved['tags'],['reading','reference'])
        self.assertNotIn('description',saved)
        p.reload()
        expect(p.locator('.bookmark-title')).to_have_text('My reviewed title')
        self.assertEqual(self.store.bookmarks.listing()['total'],1)

    def test_capture_shared_text_finds_url_and_autofills_title_on_mobile(self):
        from urllib.parse import urlencode
        p=self.page
        p.set_viewport_size({'width':390,'height':844})
        p.unroute('**/api/bookmarks/title')
        p.route('**/api/bookmarks/title',lambda route:route.fulfill(json={'title':'An automatically resolved article'}))
        query=urlencode({'text':'Read this later https://example.com/shared?one=1&two=2','tag':['mobile','reading']},doseq=True)
        p.goto(self.url+'/bookmarks/add/?'+query)
        expect(p.get_by_label('Web address',exact=True)).to_have_value('https://example.com/shared?one=1&two=2')
        expect(p.get_by_label('Title (optional)',exact=True)).to_have_value('An automatically resolved article')
        expect(p.get_by_label('Short description',exact=True)).to_have_count(0)
        expect(p.get_by_label('Tags',exact=True)).to_have_value('mobile, reading')
        expect(p.get_by_label('Save page text for search',exact=True)).to_be_checked()
        self.assertTrue(p.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        p.get_by_role('button',name='Save bookmark',exact=True).scroll_into_view_if_needed()
        expect(p.get_by_role('button',name='Save bookmark',exact=True)).to_be_visible()
        self.assertEqual(self.store.bookmarks.listing()['total'],0)
        p.get_by_label('Title (optional)',exact=True).fill('A'*300)
        p.get_by_label('Save page text for search',exact=True).uncheck()
        p.get_by_role('button',name='Save bookmark',exact=True).click()
        expect(p).to_have_url(self.url+'/bookmarks')
        self.assertTrue(p.evaluate('document.documentElement.scrollWidth<=innerWidth'))

    def test_capture_drafts_are_separate_for_each_shared_url_and_modal(self):
        from urllib.parse import urlencode
        p=self.page
        p.locator('#bookmarks-open').click();p.locator('#add-bookmark').click()
        p.get_by_label('Title (optional)',exact=True).fill('My modal draft')
        p.get_by_label('Web address',exact=True).fill('https://example.com/modal')
        p.locator('#modal-close').click()
        first=self.url+'/bookmarks/add?'+urlencode({'url':'https://example.com/first','title':'First shared title'})
        second=self.url+'/bookmarks/add?'+urlencode({'url':'https://example.com/second','title':'Second shared title'})
        p.goto(first)
        expect(p.get_by_label('Web address',exact=True)).to_have_value('https://example.com/first')
        p.get_by_label('Title (optional)',exact=True).fill('Keep my capture draft')
        p.reload();expect(p.get_by_label('Title (optional)',exact=True)).to_have_value('Keep my capture draft')
        p.goto(second);expect(p.get_by_label('Title (optional)',exact=True)).to_have_value('Second shared title')
        p.goto(first);expect(p.get_by_label('Title (optional)',exact=True)).to_have_value('Keep my capture draft')
        p.get_by_role('button',name='Discard draft',exact=True).click()
        expect(p.locator('#page-title')).to_have_text('Bookmarks')
        p.locator('#add-bookmark').click()
        expect(p.get_by_label('Web address',exact=True)).to_have_value('https://example.com/modal')
        expect(p.get_by_label('Title (optional)',exact=True)).to_have_value('My modal draft')

    def test_bookmarklet_captures_current_page_title_and_encoded_url(self):
        p=self.page
        p.goto(self.url+'/bookmarks/tools')
        expect(p.locator('#page-title')).to_have_text('Save from your browser')
        code=p.locator('#bookmarklet-code').input_value()
        self.assertTrue(code.startswith('javascript:void('))
        self.assertIn(self.url+'/bookmarks/add',code)
        source=self.context.new_page();source.on('pageerror',lambda error:self.errors.append(str(error)))
        source.route('http://capture-source.test/**',lambda route:route.fulfill(
            body='<title>Article &amp; notes</title><p id="selection">Selected notes &amp; a quote</p>',content_type='text/html'))
        source_url='http://capture-source.test/article?plus=a%2Bb&part=2#section'
        source.goto(source_url)
        source.evaluate("""()=>{const range=document.createRange();range.selectNodeContents(document.querySelector('#selection'));getSelection().removeAllRanges();getSelection().addRange(range);}""")
        source.evaluate('(address)=>{location.href=address;}',code)
        expect(source.locator('#page-title')).to_have_text('Add bookmark')
        expect(source.get_by_label('Web address',exact=True)).to_have_value(source_url)
        expect(source.get_by_label('Title (optional)',exact=True)).to_have_value('Article & notes')
        expect(source.get_by_label('Short description',exact=True)).to_have_count(0)
        self.assertNotIn('description',code)
        self.assertNotIn('getSelection',code)
        self.assertEqual(self.store.bookmarks.listing()['total'],0)
        source.get_by_label('Save page text for search',exact=True).uncheck()
        source.get_by_role('button',name='Save bookmark',exact=True).click()
        expect(source).to_have_url(self.url+'/bookmarks')
        expect(source.locator('.bookmark-title')).to_have_text('Article & notes')
        self.assertEqual(self.store.bookmarks.listing()['total'],1)
        source.go_back()
        expect(source).to_have_url(source_url)
        source.close()

    def test_capture_tools_have_mobile_setup_and_do_not_run_on_install_click(self):
        p=self.page
        p.goto(self.url+'/bookmarks/tools/')
        expect(p).to_have_url(self.url+'/bookmarks/tools')
        expect(p.locator('#bookmarks-view')).to_contain_text('iPhone Safari setup')
        expect(p.locator('#bookmark-capture-address')).to_have_value(self.url+'/bookmarks/add')
        expect(p.locator('#bookmarklet-code')).to_have_attribute('readonly','')
        p.locator('#install-bookmarklet').click()
        expect(p).to_have_url(self.url+'/bookmarks/tools')
        expect(p.locator('#toast')).to_contain_text('Drag this')
        p.locator('#copy-bookmarklet').click()
        expect(p.locator('#toast')).to_be_visible()
        p.set_viewport_size({'width':390,'height':844})
        self.assertTrue(p.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        self.assertEqual(self.store.bookmarks.listing()['total'],0)

    def test_untrusted_capture_parameters_do_not_execute_or_edit_existing_bookmarks(self):
        from urllib.parse import urlencode
        p=self.page
        saved=self.store.bookmarks.save({'url':'https://example.com/already-saved','title':'Original title','tags':[]})
        query=urlencode({'url':saved['url'],'title':'<img src=x onerror=alert(1)>','id':saved['id'],'revision':saved['revision'],'extract':'0','save':'1'})
        p.goto(self.url+'/bookmarks/add?'+query)
        expect(p.get_by_label('Title (optional)',exact=True)).to_have_value('<img src=x onerror=alert(1)>')
        self.assertEqual(p.locator('#bookmarks-view img').count(),0)
        self.assertEqual(self.store.bookmarks.get(saved['id']),saved)
        p.get_by_role('button',name='Save bookmark',exact=True).click()
        expect(p.locator('.bookmark-capture-form .error-text')).to_contain_text('already bookmarked')
        expect(p.locator('.bookmark-existing-link')).to_have_attribute('href',re.compile(r'^/bookmarks\?q='))
        self.assertEqual(self.store.bookmarks.get(saved['id']),saved)
        p.get_by_label('Web address',exact=True).fill('javascript:alert(1)')
        p.get_by_role('button',name='Save bookmark',exact=True).click()
        expect(p.locator('.bookmark-capture-form .error-text')).to_contain_text('HTTP or HTTPS')
        self.assertEqual(self.store.bookmarks.listing()['total'],1)

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
        p.locator('#settings-open').click();p.locator('#trash-open').click();p.locator('.result').filter(has_text='Projects/Renamed').click();expect(p.locator('#page-title')).to_have_text('My notebook');expect(p.locator('#breadcrumb')).to_have_text('Projects/Renamed')
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
        expect(preview.locator('ul ul ul a')).to_have_attribute('href','/Home')
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
            ('[example.com](https://www.example.com) [[Games]]', ['https://www.example.com','/Games']),
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
        brand=p.locator('#brand-home');expect(brand).to_have_attribute('href','/Home')
        brand.focus();p.keyboard.press('Enter')
        expect(p).to_have_url(self.url+'/Home');expect(p.locator('#page-title')).to_have_text('A home for your thoughts')
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
        expect(toolbar.get_by_role('link',name='Bookmark tools',exact=True)).to_be_visible()
        self.assertEqual(p.locator('#tools-open').inner_text(),'')
        p.locator('#new-page').click();expect(p.locator('#modal-title')).to_have_text('Create a page');p.locator('#modal-close').click()
        p.locator('#recently-edited').click();expect(p.locator('#page-title')).to_have_text('Recently edited')
        expect(p.locator('#recently-edited')).to_have_attribute('aria-current','page')
        p.locator('#tools-open').click()
        expect(p.locator('#page-title')).to_have_text('Save from your browser')
        expect(p.locator('#tools-open')).to_have_attribute('aria-current','page')
        p.locator('#brand-home').click()
        expect(p.locator('#tools-open')).not_to_have_attribute('aria-current','page')
        for width in [1440,1000,800,390]:
            p.set_viewport_size({'width':width,'height':900})
            p.reload()
            if width<721:p.locator('#expand').click()
            sidebar=p.locator('#sidebar').bounding_box();brand=p.locator('.brand').bounding_box()
            buttons=toolbar.locator('button,a').all()
            self.assertEqual(len(buttons),5)
            brand_link=p.locator('#brand-home').bounding_box()
            self.assertGreaterEqual(buttons[0].bounding_box()['x'],brand_link['x']+brand_link['width'])
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

    def test_root_urls_direct_load_history_and_legacy_links(self):
        p=self.page
        self.store.save('Games/Owned','# Owned\n\n[[Games]]')
        response=p.goto(self.url+'/Games/Owned')
        self.assertEqual(response.status,200)
        expect(p.locator('#page-title')).to_have_text('Owned')
        expect(p).to_have_url(self.url+'/Games/Owned')
        expect(p.locator('#tree a[aria-current=page]')).to_have_attribute('href','/Games/Owned')
        p.reload();expect(p.locator('#page-title')).to_have_text('Owned')
        p.locator('#breadcrumb').get_by_role('link',name='Games',exact=True).click()
        expect(p).to_have_url(self.url+'/Games')
        p.go_back();expect(p.locator('#page-title')).to_have_text('Owned')
        p.go_forward();expect(p).to_have_url(self.url+'/Games')
        p.goto(self.url+'/page/Games/Owned?view=wiki#section')
        expect(p).to_have_url(self.url+'/Games/Owned?view=wiki#section')
        expect(p.locator('#page-title')).to_have_text('Owned')
        p.goto(self.url+'/#/page/Games%2FOwned')
        expect(p).to_have_url(self.url+'/Games/Owned')
        expect(p.locator('#page-title')).to_have_text('Owned')
        p.locator('#content').get_by_role('link',name='Games',exact=True).click()
        expect(p).to_have_url(self.url+'/Games')
        p.locator('#brand-home').click();expect(p).to_have_url(self.url+'/Home')
        # Names with spaces and literal percent signs round-trip cleanly.
        self.store.save('Notes & ideas/100% done','# Done')
        p.goto(self.url+'/Notes%20%26%20ideas/100%25%20done')
        expect(p.locator('#page-title')).to_have_text('Done')
        p.reload();expect(p.locator('#page-title')).to_have_text('Done')
    def test_reserved_page_names_and_markdown_root_links(self):
        p=self.page
        self.store.save('api/Notes','# Reserved note')
        self.store.save('recently-edited','# My recent note')
        p.goto(self.url+'/page/api/Notes')
        expect(p.locator('#page-title')).to_have_text('Reserved note')
        expect(p).to_have_url(self.url+'/page/api/Notes')
        p.reload();expect(p.locator('#page-title')).to_have_text('Reserved note')
        p.goto(self.url+'/page/recently-edited')
        expect(p.locator('#page-title')).to_have_text('My recent note')
        p.locator('#recently-edited').click()
        expect(p.locator('#page-title')).to_have_text('Recently edited')
        expect(p).to_have_url(self.url+'/recently-edited')
        source='[New root](/Games/Completed%20games) [Old root](/page/Games/Completed%20games) [[Home]]'
        self.store.save('Root link check','# Root link check\n\n'+source)
        p.goto(self.url+'/Root%20link%20check')
        expect(p.locator('#page-title')).to_have_text('Root link check')
        self.assertEqual(p.locator('#content a').evaluate_all('links=>links.map(a=>a.getAttribute("href"))'),['/Games/Completed%20games','/Games/Completed%20games','/Home'])
        p.locator('#content a').first.click()
        expect(p).to_have_url(self.url+'/Games/Completed%20games')
        expect(p.locator('#page-title')).to_have_text('Completed games')

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

    def test_mobile_sidebar_backdrop_dismisses_without_clicking_the_page(self):
        context=self.browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
        try:
            p=context.new_page();p.on('pageerror',lambda error:self.errors.append(str(error)))
            for width in [390,720]:
                p.set_viewport_size({'width':width,'height':844});p.goto(self.url)
                expect(p.locator('#sidebar-shade')).not_to_be_visible()
                p.locator('#expand').tap()
                expect(p.locator('#sidebar-shade')).to_be_visible()
                p.locator('.section-label').tap()
                expect(p.locator('#sidebar')).to_be_visible()
                # A tap above the underlying page-actions button only dismisses the menu.
                box=p.locator('#more').bounding_box()
                p.touchscreen.tap(box['x']+box['width']/2,box['y']+box['height']/2)
                expect(p.locator('#sidebar')).not_to_be_visible()
                expect(p.locator('#sidebar-shade')).not_to_be_visible()
                expect(p.locator('#actions')).not_to_be_visible()
                expect(p.locator('#expand')).to_be_focused()
                p.locator('#expand').tap()
                p.touchscreen.tap(width-10,830)
                expect(p.locator('#sidebar')).not_to_be_visible()
                p.reload();expect(p.locator('#sidebar-shade')).not_to_be_visible()
            p.set_viewport_size({'width':1440,'height':1000});p.locator('#expand').click()
            expect(p.locator('#sidebar-shade')).not_to_be_visible()
            p.locator('#more').click();expect(p.locator('#actions')).to_be_visible()
        finally:
            context.close()

    def test_visual_editor_shares_draft_and_preserves_untouched_markdown(self):
        page = self.page
        original = "# Visual test\n\nKeep [[Games|the wiki link]] and `code`.\n\n~~~python\nprint('keep')\n~~~\n\n| A | B |\n| --- | --- |\n| 1 |\n"
        self.store.save('Visual example', original)
        page.goto(self.url + '/Visual%20example')
        page.locator('#edit-page').click()
        page.locator('#tab-visual').click()
        expect(page.locator('#visual-blocks .visual-code')).to_have_count(1)
        expect(page.locator('#visual-blocks td[contenteditable]')).to_have_count(2)
        page.locator('#tab-write').click()
        expect(page.locator('#source')).to_have_value(original)
        page.locator('#tab-visual').click()
        paragraph = page.get_by_role('textbox', name='Visual text block').nth(1)
        paragraph.locator('p').click()
        paragraph.press('End')
        page.keyboard.insert_text(' Added')
        page.keyboard.press('Shift+ArrowLeft')
        page.keyboard.press('Shift+ArrowLeft')
        page.keyboard.press('Shift+ArrowLeft')
        page.keyboard.press('Shift+ArrowLeft')
        page.keyboard.press('Shift+ArrowLeft')
        page.get_by_role('button', name='Bold', exact=True).click()
        expect(paragraph.locator('b,strong')).to_have_text('Added')
        expected = original.replace('and `code`.', 'and `code`. **Added**')
        page.locator('#tab-preview').click()
        expect(page.locator('#preview strong')).to_have_text('Added')
        page.reload()
        expect(page.locator('#notice')).to_contain_text('Recovered your unsaved draft')
        expect(page.locator('#source')).to_have_value(expected)
        page.locator('#save-page').click()
        expect(page.locator('#content strong')).to_have_text('Added')
        self.assertEqual(self.store.read('Visual example')['content'], expected)

    def test_visual_editor_edits_table_cells_end_list_and_code(self):
        page = self.page
        original = (
            "# Collection\n\n"
            "| Name | Platform | Status |\n| :--- | --- | ---: |\n"
            "| Portal | PC |\n| Halo  | Xbox | Done |\n\n"
            "> A quotation\n\n~~~python\nprint('original')\n~~~\n\n"
            "## Notes\n\n- [x] Existing task\n  - Nested wiki link [[Games|Play]]\n- Last line\n"
        )
        self.store.save('Visual collection', original)
        page.goto(self.url + '/Visual%20collection')
        page.locator('#edit-page').click()
        page.locator('#tab-visual').click()
        visual = page.locator('#visual-blocks')
        visual.locator('th').last.fill('Completed')
        visual.locator('tbody tr').first.locator('td').last.fill('PC | Steam')
        visual.locator('blockquote p').click()
        page.keyboard.press('End')
        page.keyboard.insert_text(' edited')
        visual.get_by_role('textbox', name='Visual code block').fill("print('updated')")
        visual.get_by_text('Last line', exact=True).click()
        page.keyboard.press('End')
        page.keyboard.insert_text(' edited')
        page.locator('#tab-write').click()
        source = page.locator('#source').input_value()
        self.assertIn('| Name | Platform | Completed |\n| :--- | --- | ---: |', source)
        self.assertIn(r'| Portal | PC | PC \| Steam |', source)
        self.assertIn('| Halo  | Xbox | Done |', source)
        self.assertIn('> A quotation edited', source)
        self.assertIn("~~~python\nprint('updated')\n~~~", source)
        self.assertIn('- [x] Existing task', source)
        self.assertIn('  - Nested wiki link [[Games|Play]]', source)
        self.assertIn('- Last line edited', source)
        page.locator('#save-page').click()
        expect(page.locator('#content tbody tr').first.locator('td').last).to_have_text('PC | Steam')
        expect(page.locator('#content li').last).to_have_text('Last line edited')
        expect(page.locator('#content .task-box')).to_be_checked()
        self.assertEqual(self.store.read('Visual collection')['content'], source)

    def test_visual_editor_adds_a_list_link_and_uploaded_image(self):
        page = self.page
        page.set_viewport_size({'width':390,'height':844})
        page.reload()
        page.locator('#edit-page').click()
        page.locator('#tab-visual').click()
        page.locator('#visual-add-paragraph').click()
        paragraph = page.get_by_role('textbox', name='Visual text block').last
        page.keyboard.insert_text('Visit ')
        page.locator('#link-picker').click()
        page.get_by_role('searchbox', name='Find a page to link…').fill('Completed games')
        page.locator('#modal .result').first.click()
        expect(paragraph.locator('a')).to_have_text('Games/Completed games')
        page.get_by_role('button', name='Bullets', exact=True).click()
        expect(paragraph.locator('li')).to_contain_text(re.compile(r'Visit\s+Games/Completed games'))
        self.image_drop('#visual-blocks')
        expect(page.locator('#visual-blocks img')).to_have_js_property('naturalWidth', 12)
        page.locator('#tab-write').click()
        expect(page.locator('#source')).to_have_value(re.compile(r'- Visit \[\[Games/Completed games\]\]'))
        page.locator('#save-page').click()
        expect(page.locator('#content img')).to_be_visible()
        expect(page.locator('#content li').get_by_role('link', name='Games/Completed games', exact=True)).to_have_attribute('href', '/Games/Completed%20games')

    def test_download_and_confirm_restore_of_a_backup_zip(self):
        page = self.page
        self.store.bookmarks.save({'url':'https://example.com/backup', 'title':'Backup test', 'tags':['saved']})
        page.locator('#settings-open').click()
        page.locator('#backup-open').click()
        with page.expect_download() as archive:
            page.get_by_role('link', name='Download backup ZIP', exact=True).click()
        archive = archive.value
        self.assertTrue(archive.suggested_filename.endswith('.zip'))
        snapshot = self.store.save('After backup', '# After backup')
        self.assertEqual(page.request.post(self.url + '/api/backup/restore', data=b'zip',
            headers={'X-Homewiki':'1','Content-Type':'application/zip'}).status,403)
        upload = page.get_by_label('Homewiki backup ZIP')
        button = page.get_by_role('button', name='Restore backup', exact=True)
        upload.set_input_files(archive.path())
        expect(button).to_be_disabled()
        page.get_by_role('checkbox', name='Replace my current saved data').check()
        button.click()
        expect(page.locator('#toast')).to_have_text('Backup restored')
        expect(page.locator('#modal')).not_to_be_visible()
        self.assertFalse(self.store.path(snapshot['path']).exists())
        self.assertEqual(self.store.bookmarks.listing(tags=['saved'])['total'],1)
        expect(page).to_have_url(self.url+'/Home')
        page.locator('#settings-open').click()
        page.locator('#backup-open').click()
        with page.expect_download() as previous:
            page.get_by_role('link', name='Download ZIP from before the last restore').click()
        import zipfile
        with zipfile.ZipFile(previous.value.path()) as saved:
            self.assertEqual(saved.read('pages/After backup.md'),b'# After backup')

    def test_appearance_markdown_and_mobile(self):
        p=self.page
        # Escaping and links: raw HTML and script URLs must never become active HTML.
        rendered=p.evaluate("markdown('# Title\\n\\n<script>alert(1)</script>\\n\\n[x](javascript:alert)\\n\\n| A | B |\\n| --- | --- |\\n| a | b |\\n\\n- [x] Done\\n\\n```\\n<b>code</b>\\n```')")
        self.assertNotIn('<script>',rendered);self.assertNotIn('href="javascript:',rendered);self.assertIn('<table>',rendered);self.assertIn('type="checkbox"',rendered);self.assertIn('&lt;b&gt;code',rendered)
        self.assertEqual(p.evaluate('document.documentElement.scrollWidth <= innerWidth'),True)
        if os.getenv('HOMEWIKI_SCREENSHOTS'):
            target=Path(os.environ['HOMEWIKI_SCREENSHOTS']);target.mkdir(parents=True,exist_ok=True);p.screenshot(path=str(target/'homewiki-light.png'),full_page=True)
        expect(p.locator('.sidebar-bottom button')).to_have_count(0)
        p.locator('#settings-open').click()
        expect(p).to_have_url(self.url+'/settings')
        expect(p.locator('#settings-open')).to_have_attribute('aria-current','page')
        p.locator('#theme-toggle').click();expect(p.locator('html')).to_have_attribute('data-theme','dark')
        p.reload();expect(p.locator('#settings-view')).to_be_visible();expect(p.locator('html')).to_have_attribute('data-theme','dark')
        p.locator('#brand-home').click();expect(p.locator('#settings-view')).to_be_hidden()
        if os.getenv('HOMEWIKI_SCREENSHOTS'):p.screenshot(path=str(target/'homewiki-dark.png'),full_page=True)
        p.locator('#collapse').click();expect(p.locator('#sidebar')).not_to_be_visible();p.locator('#expand').click();expect(p.locator('#sidebar')).to_be_visible()
        p.set_viewport_size({'width':390,'height':844});p.goto(self.url+'/settings')
        expect(p.locator('#settings-view')).to_be_visible()
        expect(p.locator('#sidebar')).not_to_be_visible()
        self.assertTrue(p.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        p.goto(self.url+'/Home')
        p.set_viewport_size({'width':390,'height':844});p.reload();expect(p.locator('#sidebar')).not_to_be_visible();p.locator('#expand').click();expect(p.locator('#sidebar')).to_be_visible();p.locator('#tree').get_by_role('link',name='Games I own',exact=False).click();expect(p.locator('#page-title')).to_have_text('Games I own');expect(p.locator('#sidebar')).not_to_be_visible()
        self.assertEqual(p.evaluate('document.documentElement.scrollWidth <= innerWidth'),True)
        p.locator('#edit-page').click();p.locator('#source').fill('# Mobile draft');p.locator('#cancel-edit').click();p.get_by_role('button',name='Discard draft').click();p.locator('#modal').get_by_role('button',name='Discard draft',exact=True).click();expect(p.locator('#notice')).to_be_hidden()

if __name__=='__main__':unittest.main()
