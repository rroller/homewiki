'use strict';
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const reservedPageRoots=new Set(['api','uploads','page','recently-edited','bookmarks','bookmarks.js','app.js','visual-editor.js','style.css','favicon.png','favicon.ico']);
const pageURL = p => (reservedPageRoots.has(p.split('/')[0])?'/page/':'/') + p.split('/').map(encodeURIComponent).join('/');
const state = {pageOrder:{parents:{},revision:null}, loading:false, recent:false, pages:[], page:null, editing:false, base:null, dirty:false, remote:false};
let tabID;
try { tabID=sessionStorage.getItem('homewiki:tab'); if(!tabID){tabID=crypto.randomUUID();sessionStorage.setItem('homewiki:tab',tabID);} } catch {tabID=String(Date.now())+Math.random();}
const draftPrefix = p => 'homewiki:draft:v2:' + encodeURIComponent(p) + ':';
const draftKey = p => draftPrefix(p) + tabID;
let toastTimer, navigating = 0;
const pendingUploads=new Map();
let draggedPage=null,orderSaving=false,orderEpoch=0;
function toast(message) { $('toast').textContent=message; $('toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('toast').hidden=true,4000); }
function stored(key, fallback=null) { try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; } }
function setStored(key, value) { try { localStorage.setItem(key,value); return true; } catch { return false; } }
const recoveredDrafts=new Map();
function removeDraft(path) { try { localStorage.removeItem(draftKey(path)); const recovered=recoveredDrafts.get(path); if(recovered && localStorage.getItem(recovered.key)===recovered.raw)localStorage.removeItem(recovered.key); recoveredDrafts.delete(path); } catch { /* unload protection remains */ } }
function draft(path) {
  try {
    const own=stored(draftKey(path));if(own)return JSON.parse(own);
    let latest=null;
    for(let i=0;i<localStorage.length;i++){
      const key=localStorage.key(i);if(!key.startsWith(draftPrefix(path)))continue;
      const raw=localStorage.getItem(key);let value;try{value=JSON.parse(raw);}catch{continue;}
      if(!value||typeof value.content!=='string')continue;
      if(!latest||value.updated>latest.value.updated)latest={key,raw,value};
    }
    if(latest){recoveredDrafts.set(path,latest);return latest.value;}
    return null;
  } catch { return null; }
}
async function api(route, data) {
  const response=await fetch('/api/'+route, data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-Homewiki':'1'},body:JSON.stringify(data)});
  const result=await response.json();
  if(!response.ok) { const error=new Error(result.error || 'Request failed'); error.status=response.status; throw error; }
  return result;
}
function webLink(href,label){
  try{
    const url=new URL(href);
    if(!['http:','https:'].includes(url.protocol)||!url.hostname)return null;
    return '<a href="'+esc(href)+'" target="_blank" rel="noopener noreferrer">'+esc(label)+'</a>';
  }catch{return null;}
}
function trimLinkPunctuation(value){
  let trimmed=value.replace(/[.,!?;:*_~]+$/g,'');
  const pairs={')':'(',']':'[','}':'{'};
  while(pairs[trimmed.at(-1)]){
    const close=trimmed.at(-1),open=pairs[close];
    if(trimmed.split(close).length<=trimmed.split(open).length)break;
    trimmed=trimmed.slice(0,-1).replace(/[.,!?;:*_~]+$/g,'');
  }
  return trimmed;
}
function autoLinks(text,hold){
  text=text.replace(/<(https?:\/\/[^\s<>\x00-\x1f]+)>/gi,(whole,url)=>{
    const html=webLink(url,url);return html?hold(html,whole):whole;
  });
  const candidates=/(?<![\w@/.:=\u005c-])(?:https?:\/\/[^\s<>"'\x60\u005c\x00-\x1f\u2018-\u201f\u00ab\u00bb]+|(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}(?::\d{1,5})?(?:[/?#][^\s<>"'\x60\u005c\x00-\x1f\u2018-\u201f\u00ab\u00bb]*)?)/gi;
  const fileExtensions=new Set(['md','markdown','txt','json','yaml','yml','js','ts','css','html','htm','py','png','jpg','jpeg','gif','webp','svg','pdf','log']);
  return text.replace(candidates,(whole)=>{
    const label=trimLinkPunctuation(whole);const explicit=/^https?:\/\//i.test(label);
    const href=explicit?label:'https://'+label;
    if(!explicit){
      try{if(fileExtensions.has(new URL(href).hostname.split('.').at(-1).toLowerCase()))return whole;}catch{return whole;}
    }
    const html=webLink(href,label);
    return html?hold(html,label)+whole.slice(label.length):whole;
  });
}
// Markdown is escaped. Only locally uploaded raster images are rendered.
function inline(text, visual = false) {
  const slots=[];
  const hold=(html, original) => {
    slots.push(visual && original !== undefined
      ? '<span contenteditable="false" data-markdown="'+esc(original)+'">'+html+'</span>'
      : html);
    return '\x01'+(slots.length-1)+'\x02';};
  text=String(text).replace(/[\x01\x02]/g,'');
  text=text.replace(/`([^`]+)`/g,(_,code)=>hold('<code>'+esc(code)+'</code>',_));
  text=text.replace(/\[\[([^\]\n]+)\]\]/g,(_,body)=>{
    const [path,...label]=body.split('|');
    return hold('<a href="'+esc(pageURL(path.trim()))+'">'+esc(label.join('|')||path)+'</a>',_);
  });
  text=text.replace(/(!?)\[([^\]\n]*)\]\((<[^>]+>|[^)\n]+)\)/g,(_,image,label,target)=>{
    target=target.replace(/^<|>$/g,'').trim();
    if(image){
      if(/^\/uploads\/[0-9a-f]{32}\.(png|jpg|gif|webp)$/.test(target))return hold('<a class="wiki-image-link" href="'+esc(target)+'" target="_blank" rel="noopener noreferrer"><img src="'+esc(target)+'" alt="'+esc(label)+'" loading="lazy" decoding="async"></a>',_);
      return hold('<span class="image-placeholder">'+esc(label||'Image')+(target.startsWith('homewiki-upload:')?'':' (use an uploaded image)')+'</span>',_);
    }
    let href, external=false;
    if(/^https?:\/\//i.test(target)) {href=target; external=true;}
    else if(/^mailto:/i.test(target)) href=target;
    else if(/^[a-z][a-z\d+.-]*:/i.test(target)||target.startsWith('//')) return hold(esc(label),_);
    else if(target.startsWith('#')) href=target;
    else {
      const pageTarget=target.startsWith('/page/')?target.slice(5):target;
      let decoded; try {decoded=decodeURIComponent(pageTarget);} catch {decoded=pageTarget;}
      const root=decoded.startsWith('/');
      const parts=root?[]:(state.page?.path.split('/').slice(0,-1)||[]);
      for(const part of decoded.replace(/^\//,'').replace(/\.md$/i,'').split('/')) {
        if(part==='..') parts.pop(); else if(part && part!=='.') parts.push(part);
      }
      href=pageURL(parts.join('/'));
    }
    return hold('<a href="'+esc(href)+'"'+(external?' target="_blank" rel="noopener noreferrer"':'')+'>'+esc(label)+'</a>',_);
  });
  text=autoLinks(text,hold);
  text=esc(text).replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>').replace(/__(.+?)__/g,'<strong>$1</strong>').replace(/\*([^*]+)\*/g,'<em>$1</em>').replace(/~~(.+?)~~/g,'<del>$1</del>');
  return text.replace(/\x01(\d+)\x02/g,(_,i)=>slots[Number(i)]||'');
}
function expandListIndent(line){
  return line.replace(/^[ \t]*/,prefix=>{let width=0;for(const char of prefix)width+=char==='\t'?4-width%4:1;return ' '.repeat(width);});
}
function listItem(line){
  const expanded=expandListIndent(line);
  const match=expanded.match(/^( *)([-*+]|\d{1,9}[.)])(?:([ \t]+)(.*)|$)/);
  if(!match)return null;
  return {indent:match[1].length,ordered:/^\d/.test(match[2]),number:parseInt(match[2],10),body:match[4]||'',contentIndent:match[1].length+match[2].length+(match[3]?.length||1)};
}
function renderList(lines,start,depth){
  const first=listItem(lines[start]);const tag=first.ordered?'ol':'ul';
  let html='<'+tag+(first.ordered&&first.number!==1?' start="'+first.number+'"':'')+'>';let i=start;
  while(i<lines.length){
    const item=listItem(lines[i]);
    if(!item||item.indent!==first.indent||item.ordered!==first.ordered)break;
    const body=[item.body];i++;
    while(i<lines.length){
      const line=expandListIndent(lines[i]);
      if(!line.trim()){
        let next=i+1;while(next<lines.length&&!lines[next].trim())next++;
        if(next===lines.length)break;
        const following=expandListIndent(lines[next]);const nextItem=listItem(following);
        const indent=following.match(/^ */)[0].length;
        if(indent<=first.indent&&!(nextItem&&nextItem.indent===first.indent&&nextItem.ordered===first.ordered))break;
        body.push('');i++;continue;
      }
      const indent=line.match(/^ */)[0].length;
      if(indent<=first.indent)break;
      body.push(line.slice(Math.min(indent,item.contentIndent)));i++;
    }
    const task=body[0].match(/^\[([ xX])\](?:\s+(.*)|$)/);
    if(task)body[0]=task[2]||'';
    let content=depth>=32?'<pre><code>'+esc(body.join('\n'))+'</code></pre>':markdown(body.join('\n'),false,depth+1);
    if(task){const checkbox='<input class="task-box" type="checkbox" disabled '+(task[1]!==' '?'checked':'')+'>';content=content.startsWith('<p>')?'<p>'+checkbox+content.slice(3):checkbox+content;}
    html+='<li>'+content+'</li>';
  }
  return {html:html+'</'+tag+'>',next:i};
}
function markdown(source, hideTitle=false, depth=0, onBlock) {
  const lines=source.replace(/\r\n/g,'\n').split('\n'); let html='', i=0, first=true;
  const special=line=>listItem(line)||/^(#{1,6})\s|^\s*```|^\s*~~~|^>\s?|^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line);
  while(i<lines.length) {
    let line=lines[i];
    if(!line.trim()){i++;continue;}
    const start = i;
    const previousLength = html.length;
    const capture = editable => onBlock?.(start, i, html.slice(previousLength), editable);
    const fence=line.match(/^\s*(```+|~~~+)(.*)$/);
    if(fence){let code=[];i++;while(i<lines.length&&!lines[i].startsWith(fence[1]))code.push(lines[i++]);i++;html+='<pre><code>'+esc(code.join('\n'))+'</code></pre>';capture(false);first=false;continue;}
    const heading=line.match(/^(#{1,6})\s+(.+)$/);
    if(heading){const level=heading[1].length;const id=heading[2].toLowerCase().replace(/[^\p{L}\p{N}\s-]/gu,'').replace(/\s+/g,'-');if(!(first&&hideTitle&&level===1))html+=`<h${level} id="${esc(id)}">${inline(heading[2], Boolean(onBlock))}</h${level}>`;i++;capture(true);first=false;continue;}
    if(/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)){html+='<hr>';i++;capture(false);first=false;continue;}
    if(/^>/.test(line)){const block=[];while(i<lines.length&&/^>/.test(lines[i]))block.push(lines[i++].replace(/^>\s?/,''));html+='<blockquote>'+markdown(block.join('\n'))+'</blockquote>';capture(false);first=false;continue;}
    if(line.includes('|')&&i+1<lines.length&&/^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[i+1])) {
      const cells=s=>s.trim().replace(/^\||\|$/g,'').split('|').map(v=>v.trim());
      const headers=cells(line);
      html+='<table><thead><tr>'+headers.map((c,column)=>'<th scope="col"><button type="button" class="table-sort" data-column="'+column+'" title="Sort ascending"><span>'+inline(c).replace(/<\/?a\b[^>]*>/g,'')+'</span><span class="table-sort-indicator" aria-hidden="true">↕</span></button></th>').join('')+'</tr></thead><tbody>';i+=2;
      while(i<lines.length&&lines[i].includes('|')&&lines[i].trim()){
        const row=cells(lines[i++]);
        html+='<tr>'+headers.map((_,column)=>'<td>'+inline(row[column]??'')+'</td>').join('')+'</tr>';
      }
      html+='</tbody></table>';capture(false);first=false;continue;
    }
    if(listItem(line)){const list=renderList(lines,i,depth);html+=list.html;i=list.next;capture(false);first=false;continue;}
    let paragraph=[line];i++;
    while(i<lines.length&&lines[i].trim()&&!special(lines[i])&&!(lines[i].includes('|')&&i+1<lines.length&&/^\s*\|?\s*:?-{3,}/.test(lines[i+1])))paragraph.push(lines[i++]);
    html+='<p>'+paragraph.map(text => inline(text, Boolean(onBlock))).join('<br>')+'</p>';capture(true);first=false;
  }
  return html;
}
function sortMarkdownTable(button){
  const table=button.closest('table');const header=button.closest('th');
  const column=Number(button.dataset.column);const descending=header.getAttribute('aria-sort')==='ascending';
  const body=table.tBodies[0];if(!body)return;
  const rows=Array.from(body.rows,(row,index)=>({row,index,value:row.cells[column]?.textContent.trim()||''}));
  const nonempty=rows.map(item=>item.value).filter(Boolean);
  const numberPattern=/^[+-]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i;
  const number=value=>Number(value.replaceAll(',',''));
  const numeric=nonempty.length>0&&nonempty.every(value=>numberPattern.test(value)&&Number.isFinite(number(value)));
  const collator=new Intl.Collator(undefined,{numeric:true,sensitivity:'base'});
  rows.sort((a,b)=>{
    // Keep blanks last in either direction, and preserve ties' current order.
    if(!a.value||!b.value)return Number(!a.value)-Number(!b.value)||a.index-b.index;
    const compared=numeric?number(a.value)-number(b.value):collator.compare(a.value,b.value);
    return (descending?-compared:compared)||a.index-b.index;
  });
  for(const cell of table.tHead.rows[0].cells){
    cell.removeAttribute('aria-sort');const control=cell.querySelector('.table-sort');
    if(control){control.title='Sort ascending';control.querySelector('.table-sort-indicator').textContent='↕';}
  }
  header.setAttribute('aria-sort',descending?'descending':'ascending');
  button.title=descending?'Sort ascending':'Sort descending';button.querySelector('.table-sort-indicator').textContent=descending?'↓':'↑';
  body.append(...rows.map(item=>item.row));
}
document.addEventListener('click',event=>{
  const button=event.target.closest('.markdown button.table-sort');
  if(button && !event.defaultPrevented)sortMarkdownTable(button);
});
function notice(message, action, handler) {
  const box=$('notice');box.replaceChildren();box.hidden=!message;
  if(message)box.append(document.createTextNode(message));
  if(action){const b=document.createElement('button');b.textContent=action;b.onclick=handler;box.append(b);}
}
function setTheme(theme) {document.documentElement.dataset.theme=theme;$('theme-label').textContent=theme==='dark'?'Light appearance':'Dark appearance';setStored('homewiki:theme',theme);}
setTheme(stored('homewiki:theme')||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'));
if(stored('homewiki:sidebar')==='hidden'||innerWidth<721)document.body.classList.add('sidebar-hidden');
function sidebar(hidden){document.body.classList.toggle('sidebar-hidden',hidden);setStored('homewiki:sidebar',hidden?'hidden':'shown');}
$('collapse').onclick=()=>sidebar(true);$('expand').onclick=()=>sidebar(false);
$('sidebar-shade').onclick=()=>{sidebar(true);$('expand').focus({preventScroll:true});};
$('theme-toggle').onclick=()=>setTheme(document.documentElement.dataset.theme==='dark'?'light':'dark');

function renderTree(){
  if(draggedPage)return;
  const root={children:new Map()};
  for(const page of state.pages){let node=root;const parts=page.path.split('/');parts.forEach((part,index)=>{if(!node.children.has(part))node.children.set(part,{name:part,path:parts.slice(0,index+1).join('/'),children:new Map()});node=node.children.get(part);});node.page=page;}
  $('tree').replaceChildren();$('page-count').textContent=state.pages.length;
  function render(parent,node,depth){
    const parentPath=node.path||'';
    const saved=Object.hasOwn(state.pageOrder.parents,parentPath)?state.pageOrder.parents[parentPath]:[];
    const ranks=new Map(saved.map((path,index)=>[path,index]));
    const children=[...node.children.values()].sort((a,b)=>(ranks.get(a.path)??saved.length)-(ranks.get(b.path)??saved.length));
    for(const child of children){
      const row=document.createElement('div');row.className='tree-row'+(child.path===state.page?.path?' active':'');row.style.paddingLeft=(depth*15+3)+'px';
      row.dataset.path=child.path;row.dataset.parent=parentPath;row.draggable=true;row.title='Drag to reorder within this folder. Alt+↑/↓ also moves this item.';
      const toggle=document.createElement('button');toggle.className='tree-toggle';toggle.type='button';row.append(toggle);
      const collapsible=depth===0&&child.children.size>0;
      let collapsed=collapsible&&stored('homewiki:folder:'+child.path)==='closed';
      const sub=document.createElement('div');
      if(collapsible){toggle.textContent=collapsed?'▸':'▾';toggle.setAttribute('aria-label','Toggle '+child.name);toggle.setAttribute('aria-expanded',String(!collapsed));sub.hidden=collapsed;toggle.onclick=()=>{collapsed=!collapsed;sub.hidden=collapsed;toggle.textContent=collapsed?'▸':'▾';toggle.setAttribute('aria-expanded',String(!collapsed));setStored('homewiki:folder:'+child.path,collapsed?'closed':'open');};}else{toggle.textContent='';toggle.disabled=true;toggle.tabIndex=-1;}
      if(child.page){const a=document.createElement('a');a.href=pageURL(child.path);a.innerHTML='<span>'+esc(child.name)+'</span>';a.title=child.path;if(child.path===state.page?.path)a.setAttribute('aria-current','page');row.append(a);}else{const label=document.createElement('span');label.className='folder-name';label.textContent=child.name;row.append(label);}
      parent.append(row);if(child.children.size){parent.append(sub);render(sub,child,depth+1);}
    }
  }
  render($('tree'),root,0);
}
async function refreshPages(){
  const epoch=orderEpoch;const [pages,order]=await Promise.all([api('pages'),api('order')]);
  state.pages=pages;if(epoch===orderEpoch&&!orderSaving)state.pageOrder=order;
  renderTree();if(state.recent)renderRecent();
}
function siblingRows(parent){return [...$('tree').querySelectorAll('.tree-row')].filter(row=>row.dataset.parent===parent);}
function clearOrderDrag(){
  draggedPage=null;
  for(const row of $('tree').querySelectorAll('.dragging,.drop-before,.drop-after'))row.classList.remove('dragging','drop-before','drop-after');
}
function focusTreePage(path){
  const row=[...$('tree').querySelectorAll('.tree-row')].find(row=>row.dataset.path===path);
  row?.querySelector('a,button:not(:disabled)')?.focus();
}
async function savePageOrder(parent,children,revision,focusPath=null){
  if(orderSaving)return;
  orderSaving=true;orderEpoch++;const before=state.pageOrder;
  state.pageOrder={parents:{...before.parents,[parent]:children},revision:before.revision};renderTree();
  if(focusPath)focusTreePage(focusPath);
  try{
    state.pageOrder=await api('order',{parent,children,revision});toast('Page order saved');
  }catch(error){
    state.pageOrder=before;
    try{state.pageOrder=await api('order');}catch{ /* Keep the last known order. */ }
    toast(error.message);
  }finally{
    orderSaving=false;orderEpoch++;renderTree();
    if(focusPath)focusTreePage(focusPath);
  }
}
$('tree').addEventListener('dragstart',event=>{
  const row=event.target.closest('.tree-row');
  if(!row||orderSaving||event.target.closest('button')){event.preventDefault();return;}
  draggedPage={path:row.dataset.path,parent:row.dataset.parent,revision:state.pageOrder.revision};
  row.classList.add('dragging');event.dataTransfer.effectAllowed='move';
  event.dataTransfer.setData('application/x-homewiki-page',draggedPage.path);
  event.dataTransfer.setData('text/plain',draggedPage.path);
});
$('tree').addEventListener('dragover',event=>{
  if(!draggedPage)return;
  event.preventDefault();
  const bounds=$('tree').getBoundingClientRect();
  if(event.clientY>bounds.bottom-24)$('tree').scrollTop+=12;
  else if(event.clientY<bounds.top+24)$('tree').scrollTop-=12;
  const row=event.target.closest('.tree-row');
  for(const target of $('tree').querySelectorAll('.drop-before,.drop-after'))target.classList.remove('drop-before','drop-after');
  if(!row||row.dataset.parent!==draggedPage.parent||row.dataset.path===draggedPage.path){event.dataTransfer.dropEffect='none';return;}
  event.preventDefault();event.dataTransfer.dropEffect='move';
  const box=row.getBoundingClientRect();row.classList.add(event.clientY<box.top+box.height/2?'drop-before':'drop-after');
});
$('tree').addEventListener('drop',event=>{
  if(!draggedPage)return;
  event.preventDefault();const row=event.target.closest('.tree-row');const moved=draggedPage;
  if(!row||row.dataset.parent!==moved.parent||row.dataset.path===moved.path){clearOrderDrag();renderTree();return;}
  const paths=siblingRows(moved.parent).map(item=>item.dataset.path).filter(path=>path!==moved.path);
  const box=row.getBoundingClientRect();const after=event.clientY>=box.top+box.height/2;
  paths.splice(paths.indexOf(row.dataset.path)+(after?1:0),0,moved.path);
  clearOrderDrag();savePageOrder(moved.parent,paths,moved.revision);
});
$('tree').addEventListener('dragend',()=>{if(draggedPage){clearOrderDrag();renderTree();}});
document.addEventListener('dragover',event=>{if(draggedPage)event.preventDefault();});
document.addEventListener('drop',event=>{if(draggedPage){event.preventDefault();clearOrderDrag();renderTree();}});
$('tree').addEventListener('keydown',event=>{
  if(!event.altKey||!['ArrowUp','ArrowDown'].includes(event.key))return;
  const row=event.target.closest('.tree-row');if(!row)return;
  event.preventDefault();if(orderSaving)return;
  const paths=siblingRows(row.dataset.parent).map(item=>item.dataset.path);
  const index=paths.indexOf(row.dataset.path),next=index+(event.key==='ArrowUp'?-1:1);
  if(next<0||next>=paths.length)return;
  [paths[index],paths[next]]=[paths[next],paths[index]];
  savePageOrder(row.dataset.parent,paths,state.pageOrder.revision,row.dataset.path);
});
function renderBreadcrumb(path){
  const parts=path.split('/');
  $('breadcrumb').replaceChildren();
  parts.forEach((part,index)=>{
    if(index){const separator=document.createElement('span');separator.className='breadcrumb-separator';separator.textContent='/';separator.setAttribute('aria-hidden','true');$('breadcrumb').append(separator);}
    const current=index===parts.length-1;
    const item=document.createElement(current?'span':'a');item.textContent=part;
    if(current)item.setAttribute('aria-current','page');
    else{item.href=pageURL(parts.slice(0,index+1).join('/'));item.title='Go to '+parts.slice(0,index+1).join('/');}
    $('breadcrumb').append(item);
  });
}
function editDate(value){return new Date(value).toLocaleString(undefined,{dateStyle:'medium',timeStyle:'short'});}
function renderRecent(){
  $('page-title').textContent='Recently edited';renderBreadcrumb('Recently edited');document.title='Recently edited · Homewiki';
  $('reader').hidden=false;$('editor').hidden=true;$('edit-page').hidden=true;$('more').hidden=true;$('actions').hidden=true;$('page-footer').hidden=true;
  $('recently-edited').setAttribute('aria-current','page');
  const content=$('content');content.replaceChildren();
  const intro=document.createElement('p');intro.className='recent-intro';intro.textContent='Your latest saved edits, newest first.';content.append(intro);
  const list=document.createElement('div');list.className='recent-list';
  const pages=[...state.pages].sort((a,b)=>new Date(b.modified)-new Date(a.modified)||a.path.localeCompare(b.path));
  for(const page of pages){
    const link=document.createElement('a');link.href=pageURL(page.path);link.className='recent-page';
    const label=document.createElement('span');const title=document.createElement('strong');title.textContent=page.path.split('/').pop();label.append(title);
    const path=document.createElement('small');path.textContent=page.path;label.append(path);
    const time=document.createElement('time');time.dateTime=page.modified;time.textContent=editDate(page.modified);time.title=new Date(page.modified).toString();
    link.append(label,time);list.append(link);
  }
  if(!pages.length){const empty=document.createElement('p');empty.textContent='No pages yet. Create a page to get started.';list.append(empty);}
  content.append(list);renderTree();
}
function navigateRecent(){
  leaveBookmarks();
  preserveDraft();const turn=++navigating;state.loading=false;state.recent=true;state.page=null;state.editing=false;state.dirty=false;notice('');renderRecent();
  if(innerWidth<721)sidebar(true);window.scrollTo(0,0);
  refreshPages().catch(error=>{if(turn===navigating)notice(error.message);});
}
function paintPage(){
  const page=state.page;if(!page)return;
  $('more').hidden=false;$('page-footer').hidden=false;$('recently-edited').removeAttribute('aria-current');
  $('last-edited').dateTime=page.modified;$('last-edited').textContent='Last edited '+editDate(page.modified);$('last-edited').title=new Date(page.modified).toString();
  const name=page.path.split('/').pop();
  const firstLine=page.content.replace(/\r\n/g,'\n').split('\n').find(line=>line.trim())||'';
  const heading=firstLine.match(/^#\s+(.+)$/);
  if(heading&&heading[1].trim())$('page-title').innerHTML=inline(heading[1]);else $('page-title').textContent=name;
  renderBreadcrumb(page.path);document.title=($('page-title').textContent.trim()||name)+' · Homewiki';
  $('content').innerHTML=markdown(page.content,true);$('word-count').textContent=page.content.trim()?page.content.trim().split(/\s+/).length+' words':'0 words';
  $('reader').hidden=state.editing;$('editor').hidden=!state.editing;$('edit-page').hidden=state.editing;
  renderTree();syncUploadControls();
}
function preserveDraft(){
  if(!state.editing||!state.page)return;
  state.dirty=$('source').value!==state.page.content||state.base!==state.page.revision;
  if(state.dirty){const ok=setStored(draftKey(state.page.path),JSON.stringify({content:$('source').value,revision:state.base,updated:Date.now()}));$('draft-status').textContent=ok?'Draft saved in this browser':'Browser storage unavailable — keep this tab open';}
  else{removeDraft(state.page.path);$('draft-status').textContent='All changes saved';}
}
async function navigate(path){
  leaveBookmarks();
  const turn=++navigating;preserveDraft();state.loading=true;state.recent=false;$('recently-edited').removeAttribute('aria-current');
  try{const page=await api('page?path='+encodeURIComponent(path));if(turn!==navigating)return;state.loading=false;
    state.page=page;state.editing=false;state.dirty=false;state.remote=false;notice('');paintPage();
    const saved=draft(path);if(saved&&saved.content!==page.content)startEdit(saved);
    else if(saved)removeDraft(path);
    if(innerWidth<721)sidebar(true);window.scrollTo(0,0);
  }catch(error){if(turn!==navigating)return;state.loading=false;state.page=null;state.editing=false;state.dirty=false;$('reader').hidden=false;$('editor').hidden=true;$('edit-page').hidden=true;$('page-title').textContent=path||'Page not found';renderBreadcrumb(path);$('content').textContent=error.message;$('word-count').textContent='';$('page-footer').hidden=true;$('more').hidden=true;notice(error.status===404?'This page does not exist yet.':'Unable to load this page.',error.status===404?'Create page':null,()=>createDialog(path));renderTree();}
}
function route(){
  const bookmarkPath=location.pathname.replace(/\/+$/,'');
  if(['/bookmarks','/bookmarks/tags','/bookmarks/domains','/bookmarks/add','/bookmarks/tools'].includes(bookmarkPath)&&!location.hash.startsWith('#/page/')){
    if(bookmarkPath!==location.pathname)history.replaceState(null,'',bookmarkPath+location.search+location.hash);
    navigateBookmarks();return;
  }
  if(location.pathname==='/recently-edited'&&!location.hash.startsWith('#/page/')){navigateRecent();return;}
  let path='Home';
  try{
    const legacyHash=location.hash.startsWith('#/page/');
    if(legacyHash)path=decodeURIComponent(location.hash.slice(7));
    else path=decodeURIComponent(location.pathname.replace(/^\/page\//,'/').slice(1).replace(/\/+$/,''))||'Home';
    const canonical=pageURL(path);
    if(legacyHash||(location.pathname!=='/'&&location.pathname!==canonical))history.replaceState(null,'',canonical+location.search+(legacyHash?'':location.hash));
  }catch{toast('Invalid page link');}
  navigate(path);
}
window.addEventListener('popstate',route);
window.addEventListener('hashchange',()=>{if(location.hash.startsWith('#/page/'))route();});
function go(path){const url=pageURL(path);if(location.pathname!==url||location.hash)history.pushState(null,'',url);route();}
document.addEventListener('click',event=>{
  if(event.defaultPrevented||event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
  const link=event.target.closest('a[href]');if(!link||link.hasAttribute('download')||(link.target&&link.target!=='_self'))return;
  const url=new URL(link.href,location.href);
  const root=decodeURIComponent(url.pathname.split('/')[1]||'');
  const wikiPath=!reservedPageRoots.has(root)||url.pathname.startsWith('/page/')||url.pathname==='/recently-edited'||url.pathname==='/bookmarks'||url.pathname.startsWith('/bookmarks/');
  const localAnchor=url.pathname===location.pathname&&url.hash&&!url.hash.startsWith('#/page/');
  if(url.origin===location.origin&&wikiPath&&!localAnchor){
    event.preventDefault();history.pushState(null,'',url.pathname+url.search+url.hash);route();
  }
});
function startEdit(saved=null){
  if(!state.page)return;state.editing=true;state.base=saved?saved.revision:state.page.revision;state.remote=Boolean(saved&&saved.revision!==state.page.revision);let interrupted=false;$('source').value=(saved?saved.content:state.page.content).replace(/!\[Uploading [^\]\n]*\]\(homewiki-upload:[a-z0-9-]+\)/g,marker=>{if(pendingUploads.has(marker))return marker;interrupted=true;return '';});setPreview(false);paintPage();preserveDraft();
  if(saved)notice((interrupted?'An image upload was interrupted; drop the image again. ':'')+(state.remote?'Recovered your draft. The file has changed since this draft began.':'Recovered your unsaved draft from this browser.'),state.remote?'Compare versions':null,compare);
  $('source').focus();
}
function uploadCount(path){return [...pendingUploads.values()].filter(value=>value===path).length;}
function syncUploadControls(){
  const count=uploadCount(state.page?.path);
  $('save-page').disabled=count>0;
  $('image-status').textContent=count?'Uploading '+count+' image'+(count===1?'…':'s…'):'Drop or paste images here · PNG, JPEG, GIF, WebP · up to 10 MB each';
}
function finishUpload(path,marker,replacement){
  pendingUploads.delete(marker);
  if(state.page?.path===path&&state.editing){
    const source=$('source');
    if(source.value.includes(marker)){const start=source.value.indexOf(marker);const cursor=source.selectionStart;source.setRangeText(replacement,start,start+marker.length,'preserve');if(cursor>=start+marker.length){const next=cursor+replacement.length-marker.length;source.setSelectionRange(next,next);}preserveDraft();refreshEditorUpload(marker,replacement);}
  }else{
    try{const saved=JSON.parse(stored(draftKey(path),'null'));if(saved?.content.includes(marker)){saved.content=saved.content.replace(marker,replacement);saved.updated=Date.now();setStored(draftKey(path),JSON.stringify(saved));}}catch{ /* Preserve any existing draft if storage is unavailable. */ }
  }
  syncUploadControls();
}
async function addImages(files){
  if(state.loading){toast('Wait for the page to finish loading, then add images.');return;}
  if(!state.page){toast('Open a page before adding images.');return;}
  const valid=[];
  for(const file of files){
    if(file.size===0||file.size>10*1024*1024){toast('Images must be between 1 byte and 10 MB: '+file.name);continue;}
    if(!/\.(png|jpe?g|gif|webp)$/i.test(file.name)&&!/^image\/(png|jpeg|gif|webp)$/.test(file.type)){toast('Choose a PNG, JPEG, GIF, or WebP image.');continue;}
    valid.push(file);
  }
  if(!valid.length)return;
  if(!state.editing)startEdit(draft(state.page.path));
  const path=state.page.path;const source=$('source');const entries=valid.map(file=>{
    const label=(file.name.replace(/\.[^.]+$/,'').replace(/[\[\]\\\r\n]/g,' ').trim()||'Image');
    const id=Date.now().toString(36)+'-'+Math.random().toString(36).slice(2);
    const marker='![Uploading '+label+'…](homewiki-upload:'+id+')';pendingUploads.set(marker,path);return {file,label,marker};
  });
  const [start, end] = editorInsertRange();
  source.setRangeText('\n\n'+entries.map(entry=>entry.marker).join('\n\n')+'\n\n',start,end,'end');preserveDraft();syncUploadControls();refreshEditorOutput();
  for(const {file,label,marker} of entries){
    try{
      const response=await fetch('/api/upload',{method:'POST',headers:{'X-Homewiki':'1','Content-Type':'application/octet-stream'},body:file});
      const result=await response.json();if(!response.ok)throw new Error(result.error||'Image upload failed');
      finishUpload(path,marker,'!['+label+']('+result.url+')');
    }catch(error){finishUpload(path,marker,'');toast('Could not add '+file.name+': '+error.message);}
  }
}
$('add-image').onclick=()=>$('image-files').click();
$('image-files').onchange=()=>{addImages(Array.from($('image-files').files));$('image-files').value='';};
const fileDrag=event=>Array.from(event.dataTransfer?.types||[]).includes('Files');
document.addEventListener('dragover',event=>{if(fileDrag(event))event.preventDefault();});
document.addEventListener('drop',event=>{if(fileDrag(event))event.preventDefault();});
$('main').addEventListener('dragover',event=>{if(fileDrag(event)){event.preventDefault();event.dataTransfer.dropEffect='copy';$('main').classList.add('image-drop-target');}});
$('main').addEventListener('dragleave',event=>{if(!$('main').contains(event.relatedTarget))$('main').classList.remove('image-drop-target');});
$('main').addEventListener('drop',event=>{if(fileDrag(event)){event.preventDefault();$('main').classList.remove('image-drop-target');addImages(Array.from(event.dataTransfer.files));}});
$('source').addEventListener('paste',event=>{const files=Array.from(event.clipboardData?.files||[]).filter(file=>file.type.startsWith('image/'));if(files.length){event.preventDefault();addImages(files);}});
function setPreview(on) {
  setEditorMode(on ? 'preview' : 'write');
}
$('edit-page').onclick=()=>startEdit();$('tab-write').onclick=()=>setPreview(false);$('tab-preview').onclick=()=>setPreview(true);
$('source').addEventListener('input',preserveDraft);
$('source').addEventListener('keydown',e=>{if(e.key==='Tab'){e.preventDefault();$('source').setRangeText('  ',$('source').selectionStart,$('source').selectionEnd,'end');preserveDraft();}});
$('cancel-edit').onclick=()=>{preserveDraft();state.editing=false;paintPage();notice(state.dirty?'Your draft is saved in this browser. Reopen the editor to continue.':'',state.dirty?'Discard draft':null,discardDialog);};
$('edit-page').onclick=()=>startEdit(draft(state.page.path));
async function save(){
  if(!state.editing||!state.page)return;
  if(uploadCount(state.page.path)){toast('Wait for image uploads to finish before saving.');return;}
  const path=state.page.path, content=$('source').value,revision=state.base;
  $('save-page').disabled=true;
  try{const page=await api('save',{path,content,revision});removeDraft(path);
    if(state.page?.path===path){state.page=page;state.base=page.revision;state.remote=false;state.dirty=false;if($('source').value!==content)preserveDraft();else{state.editing=false;notice('');paintPage();}}await refreshPages();toast('Page saved');
  }catch(error){if(error.status===409){state.remote=true;notice(error.message,'Compare versions',compare);}else notice(error.message);preserveDraft();}
  finally{syncUploadControls();}
}
$('save-page').onclick=save;
function modal(title){$('modal-title').textContent=title;$('modal-body').replaceChildren();if(!$('modal').open)$('modal').showModal();return $('modal-body');}
function closeModal(){$('modal').close();}
$('modal-close').onclick=closeModal;
$('modal').addEventListener('click',e=>{if(e.target===$('modal')){const r=$('modal').getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)closeModal();}});
function errorInto(body,error){let p=body.querySelector('.error-text');if(!p){p=document.createElement('p');p.className='error-text';p.setAttribute('role','alert');body.append(p);}p.textContent=error.message;}
function formDialog(title,description,value,submitLabel,callback){
  const body=modal(title);const p=document.createElement('p');p.textContent=description;body.append(p);
  const form=document.createElement('form');form.innerHTML='<label for="page-path">Page path</label><input id="page-path" type="text" required maxlength="400" autocomplete="off"><div class="dialog-buttons"><button type="button" class="subtle-button">Cancel</button><button class="primary-button" type="submit">'+esc(submitLabel)+'</button></div>';body.append(form);const input=form.querySelector('input');input.value=value;form.querySelector('button').onclick=closeModal;
  form.onsubmit=async e=>{e.preventDefault();const button=form.querySelector('[type=submit]');button.disabled=true;try{await callback(input.value.trim());}catch(error){errorInto(body,error);}finally{button.disabled=false;}};input.focus();input.select();
}
function createDialog(initial='',parent=''){
  formDialog(parent?'Create a subpage':'Create a page',parent?'Create under “'+parent+'”. Use / to nest further.':'Use / to nest pages. For example: Games/Completed games.',initial,'Create page',async name=>{
    const path=parent?parent+'/'+name:name;
    await api('save',{path,content:'# '+path.split('/').pop()+'\n\n',revision:null});
    if(parent)setStored('homewiki:folder:'+parent.split('/')[0],'open');
    await refreshPages();closeModal();go(path);
  });
  if(parent){$('modal-body').querySelector('label').textContent='Subpage name';$('page-path').placeholder='New subpage';}
}
$('new-page').onclick=()=>createDialog();
$('new-subpage').onclick=()=>{
  $('actions').hidden=true;$('more').setAttribute('aria-expanded','false');
  if(!state.page||state.loading)return;
  preserveDraft();createDialog('',state.page.path);
};
$('more').onclick=()=>{$('actions').hidden=!$('actions').hidden;$('more').setAttribute('aria-expanded',String(!$('actions').hidden));};
document.addEventListener('click',e=>{if(!e.target.closest('.header-actions')){$('actions').hidden=true;$('more').setAttribute('aria-expanded','false');}});
function requireClean(){preserveDraft();if(state.dirty||draft(state.page?.path)){toast('Save or discard your draft before renaming or deleting.');return false;}return !!state.page;}
$('rename-page').onclick=()=>{
  $('actions').hidden=true;if(!requireClean())return;const page=state.page;
  formDialog('Rename / move page','Enter a new path for this Markdown file. Nested pages and links to the old path remain unchanged.',page.path,'Rename page',async target=>{const renamed=await api('rename',{path:page.path,target,revision:page.revision});await refreshPages();closeModal();go(renamed.path);toast('Page renamed');});
};
$('delete-page').onclick=()=>{
  $('actions').hidden=true;if(!requireClean())return;const page=state.page;const body=modal('Move page to trash?');const p=document.createElement('p');p.textContent='“'+page.path+'” will be recoverable from Recently deleted. Nested pages remain in place.';body.append(p);
  const row=document.createElement('div');row.className='dialog-buttons';row.innerHTML='<button class="subtle-button">Cancel</button><button class="primary-button">Move to trash</button>';body.append(row);row.children[0].onclick=closeModal;row.children[1].onclick=async()=>{row.children[1].disabled=true;try{await api('delete',{path:page.path,revision:page.revision});await refreshPages();closeModal();go(state.pages.find(p=>p.path==='Home')?.path||state.pages[0]?.path||'Home');toast('Page moved to trash');}catch(error){errorInto(body,error);row.children[1].disabled=false;}};
};
function historyDialog(){
  const path=state.page?.path;if(!path||state.loading)return;
  preserveDraft();
  const body=modal('Page history');
  const view=document.createElement('div');view.className='history-view';body.append(view);
  const intro=document.createElement('p');intro.textContent='Saved versions of '+path+'. Current content is kept in history when you restore an earlier version.';view.append(intro);
  const loading=document.createElement('p');loading.textContent='Loading versions…';view.append(loading);
  const active=()=>view.isConnected&&$('modal').open;
  const versionDate=value=>new Date(value).toLocaleString(undefined,{dateStyle:'medium',timeStyle:'medium'});
  api('history?path='+encodeURIComponent(path)).then(result=>{
    if(!active())return;
    loading.remove();
    if(!result.versions.length){
      const empty=document.createElement('p');empty.className='empty';empty.textContent='No earlier versions yet. The current page will be kept when you save a change.';view.append(empty);return;
    }
    const label=document.createElement('label');label.htmlFor='history-version';label.textContent='Saved version';
    const select=document.createElement('select');select.id='history-version';
    result.versions.forEach((version,index)=>{
      const option=document.createElement('option');option.value=version.id;
      option.textContent=versionDate(version.modified)+' · '+version.bytes.toLocaleString()+' bytes'+(index===0?' · most recent':'');
      select.append(option);
    });
    const current=document.createElement('p');current.className='history-current';
    const tabs=document.createElement('div');tabs.className='history-tabs';tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','Version comparison');
    const changes=document.createElement('button');changes.type='button';changes.id='history-changes-tab';changes.textContent='Changes';
    const source=document.createElement('button');source.type='button';source.id='history-source-tab';source.textContent='Saved Markdown';
    for(const tab of [changes,source]){tab.setAttribute('role','tab');tabs.append(tab);}
    changes.setAttribute('aria-controls','history-diff');source.setAttribute('aria-controls','history-content');
    const legend=document.createElement('p');legend.className='history-legend';legend.textContent='− Selected version · + Current page';
    const diff=document.createElement('pre');diff.id='history-diff';diff.className='history-code';diff.setAttribute('role','tabpanel');diff.setAttribute('aria-labelledby',changes.id);diff.tabIndex=0;
    const content=document.createElement('pre');content.id='history-content';content.className='history-code';content.setAttribute('role','tabpanel');content.setAttribute('aria-labelledby',source.id);content.tabIndex=0;
    const controls=document.createElement('div');controls.className='dialog-buttons';
    const restore=document.createElement('button');restore.id='history-restore';restore.type='button';restore.className='primary-button';restore.textContent='Restore this version';restore.disabled=true;controls.append(restore);
    const errorArea=document.createElement('div');
    view.append(label,select,current,tabs,legend,diff,content,errorArea,controls);
    function tab(showSource){
      changes.setAttribute('aria-selected',String(!showSource));source.setAttribute('aria-selected',String(showSource));changes.tabIndex=showSource?-1:0;source.tabIndex=showSource?0:-1;
      diff.hidden=showSource;legend.hidden=showSource;content.hidden=!showSource;
    }
    changes.onclick=()=>tab(false);source.onclick=()=>tab(true);
    tabs.onkeydown=event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();const target=event.key==='Home'?changes:event.key==='End'?source:event.target===changes?source:changes;target.click();target.focus();}};
    tab(false);
    let sequence=0,comparison=null;
    async function loadVersion(){
      const turn=++sequence;comparison=null;restore.disabled=true;errorArea.replaceChildren();diff.textContent='Loading comparison…';content.textContent='';
      try{
        const value=await api('history-version?path='+encodeURIComponent(path)+'&version='+select.value);
        if(!active()||turn!==sequence)return;
        comparison=value;current.textContent='Compared with current page · '+versionDate(value.current.modified);
        content.textContent=value.version.content;diff.replaceChildren();
        for(const line of value.diff){
          const row=document.createElement('span');row.textContent=line+'\n';
          row.className=line.startsWith('+++')||line.startsWith('---')?'diff-header':line.startsWith('+')?'diff-added':line.startsWith('-')?'diff-removed':line.startsWith('@@')?'diff-range':'';
          diff.append(row);
        }
        if(!value.diff.length)diff.textContent=value.version.revision===value.current.revision?'This version matches the current page.':'Only line endings or the final newline differ. See Saved Markdown for the selected content.';
        restore.disabled=value.version.revision===value.current.revision;
      }catch(error){if(active()&&turn===sequence){diff.textContent='Unable to load this version.';errorInto(errorArea,error);}}
    }
    select.onchange=loadVersion;loadVersion();
    restore.onclick=()=>{
      if(!comparison)return;
      preserveDraft();
      if(state.page?.path!==path){toast('Open this page again before restoring a version.');return;}
      if(state.dirty||draft(path)||uploadCount(path)){errorInto(errorArea,new Error('Save or discard your draft before restoring a version.'));return;}
      const selected=comparison;
      const confirmation=modal('Restore this version?');
      const message=document.createElement('p');message.textContent='Restore '+path+' to the version from '+versionDate(selected.version.modified)+'? Its current saved content will remain in page history.';confirmation.append(message);
      const row=document.createElement('div');row.className='dialog-buttons';
      const cancel=document.createElement('button');cancel.className='subtle-button';cancel.textContent='Cancel';cancel.onclick=historyDialog;
      const confirm=document.createElement('button');confirm.className='primary-button';confirm.id='confirm-history-restore';confirm.textContent='Restore version';
      row.append(cancel,confirm);confirmation.append(row);
      confirm.onclick=async()=>{
        confirm.disabled=true;cancel.disabled=true;
        try{
          // Check again immediately before the write; other tabs retain their own drafts.
          preserveDraft();
          if(state.page?.path!==path||state.dirty||draft(path)||uploadCount(path))throw new Error('Save or discard your draft before restoring a version.');
          await api('history-restore',{path,version:selected.version.id,revision:selected.current.revision});
          closeModal();await navigate(path);await refreshPages();toast('Earlier version restored');
        }catch(error){errorInto(confirmation,error);confirm.disabled=false;cancel.disabled=false;}
      };
    };
  }).catch(error=>{if(active()){loading.remove();errorInto(view,error);}});
}
$('page-history').onclick=()=>{$('actions').hidden=true;$('more').setAttribute('aria-expanded','false');historyDialog();};

function discardDialog(){const body=modal('Discard unsaved draft?');body.innerHTML='<p>This removes the browser draft. The saved Markdown file stays as it is.</p><div class="dialog-buttons"><button class="subtle-button">Keep draft</button><button class="primary-button">Discard draft</button></div>';const buttons=body.querySelectorAll('button');buttons[0].onclick=closeModal;buttons[1].onclick=()=>{removeDraft(state.page.path);state.editing=false;state.dirty=false;closeModal();navigate(state.page.path);};}
async function compare(){
  const path=state.page?.path;if(!path)return;preserveDraft();const saved=draft(path);const local=state.editing?$('source').value:(saved?.content||'');const body=modal('Compare conflicting edits');
  body.innerHTML='<p>Your draft is preserved. Copy any changes you want to keep into the editor. “Use merged draft” updates the expected revision; it does not save the page.</p>';
  try{
    const current=await api('page?path='+encodeURIComponent(path));
    const add=(label,content)=>{const l=document.createElement('div');l.className='compare-label';l.textContent=label;const pre=document.createElement('pre');pre.className='compare';pre.textContent=content;body.append(l,pre);};
    add('CURRENT FILE ON DISK',current.content);add('YOUR DRAFT',local);
    const label=document.createElement('label');label.textContent='Merged draft (edit below)';label.className='compare-label';const merged=document.createElement('textarea');merged.className='compare';merged.style.width='100%';merged.style.minHeight='180px';merged.value=local;body.append(label,merged);
    const row=document.createElement('div');row.className='dialog-buttons';row.innerHTML='<button class="subtle-button">Keep editing</button><button class="primary-button">Use merged draft</button>';body.append(row);row.children[0].onclick=closeModal;row.children[1].onclick=()=>{if(state.page?.path!==path)return closeModal();state.page=current;state.editing=true;state.base=current.revision;state.remote=false;$('source').value=merged.value;preserveDraft();setPreview(false);paintPage();notice('Merged draft is ready. Review it, then save.');closeModal();};
  }catch(error){errorInto(body,error);}
  const blob=new Blob([local],{type:'text/markdown'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.className='draft-download';a.textContent='Download your draft as Markdown';a.href=url;a.download=path.split('/').pop()+'.md';body.append(a);$('modal').addEventListener('close',()=>URL.revokeObjectURL(url),{once:true});
}
async function searchDialog(picker=false){
  const body=modal(picker?'Link to a page':'Search your wiki');const input=document.createElement('input');input.type='search';input.placeholder=picker?'Find a page to link…':'Search titles and page contents…';input.setAttribute('aria-label',input.placeholder);body.append(input);const results=document.createElement('div');body.append(results);let sequence=0;
  async function update(){const request=++sequence;try{const pages=await api('pages?q='+encodeURIComponent(input.value));if(request!==sequence)return;results.replaceChildren();if(!pages.length){results.innerHTML='<div class="empty">No pages found.</div>';return;}for(const p of pages){const b=document.createElement('button');b.className='result';b.innerHTML='<strong>▤ '+esc(p.path)+'</strong><small>'+esc(p.excerpt)+'</small>';b.onclick=()=>{closeModal();if(picker){const text='[['+p.path+']]';insertEditorLink(text);}else go(p.path);};results.append(b);}}catch(error){errorInto(body,error);}}
  let debounce;input.oninput=()=>{clearTimeout(debounce);sequence++;results.replaceChildren();debounce=setTimeout(update,130);};input.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();results.querySelector('button')?.click();}};input.focus();await update();
}
$('search-open').onclick=()=>searchDialog();$('link-picker').onclick=()=>searchDialog(true);
$('trash-open').onclick=async()=>{const body=modal('Recently deleted');body.innerHTML='<p>Deleted Markdown files are kept until you remove them from the data folder. Select a page to restore it.</p>';try{const pages=await api('trash');if(!pages.length){body.innerHTML+='<div class="empty">Nothing in the trash. A clean slate.</div>';return;}for(const page of pages){const b=document.createElement('button');b.className='result';b.innerHTML='<strong>↶ '+esc(page.path)+'</strong><small>Deleted '+esc(new Date(page.deleted).toLocaleString())+' · Restore page</small>';b.onclick=async()=>{b.disabled=true;try{const restored=await api('restore',{id:page.id});await refreshPages();closeModal();go(restored.path);toast('Page restored');}catch(error){errorInto(body,error);b.disabled=false;}};body.append(b);}}catch(error){errorInto(body,error);}};
window.addEventListener('beforeunload',e=>{if(pendingUploads.size||(state.editing&&state.dirty)){preserveDraft();e.preventDefault();e.returnValue='';}});
window.addEventListener('storage',e=>{if(e.key?.startsWith(draftPrefix(state.page?.path))&&e.key!==draftKey(state.page?.path)&&state.editing&&e.newValue){notice('Another tab is editing this page. Each tab keeps its own draft; saving checks for conflicts.');}});
document.addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();searchDialog();}if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='s'){e.preventDefault();save();}});
// Refresh idle readers and flag external edits without replacing an editor's base revision.
let checking=false;
async function checkUpdates(){
  if(checking||document.hidden||$('modal').open)return;checking=true;
  try{await refreshPages();if(state.page){const path=state.page.path;const current=await api('page?path='+encodeURIComponent(path));if(state.page?.path!==path)return;if(current.revision===state.page.revision&&current.modified!==state.page.modified&&!state.editing){state.page=current;paintPage();}
    if(current.revision!==state.page.revision){if(state.editing||draft(path)){state.remote=true;notice('This file changed on disk. Your draft is preserved.','Compare versions',compare);}else{state.page=current;paintPage();}}}}
  catch(error){if(error.status===404&&state.editing)notice('This page was moved or deleted on disk. Your draft is preserved.','Download / compare draft',compare);}
  finally{checking=false;}
}
window.addEventListener('focus',checkUpdates);setInterval(checkUpdates,10000);
refreshPages().then(route).catch(error=>notice('Unable to connect to Homewiki: '+error.message));


// Independent of page loading, so a missing version never blocks the wiki.
async function showRunningVersion(){
  const label=$('build-version');
  try{
    const version=await api('version');
    if(!/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(version.revision||'')){label.textContent='Development';return;}
    const link=document.createElement('a');
    link.href='https://github.com/rroller/homewiki/commit/'+version.revision;
    link.textContent=version.revision.slice(0,7)+(version.dirty?'*':'');
    link.title=version.revision+(version.dirty?' · Local checkout has uncommitted changes':'');
    link.setAttribute('aria-label','View running version '+version.revision.slice(0,7)+' on GitHub');
    link.target='_blank';link.rel='noopener noreferrer';label.replaceChildren(link);
  }catch{label.textContent='Version unavailable';}
}
showRunningVersion();
