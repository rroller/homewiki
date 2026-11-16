'use strict';
let bookmarkSearchTimer;
function bmElement(tag,className='',text=''){
  const element=document.createElement(tag);if(className)element.className=className;if(text)element.textContent=text;return element;
}
function bookmarkURL({q='',tags=[],domain='',deleted=false,sort='newest',page=0}={}){
  const args=new URLSearchParams();if(q)args.set('q',q);tags.forEach(tag=>args.append('tag',tag));if(domain)args.set('domain',domain);
  if(deleted)args.set('deleted','1');if(sort!=='newest')args.set('sort',sort);if(page)args.set('page',page);
  return '/bookmarks'+(args.size?'?'+args:'');
}
function bookmarkLink(label,url,className=''){
  const link=bmElement('a',className,label);link.href=url;return link;
}
function leaveBookmarks(){
  state.bookmarks=false;$('bookmarks-view').hidden=true;$('bookmarks-open').removeAttribute('aria-current');
}
function navigateBookmarks(){
  clearTimeout(bookmarkSearchTimer);preserveDraft();const turn=++navigating;
  state.loading=false;state.bookmarks=true;state.recent=false;state.page=null;state.editing=false;state.dirty=false;
  notice('');$('reader').hidden=true;$('editor').hidden=true;$('edit-page').hidden=true;$('more').hidden=true;$('actions').hidden=true;$('page-footer').hidden=true;
  $('recently-edited').removeAttribute('aria-current');$('bookmarks-open').setAttribute('aria-current','page');
  const view=$('bookmarks-view');view.hidden=false;view.replaceChildren();
  const mode=location.pathname==='/bookmarks/tags'?'tags':location.pathname==='/bookmarks/domains'?'domains':'links';
  const params=new URLSearchParams(location.search);
  const filter={q:params.get('q')||'',tags:params.getAll('tag'),domain:params.get('domain')||'',deleted:params.get('deleted')==='1',sort:params.get('sort')||'newest',page:Math.max(0,Math.min(2500,Math.trunc(Number(params.get('page'))||0)))};
  const heading=mode==='tags'?'Bookmark tags':mode==='domains'?'Bookmark domains':filter.deleted?'Deleted bookmarks':'Bookmarks';
  $('page-title').textContent=heading;document.title=heading+' · Homewiki';renderBreadcrumb('Bookmarks');
  if(mode!=='links'){
    const crumb=$('breadcrumb');crumb.replaceChildren(bookmarkLink('Bookmarks','/bookmarks'),bmElement('span','breadcrumb-separator','/'),bmElement('span','',mode==='tags'?'Tags':'Domains'));
  }
  const toolbar=bmElement('div','bookmark-toolbar'),nav=bmElement('nav','bookmark-nav');nav.setAttribute('aria-label','Bookmark library');
  for(const [label,url,selected] of [['Recent','/bookmarks',mode==='links'&&!filter.deleted],['Tags','/bookmarks/tags',mode==='tags'],['Domains','/bookmarks/domains',mode==='domains'],['Deleted',bookmarkURL({deleted:true}),filter.deleted]]){
    const link=bookmarkLink(label,url);if(selected)link.setAttribute('aria-current','page');nav.append(link);
  }
  const add=bmElement('button','primary-button','Add bookmark');add.id='add-bookmark';add.onclick=()=>bookmarkForm();toolbar.append(nav,add);view.append(toolbar);
  const results=bmElement('div','bookmark-results');results.setAttribute('aria-live','polite');
  if(mode==='links'){
    const controls=bmElement('div','bookmark-controls'),search=document.createElement('input');search.type='search';search.id='bookmark-search';search.placeholder='Search bookmarks and saved page text…';search.setAttribute('aria-label','Search bookmarks');search.maxLength=500;search.value=filter.q;
    const sort=document.createElement('select');sort.setAttribute('aria-label','Sort bookmarks');
    for(const [value,label] of [['newest','Newest first'],['oldest','Oldest first'],['title','Title']]){const option=bmElement('option','',label);option.value=value;sort.append(option);}sort.value=filter.sort;
    controls.append(search,sort);view.append(controls);
    const chips=bmElement('div','bookmark-filters');
    for(const tag of filter.tags)chips.append(bookmarkLink('#'+tag+' ×',bookmarkURL({...filter,tags:filter.tags.filter(value=>value!==tag),page:0}),'bookmark-chip'));
    if(filter.domain)chips.append(bookmarkLink(filter.domain+' ×',bookmarkURL({...filter,domain:'',page:0}),'bookmark-chip'));
    if(filter.tags.length||filter.domain)chips.append(bookmarkLink('Clear filters',bookmarkURL({deleted:filter.deleted,q:filter.q}),'bookmark-clear'));
    view.append(chips);
    let request=0;
    async function load(){
      const sequence=++request;results.textContent='Loading bookmarks…';
      const query=new URLSearchParams();if(filter.q)query.set('q',filter.q);filter.tags.forEach(tag=>query.append('tag',tag));if(filter.domain)query.set('domain',filter.domain);
      if(filter.deleted)query.set('deleted','1');query.set('sort',filter.sort);query.set('offset',String(filter.page*40));query.set('limit','40');
      try{
        const data=await api('bookmarks?'+query);if(turn!==navigating||sequence!==request)return;
        results.replaceChildren();results.append(bmElement('p','bookmark-count',data.total+' bookmark'+(data.total===1?'':'s')+(filter.q?' matching “'+filter.q+'”':'')));
        if(!data.items.length)results.append(bmElement('p','empty',filter.q||filter.tags.length||filter.domain?'No bookmarks match these filters.':filter.deleted?'No deleted bookmarks.':'Save your first link with Add bookmark.'));
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
  if(bookmark.description)card.append(bmElement('p','bookmark-description',bookmark.description));
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
  if(bookmark.deleted_at){
    const restore=button('Restore',async()=>{
      restore.disabled=true;try{await api('bookmarks/restore',{id:bookmark.id,revision:bookmark.revision});navigateBookmarks();toast('Bookmark restored');}catch(error){restore.disabled=false;toast(error.message);}
    });
  }else{
    button('Edit',()=>bookmarkForm(bookmark.id));
    button(bookmark.extracted_at?'Refresh text':'Save page text',async event=>{
      const button=event.currentTarget;button.disabled=true;button.textContent='Saving text…';
      try{const saved=await api('bookmarks/extract',{id:bookmark.id,revision:bookmark.revision});navigateBookmarks();toast(saved.extraction_status==='saved'?'Page text saved':'Bookmark kept; page text could not be saved.');}
      catch(error){button.disabled=false;button.textContent='Save page text';toast(error.message);}
    });
    if(bookmark.extracted_at)button('View text',()=>bookmarkText(bookmark.id));
    const remove=button('Delete',()=>{
      const body=modal('Delete bookmark?');const description=bmElement('p','',bookmark.title+' will be recoverable from Bookmarks → Deleted.');body.append(description);
      const row=bmElement('div','dialog-buttons'),cancel=bmElement('button','subtle-button','Cancel'),confirm=bmElement('button','primary-button','Delete bookmark');
      cancel.onclick=closeModal;confirm.onclick=async()=>{
        confirm.disabled=true;try{await api('bookmarks/delete',{id:bookmark.id,revision:bookmark.revision});closeModal();navigateBookmarks();toast('Bookmark moved to Deleted');}catch(error){errorInto(body,error);confirm.disabled=false;}
      };row.append(cancel,confirm);body.append(row);
    });remove.classList.add('danger');
  }
  card.append(actions);return card;
}
function bookmarkText(id){
  const body=modal('Saved page text'),view=bmElement('div');view.textContent='Loading…';body.append(view);
  api('bookmarks/item?id='+id).then(bookmark=>{
    if(!view.isConnected||!$('modal').open)return;view.replaceChildren();
    view.append(bmElement('p','',bookmark.title),bmElement('pre','bookmark-saved-text',bookmark.page_text||'No page text has been saved.'));
  }).catch(error=>{if(view.isConnected){view.replaceChildren();errorInto(view,error);}});
}
async function bookmarkForm(id=null){
  const body=modal(id?'Edit bookmark':'Add bookmark'),view=bmElement('div');view.textContent='Loading…';body.append(view);
  let bookmark={url:'',title:'',description:'',tags:[],revision:null};
  try{if(id)bookmark=await api('bookmarks/item?id='+id);}catch(error){view.replaceChildren();errorInto(view,error);return;}
  if(!view.isConnected||!$('modal').open)return;
  view.replaceChildren();
  const prefix='homewiki:bookmark-draft:'+(id||'new')+':',key=prefix+tabID;
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
  const initial=recovered||{...bookmark,extract:false};
  const form=document.createElement('form');form.id='bookmark-form';
  form.innerHTML='<label for="bookmark-url">Web address</label><input id="bookmark-url" type="text" required maxlength="4096" placeholder="https://example.com"><label for="bookmark-title">Title (optional)</label><input id="bookmark-title" type="text" maxlength="300" placeholder="Leave blank to use the page title"><label for="bookmark-description">Short description</label><textarea id="bookmark-description" maxlength="2000" rows="3"></textarea><label for="bookmark-tags">Tags</label><input id="bookmark-tags" type="text" placeholder="reading, technology, ideas"><p class="bookmark-form-hint">Separate tags with commas. Multiword tags are welcome.</p><label class="bookmark-extract"><input id="bookmark-extract" type="checkbox"> Save page text for search</label><p class="bookmark-form-hint">Optional. Reads public HTML or text pages; links can be saved even if extraction fails.</p><p id="bookmark-draft-status" role="status"></p><div class="dialog-buttons"><button type="button" class="subtle-button" id="bookmark-cancel">Cancel</button><button class="primary-button" type="submit">Save bookmark</button></div>';
  view.append(form);
  $('bookmark-url').value=initial.url||'';$('bookmark-title').value=initial.title||'';$('bookmark-description').value=initial.description||'';$('bookmark-tags').value=Array.isArray(initial.tags)?initial.tags.join(', '):'';$('bookmark-extract').checked=Boolean(initial.extract);
  if(recovered)$('bookmark-draft-status').textContent='Recovered your bookmark draft.';
  const values=()=>({id:id||undefined,url:$('bookmark-url').value,title:$('bookmark-title').value,description:$('bookmark-description').value,tags:$('bookmark-tags').value.split(',').map(tag=>tag.trim()).filter(Boolean),extract:$('bookmark-extract').checked,revision:recovered?recovered.revision:bookmark.revision,updated:Date.now()});
  form.oninput=()=>{$('bookmark-draft-status').textContent=setStored(key,JSON.stringify(values()))?'Draft saved in this browser.':'Browser storage unavailable; keep this dialog open.';};
  $('bookmark-cancel').onclick=closeModal;
  const discard=bmElement('button','subtle-button','Discard draft');discard.onclick=()=>{clearDraft();closeModal();};
  form.querySelector('.dialog-buttons').prepend(discard);
  form.onsubmit=async event=>{
    event.preventDefault();const button=form.querySelector('[type=submit]');button.disabled=true;
    const value=values();setStored(key,JSON.stringify(value));
    try{
      const saved=await api('bookmarks/save',value);
      clearDraft();
      closeModal();
      if(!state.bookmarks){history.pushState(null,'','/bookmarks');route();}else navigateBookmarks();
      toast(saved.extraction_status==='failed'?'Bookmark saved. Page text could not be saved; you can retry later.':'Bookmark saved');
    }catch(error){
      errorInto(view,error);button.disabled=false;
      if(error.status===409&&id&&!view.querySelector('.bookmark-reload')){
        const reload=bmElement('button','subtle-button bookmark-reload','Compare latest bookmark');
        reload.onclick=async()=>{
          reload.disabled=true;
          try{
            const latest=await api('bookmarks/item?id='+id);if(!view.isConnected)return;
            const panel=bmElement('div','bookmark-conflict');
            panel.append(bmElement('strong','','Latest saved bookmark'),bmElement('p','',latest.title+'\n'+latest.url+'\n'+latest.description+'\nTags: '+latest.tags.join(', ')));
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
