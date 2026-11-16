'use strict';
let bookmarkSearchTimer;
function bmElement(tag,className='',text=''){
  const element=document.createElement(tag);if(className)element.className=className;if(text)element.textContent=text;return element;
}
function bookmarkURL({q='',tags=[],domain='',sort='newest',page=0}={}){
  const args=new URLSearchParams();if(q)args.set('q',q);tags.forEach(tag=>args.append('tag',tag));if(domain)args.set('domain',domain);
  if(sort!=='newest')args.set('sort',sort);if(page)args.set('page',page);
  return '/bookmarks'+(args.size?'?'+args:'');
}
function bookmarkLink(label,url,className=''){
  const link=bmElement('a',className,label);link.href=url;return link;
}
function leaveBookmarks(){
  state.bookmarks=false;$('bookmarks-view').hidden=true;$('bookmarks-open').removeAttribute('aria-current');$('tools-open').removeAttribute('aria-current');
}
function navigateBookmarks(){
  clearTimeout(bookmarkSearchTimer);preserveDraft();const turn=++navigating;
  state.loading=false;state.bookmarks=true;state.recent=false;state.page=null;state.editing=false;state.dirty=false;
  notice('');$('reader').hidden=true;$('editor').hidden=true;$('edit-page').hidden=true;$('more').hidden=true;$('actions').hidden=true;$('page-footer').hidden=true;
  $('recently-edited').removeAttribute('aria-current');$('bookmarks-open').setAttribute('aria-current','page');
  const view=$('bookmarks-view');view.hidden=false;view.replaceChildren();
  const mode=location.pathname==='/bookmarks/add'?'add':location.pathname==='/bookmarks/tools'?'tools':location.pathname==='/bookmarks/tags'?'tags':location.pathname==='/bookmarks/domains'?'domains':'links';
  if(mode==='tools')$('tools-open').setAttribute('aria-current','page');else $('tools-open').removeAttribute('aria-current');
  const params=new URLSearchParams(location.search);
  const filter={q:params.get('q')||'',tags:params.getAll('tag'),domain:params.get('domain')||'',sort:params.get('sort')||'newest',page:Math.max(0,Math.min(2500,Math.trunc(Number(params.get('page'))||0)))};
  const heading=mode==='add'?'Add bookmark':mode==='tools'?'Save from your browser':mode==='tags'?'Bookmark tags':mode==='domains'?'Bookmark domains':'Bookmarks';
  $('page-title').textContent=heading;document.title=heading+' · Homewiki';renderBreadcrumb('Bookmarks');
  if(mode!=='links'){
    const crumb=$('breadcrumb');crumb.replaceChildren(bookmarkLink('Bookmarks','/bookmarks'),bmElement('span','breadcrumb-separator','/'),bmElement('span','',mode==='add'?'Add bookmark':mode==='tools'?'Save from your browser':mode==='tags'?'Tags':'Domains'));
  }
  const toolbar=bmElement('div','bookmark-toolbar'),nav=bmElement('nav','bookmark-nav');nav.setAttribute('aria-label','Bookmark library');
  for(const [label,url,selected] of [['Recent','/bookmarks',mode==='links'],['Tags','/bookmarks/tags',mode==='tags'],['Domains','/bookmarks/domains',mode==='domains'],['Save from browser','/bookmarks/tools',mode==='tools']]){
    const link=bookmarkLink(label,url);if(selected)link.setAttribute('aria-current','page');nav.append(link);
  }
  toolbar.append(nav);
  if(mode!=='add'){const add=bmElement('button','primary-button','Add bookmark');add.id='add-bookmark';add.onclick=()=>bookmarkForm();toolbar.append(add);}
  view.append(toolbar);
  if(mode==='add'||mode==='tools'){
    if(mode==='add'){
      const intro=bmElement('p','bookmark-capture-intro','Review the link, add any tags, then save it to your library.');view.append(intro);
      const mount=bmElement('div','bookmark-capture-form');view.append(mount);
      bookmarkForm(null,{mount,prefill:bookmarkCaptureValues(params)});
    }else renderBookmarkTools(view);
    renderTree();if(innerWidth<721)sidebar(true);window.scrollTo(0,0);return;
  }
  const results=bmElement('div','bookmark-results');results.setAttribute('aria-live','polite');
  if(mode==='links'){
    const controls=bmElement('div','bookmark-controls'),search=document.createElement('input');search.type='search';search.id='bookmark-search';search.placeholder='Search bookmarks and saved page text…';search.setAttribute('aria-label','Search bookmarks');search.maxLength=500;search.value=filter.q;
    const sort=document.createElement('select');sort.setAttribute('aria-label','Sort bookmarks');
    for(const [value,label] of [['newest','Newest first'],['oldest','Oldest first'],['title','Title']]){const option=bmElement('option','',label);option.value=value;sort.append(option);}sort.value=filter.sort;
    controls.append(search,sort);view.append(controls);
    const chips=bmElement('div','bookmark-filters');
    for(const tag of filter.tags)chips.append(bookmarkLink('#'+tag+' ×',bookmarkURL({...filter,tags:filter.tags.filter(value=>value!==tag),page:0}),'bookmark-chip'));
    if(filter.domain)chips.append(bookmarkLink(filter.domain+' ×',bookmarkURL({...filter,domain:'',page:0}),'bookmark-chip'));
    if(filter.tags.length||filter.domain)chips.append(bookmarkLink('Clear filters',bookmarkURL({q:filter.q}),'bookmark-clear'));
    view.append(chips);
    let request=0;
    async function load(){
      const sequence=++request;results.textContent='Loading bookmarks…';
      const query=new URLSearchParams();if(filter.q)query.set('q',filter.q);filter.tags.forEach(tag=>query.append('tag',tag));if(filter.domain)query.set('domain',filter.domain);
      query.set('sort',filter.sort);query.set('offset',String(filter.page*40));query.set('limit','40');
      try{
        const data=await api('bookmarks?'+query);if(turn!==navigating||sequence!==request)return;
        results.replaceChildren();results.append(bmElement('p','bookmark-count',data.total+' bookmark'+(data.total===1?'':'s')+(filter.q?' matching “'+filter.q+'”':'')));
        if(!data.items.length)results.append(bmElement('p','empty',filter.q||filter.tags.length||filter.domain?'No bookmarks match these filters.':'Save your first link with Add bookmark.'));
        for(const bookmark of data.items)results.append(bookmarkCard(bookmark,filter));
        if(data.total>40){
          const pages=bmElement('nav','bookmark-pagination');pages.setAttribute('aria-label','Bookmark pages');
          if(filter.page)pages.append(bookmarkLink('← Previous',bookmarkURL({...filter,page:filter.page-1})));
          pages.append(bmElement('span','',String(filter.page+1)+' / '+Math.ceil(data.total/40)));
          if((filter.page+1)*40<data.total)pages.append(bookmarkLink('Next →',bookmarkURL({...filter,page:filter.page+1})));
          results.append(pages);
        }
      }catch(error){if(turn===navigating&&sequence===request){results.replaceChildren();errorInto(results,error);}}
    }
    search.oninput=()=>{
      clearTimeout(bookmarkSearchTimer);request++;filter.q=search.value;filter.page=0;
      history.replaceState(null,'',bookmarkURL(filter));
      bookmarkSearchTimer=setTimeout(load,220);
    };
    sort.onchange=()=>{clearTimeout(bookmarkSearchTimer);filter.sort=sort.value;filter.page=0;history.pushState(null,'',bookmarkURL(filter));load();};
    load();
  }else{
    const search=document.createElement('input');search.type='search';search.placeholder=mode==='tags'?'Filter tags…':'Filter domains…';search.setAttribute('aria-label',mode==='tags'?'Filter tags':'Filter domains');view.append(search);
    results.textContent='Loading…';
    api('bookmarks/'+mode).then(groups=>{
      if(turn!==navigating)return;
      function draw(){
        results.replaceChildren();const grid=bmElement('div','bookmark-group-grid');
        for(const group of groups.filter(item=>item.name.toLocaleLowerCase().includes(search.value.toLocaleLowerCase()))){
          const link=bookmarkLink('',bookmarkURL(mode==='tags'?{tags:[group.name]}:{domain:group.name}),'bookmark-group');
          link.append(bmElement('span','',group.name),bmElement('small','',String(group.count)));grid.append(link);
        }
        results.append(grid);if(!grid.childElementCount)results.append(bmElement('p','empty','No '+mode+' found.'));
      }
      search.oninput=draw;draw();
    }).catch(error=>{if(turn===navigating){results.replaceChildren();errorInto(results,error);}});
  }
  view.append(results);renderTree();if(innerWidth<721)sidebar(true);window.scrollTo(0,0);
}
function bookmarkCard(bookmark,filter){
  const card=bmElement('article','bookmark-card');card.dataset.id=bookmark.id;
  const title=bookmarkLink(bookmark.title,bookmark.url,'bookmark-title');title.target='_blank';title.rel='noopener noreferrer';
  const url=bmElement('p','bookmark-address',bookmark.url);url.title=bookmark.url;
  card.append(title,url);
  const metadata=bmElement('div','bookmark-metadata');
  metadata.append(bookmarkLink(bookmark.domain,bookmarkURL({domain:bookmark.domain}),'bookmark-domain'));
  const time=document.createElement('time');time.dateTime=bookmark.created_at;time.textContent='Saved '+editDate(bookmark.created_at);time.title=new Date(bookmark.created_at).toString();metadata.append(time);card.append(metadata);
  const tags=bmElement('div','bookmark-tags');
  for(const tag of bookmark.tags)tags.append(bookmarkLink('#'+tag,bookmarkURL({...filter,tags:[...new Set([...filter.tags,tag])],page:0}),'bookmark-chip'));
  if(tags.childElementCount)card.append(tags);
  if(filter.q&&bookmark.excerpt)card.append(bmElement('p','bookmark-excerpt',bookmark.excerpt));
  if(bookmark.extraction_status==='failed')card.append(bmElement('p','bookmark-text-status','Page text: '+bookmark.extraction_error+(bookmark.extracted_at?' Previously saved text is still searchable.':'')));
  else if(bookmark.extracted_at)card.append(bmElement('p','bookmark-text-status','Page text saved '+editDate(bookmark.extracted_at)));
  const actions=bmElement('div','bookmark-actions');
  function button(label,callback){const element=bmElement('button','subtle-button',label);element.onclick=callback;actions.append(element);return element;}
    button('Edit',()=>bookmarkForm(bookmark.id));
    button(bookmark.extracted_at?'Refresh text':'Save page text',async event=>{
      const button=event.currentTarget;button.disabled=true;button.textContent='Saving text…';
      try{const saved=await api('bookmarks/extract',{id:bookmark.id,revision:bookmark.revision});navigateBookmarks();toast(saved.extraction_status==='saved'?'Page text saved':'Bookmark kept; page text could not be saved.');}
      catch(error){button.disabled=false;button.textContent='Save page text';toast(error.message);}
    });
    if(bookmark.extracted_at)button('View text',()=>bookmarkText(bookmark.id));
    const remove=button('Delete',()=>{
      const body=modal('Delete bookmark permanently?');const description=bmElement('p','','This permanently deletes “'+bookmark.title+'” and its saved page text.');body.append(description);
      const row=bmElement('div','dialog-buttons'),cancel=bmElement('button','subtle-button','Cancel'),confirm=bmElement('button','primary-button','Delete bookmark');
      cancel.onclick=closeModal;confirm.onclick=async()=>{
        confirm.disabled=true;try{await api('bookmarks/delete',{id:bookmark.id,revision:bookmark.revision});closeModal();navigateBookmarks();toast('Bookmark deleted');}catch(error){errorInto(body,error);confirm.disabled=false;}
      };row.append(cancel,confirm);body.append(row);
    });remove.classList.add('danger');
  card.append(actions);return card;
}
function bookmarkText(id){
  const body=modal('Saved page text'),view=bmElement('div');view.textContent='Loading…';body.append(view);
  api('bookmarks/item?id='+id).then(bookmark=>{
    if(!view.isConnected||!$('modal').open)return;view.replaceChildren();
    view.append(bmElement('p','',bookmark.title),bmElement('pre','bookmark-saved-text',bookmark.page_text||'No page text has been saved.'));
  }).catch(error=>{if(view.isConnected){view.replaceChildren();errorInto(view,error);}});
}
async function bookmarkForm(id=null,options={}){
  const embedded=Boolean(options.mount),body=options.mount||modal(id?'Edit bookmark':'Add bookmark'),view=bmElement('div');view.textContent='Loading…';body.append(view);
  let bookmark={url:'',title:'',tags:[],revision:null};
  try{if(id)bookmark=await api('bookmarks/item?id='+id);}catch(error){view.replaceChildren();errorInto(view,error);return;}
  const present=()=>view.isConnected&&(embedded?state.bookmarks&&location.pathname==='/bookmarks/add':$('modal').open);
  if(!present())return;
  view.replaceChildren();
  const scope=embedded?'capture:'+encodeURIComponent(options.prefill?.url||'new'):(id||'new');
  const prefix='homewiki:bookmark-draft:'+scope+':',key=prefix+tabID;
  let recovered=null,recoveredKey=null,recoveredRaw=null;
  try{
    recovered=JSON.parse(stored(key,'null'));
    if(!recovered)for(let index=0;index<localStorage.length;index++){
      const candidate=localStorage.key(index);if(!candidate.startsWith(prefix))continue;
      const raw=localStorage.getItem(candidate);let value;try{value=JSON.parse(raw);}catch{continue;}
      if(value&&typeof value.url==='string'&&(!recovered||(value.updated||0)>(recovered.updated||0))){recovered=value;recoveredKey=candidate;recoveredRaw=raw;}
    }
  }catch{}
  function clearDraft(){
    try{localStorage.removeItem(key);if(recoveredKey&&localStorage.getItem(recoveredKey)===recoveredRaw)localStorage.removeItem(recoveredKey);}catch{}
  }
  const initial=recovered||{...bookmark,extract:!id,...(options.prefill||{})};
  const form=document.createElement('form');form.id='bookmark-form';
  form.innerHTML='<label for="bookmark-url">Web address</label><input id="bookmark-url" type="text" required maxlength="4096" placeholder="https://example.com"><label for="bookmark-title">Title (optional)</label><input id="bookmark-title" type="text" maxlength="300" placeholder="Leave blank to use the page title"><p id="bookmark-title-status" class="bookmark-form-hint" role="status"></p><label for="bookmark-tags">Tags</label><input id="bookmark-tags" type="text"><p class="bookmark-form-hint">Separate tags with commas. Multiword tags are welcome.</p><label class="bookmark-extract"><input id="bookmark-extract" type="checkbox"> Save page text for search</label><p class="bookmark-form-hint">Optional. Reads public HTML or text pages; links can be saved even if extraction fails.</p><p id="bookmark-draft-status" role="status"></p><div class="dialog-buttons"><button type="button" class="subtle-button" id="bookmark-cancel">Cancel</button><button class="primary-button" type="submit">Save bookmark</button></div>';
  view.append(form);
  $('bookmark-url').value=initial.url||'';$('bookmark-title').value=initial.title||'';$('bookmark-tags').value=Array.isArray(initial.tags)?initial.tags.join(', '):'';$('bookmark-extract').checked=Boolean(initial.extract);
  if(recovered)$('bookmark-draft-status').textContent='Recovered your bookmark draft.';
  let titleEdited=recovered?.titleEdited??Boolean(initial.title),titleSequence=0,titleTimer,lookupRunning=false,queuedLookup=null,runningLookup=null,lastLookup=null,titleCache=new Map(),submitting=false;
  const values=()=>({id:id||undefined,url:$('bookmark-url').value,title:$('bookmark-title').value,tags:$('bookmark-tags').value.split(',').map(tag=>tag.trim()).filter(Boolean),extract:$('bookmark-extract').checked,revision:recovered?recovered.revision:bookmark.revision,titleEdited,updated:Date.now()});
  form.oninput=()=>{$('bookmark-draft-status').textContent=setStored(key,JSON.stringify(values()))?'Draft saved in this browser.':'Browser storage unavailable; keep this dialog open.';};
  const urlField=$('bookmark-url'),titleField=$('bookmark-title'),titleStatus=$('bookmark-title-status');
  const active=()=>present()&&!submitting;
  const keepDraft=()=>{if(active())form.oninput();};
  async function lookupTitle(url,sequence){
    if(!active()||titleEdited||sequence!==titleSequence)return;
    if(titleCache.has(url)){const title=titleCache.get(url);titleField.value=title;titleStatus.textContent=title?'Title found. You can edit it.':'No title found. Enter your own.';keepDraft();return;}
    if(lookupRunning){queuedLookup={url,sequence};return;}
    lookupRunning=true;runningLookup=url;titleStatus.textContent='Looking up page title…';
    try{
      const result=await api('bookmarks/title',{url});
      if(!active()||sequence!==titleSequence||titleEdited||urlField.value.trim()!==url)return;
      lastLookup=url;titleCache.set(url,result.title||'');
      if(result.title){titleField.value=result.title;titleStatus.textContent='Title found. You can edit it.';keepDraft();}
      else titleStatus.textContent='No title found. Enter your own.';
    }catch(error){if(active()&&sequence===titleSequence&&!titleEdited)titleStatus.textContent='Could not read title. Enter your own.';}
    finally{lookupRunning=false;runningLookup=null;const next=queuedLookup;queuedLookup=null;if(next&&active())lookupTitle(next.url,next.sequence);}
  }
  function scheduleTitle(immediate=false){
    clearTimeout(titleTimer);const sequence=++titleSequence,url=urlField.value.trim();
    if(!active()||titleEdited)return;
    titleField.value='';titleStatus.textContent='';keepDraft();
    if(!url)return;
    try{const candidate=new URL(/^https?:\/\//i.test(url)?url:'https://'+url);if(!['http:','https:'].includes(candidate.protocol)||!candidate.hostname||(!candidate.hostname.includes('.')&&!candidate.hostname.includes(':')&&candidate.hostname!=='localhost'))return;}catch{return;}
    if(immediate)lookupTitle(url,sequence);else titleTimer=setTimeout(()=>lookupTitle(url,sequence),400);
  }
  urlField.addEventListener('input',()=>scheduleTitle());
  urlField.addEventListener('blur',()=>{if(!lookupRunning&&lastLookup!==urlField.value.trim())scheduleTitle(true);});
  titleField.addEventListener('input',()=>{titleEdited=true;titleSequence++;clearTimeout(titleTimer);queuedLookup=null;titleStatus.textContent='';});
  if(!titleEdited&&urlField.value.trim())scheduleTitle();
  const exit=()=>{if(embedded){history.pushState(null,'','/bookmarks');route();}else closeModal();};
  $('bookmark-cancel').onclick=exit;
  const discard=bmElement('button','subtle-button','Discard draft');discard.type='button';discard.onclick=()=>{clearDraft();exit();};
  form.querySelector('.dialog-buttons').prepend(discard);
  form.onsubmit=async event=>{
    event.preventDefault();submitting=true;titleSequence++;clearTimeout(titleTimer);queuedLookup=null;const button=form.querySelector('[type=submit]');button.disabled=true;
    const value=values();setStored(key,JSON.stringify(value));
    try{
      const saved=await api('bookmarks/save',value);
      clearDraft();
      if(embedded){
        if(present())showBookmarkCaptureSuccess(options.mount,saved);
      }else{
        closeModal();
        if(!state.bookmarks){history.pushState(null,'','/bookmarks');route();}else navigateBookmarks();
      }
      toast(saved.extraction_status==='failed'?'Bookmark saved. Page text could not be saved; you can retry later.':'Bookmark saved');
    }catch(error){
      errorInto(view,error);button.disabled=false;submitting=false;
      if(embedded&&!id&&error.status===409&&!view.querySelector('.bookmark-existing-link')){
        view.append(bookmarkLink('Find this bookmark in your library',bookmarkURL({q:value.url}),'bookmark-existing-link'));
      }
      if(error.status===409&&id&&!view.querySelector('.bookmark-reload')){
        const reload=bmElement('button','subtle-button bookmark-reload','Compare latest bookmark');
        reload.onclick=async()=>{
          reload.disabled=true;
          try{
            const latest=await api('bookmarks/item?id='+id);if(!view.isConnected)return;
            const panel=bmElement('div','bookmark-conflict');
            panel.append(bmElement('strong','','Latest saved bookmark'),bmElement('p','',latest.title+'\n'+latest.url+'\nTags: '+latest.tags.join(', ')));
            const reviewed=bmElement('button','subtle-button','Use my reviewed draft');
            reviewed.onclick=()=>{bookmark.revision=latest.revision;recovered=null;setStored(key,JSON.stringify(values()));panel.replaceChildren(bmElement('p','','Review your fields above, then Save bookmark to replace this version.'));};
            panel.append(reviewed);view.append(panel);reload.remove();
          }catch(error){errorInto(view,error);reload.disabled=false;}
        };
        view.append(reload,bmElement('p','bookmark-form-hint','Your draft remains in the fields above. Compare and merge any changes before saving.'));
      }
    }
  };
  $('bookmark-url').focus();
}

function bookmarkCaptureValues(params){
  const text=params.get('text')||'';
  const url=params.get('url')||(text.match(/https?:\/\/[^\s<>"\u0000-\u001f]+/i)?.[0]||'');
  const tags=[...(params.get('tags')||'').split(','),...params.getAll('tag')].map(value=>value.trim()).filter(Boolean);
  return {url,title:(params.get('title')||'').trim().slice(0,300),tags,
    extract:!['0','false'].includes((params.get('extract')||'').toLowerCase())};
}
function showBookmarkCaptureSuccess(mount,bookmark){
  mount.replaceChildren();history.replaceState(null,'','/bookmarks/add');
  $('page-title').textContent='Bookmark saved';document.title='Bookmark saved · Homewiki';
  const card=bmElement('div','bookmark-capture-success');card.setAttribute('role','status');
  card.append(bmElement('p','','Saved to your Homewiki library.'));
  const original=bookmarkLink(bookmark.title,bookmark.url,'bookmark-title');original.target='_blank';original.rel='noopener noreferrer';card.append(original);
  if(bookmark.extraction_status==='failed')card.append(bmElement('p','bookmark-form-hint','The link is saved. Its page text could not be fetched; you can retry from the library.'));
  const links=bmElement('div','bookmark-capture-links');
  links.append(bookmarkLink('View bookmarks','/bookmarks'),bookmarkLink('Add another','/bookmarks/add'));
  card.append(links,bmElement('p','bookmark-form-hint','Use your browser’s Back button to return to the page you were reading.'));
  mount.append(card);
}
function homewikiBookmarklet(base=location.origin+'/bookmarks/add'){
  return 'javascript:void(function(){var p=new URLSearchParams({url:location.href,title:document.title.slice(0,300)});location.assign('+JSON.stringify(base)+'+"?"+p.toString());}());';
}
async function copyBookmarkTool(area,message){
  try{
    if(navigator.clipboard?.writeText)await navigator.clipboard.writeText(area.value);
    else{area.focus();area.select();area.setSelectionRange(0,area.value.length);if(!document.execCommand('copy'))throw new Error();}
    toast(message);
  }catch{area.focus();area.select();area.setSelectionRange(0,area.value.length);toast('Select Copy to copy the highlighted text.');}
}
function renderBookmarkTools(view){
  view.append(bmElement('p','bookmark-capture-intro','Save the page you are reading to Homewiki, then review its title and tags before saving.'));
  const setup=bmElement('section','bookmark-tool-card');
  setup.append(bmElement('h2','','Save to Homewiki bookmarklet'));
  const install=bookmarkLink('Save to Homewiki',homewikiBookmarklet(),'bookmarklet-install');install.id='install-bookmarklet';install.draggable=true;
  install.title='Drag this to your bookmarks bar, or copy its code below.';
  install.onclick=event=>{event.preventDefault();toast('Drag this to your bookmarks bar, or use Copy bookmarklet below.');};
  setup.append(install,bmElement('p','bookmark-form-hint','On a desktop, drag this link to your bookmarks bar. It opens the capture form in the current tab.'));
  const label=bmElement('label','','Bookmarklet address');label.htmlFor='bookmarklet-code';
  const code=document.createElement('textarea');code.id='bookmarklet-code';code.className='bookmark-tool-code';code.readOnly=true;code.rows=4;code.spellcheck=false;code.value=homewikiBookmarklet();
  const copy=bmElement('button','outline-button','Copy bookmarklet');copy.type='button';copy.id='copy-bookmarklet';copy.onclick=()=>copyBookmarkTool(code,'Bookmarklet copied');
  setup.append(label,code,copy);
  const heading=bmElement('h3','','iPhone Safari setup'),steps=document.createElement('ol');
  for(const text of [
    'Tap Copy bookmarklet above.',
    'Use Safari’s Share menu → Add Bookmark. Name it “Save to Homewiki” and save it in Favorites.',
    'Open Bookmarks → Favorites → Edit. Edit that bookmark and replace its address with the copied bookmarklet address, including javascript: at the start.',
    'While reading any page, open Favorites and tap Save to Homewiki. Review the filled form, add tags, and tap Save bookmark.'
  ])steps.append(bmElement('li','',text));
  setup.append(heading,steps,bmElement('p','bookmark-form-hint','If copying is unavailable, select the address above and use Copy. Some browsers or websites block bookmarklets; a browser extension or an iOS Shortcut can open the capture URL below instead.'));
  const help=bookmarkLink('Safari bookmark help','https://support.apple.com/guide/iphone/bookmark-a-website-iph42ab2f3a7/ios');help.target='_blank';help.rel='noopener noreferrer';setup.append(help);
  view.append(setup);
  const integration=bmElement('section','bookmark-tool-card');integration.append(bmElement('h2','','Capture URL for extensions and shortcuts'));
  const address=location.origin+'/bookmarks/add';
  integration.append(bookmarkLink('Open Add bookmark',address));
  const addressLabel=bmElement('label','','Capture address');addressLabel.htmlFor='bookmark-capture-address';
  const field=document.createElement('input');field.type='text';field.id='bookmark-capture-address';field.readOnly=true;field.value=address;
  const copyAddress=bmElement('button','outline-button','Copy capture address');copyAddress.type='button';copyAddress.onclick=()=>copyBookmarkTool(field,'Capture address copied');
  const example=bmElement('pre','bookmark-tool-example',address+'?url='+encodeURIComponent('https://example.com/article')+'&title='+encodeURIComponent('Article title'));
  integration.append(addressLabel,field,copyAddress,example,bmElement('p','bookmark-form-hint','Pass URL-encoded url and optional title, tags, or repeated tag parameters. A text parameter can contain a shared URL. Omit title to look it up automatically. Add extract=0 to start without page-text saving. Opening the address never saves a bookmark automatically.'));
  view.append(integration);
}
