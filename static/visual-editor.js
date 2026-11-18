'use strict';

const visualBlocks = $('visual-blocks');
let editorMode = 'write';
let visualParts = [];
let visualTrailing = '';
let activeVisualBlock = null;
let visualSelection = null;
const visualSnapshots = new WeakMap();

function setEditorMode(mode) {
  editorMode = mode;
  $('source').hidden = mode !== 'write';
  $('preview').hidden = mode !== 'preview';
  $('visual-editor').hidden = mode !== 'visual';
  for (const name of ['write', 'visual', 'preview']) {
    const button = $('tab-' + name);
    button.classList.toggle('selected', name === mode);
    button.setAttribute('aria-pressed', String(name === mode));
  }
  refreshEditorOutput();
}

function refreshEditorOutput() {
  const source = $('source').value;
  if (editorMode === 'preview') $('preview').innerHTML = markdown(source);
  if (editorMode !== 'visual') return;
  visualBlocks.replaceChildren();
  visualParts = [];
  activeVisualBlock = null;
  visualSelection = null;
  const offsets = [0, ...Array.from(source.matchAll(/\n/g), match => match.index + 1)];
  let previousEnd = 0;
  markdown(source, false, 0, (firstLine, nextLine, html, editable) => {
    const start = offsets[firstLine];
    const end = nextLine < offsets.length ? offsets[nextLine] - 1 : source.length;
    const block = makeVisualBlock(html, editable);
    visualParts.push({block, original: source.slice(start, end), gap: source.slice(previousEnd, start), end});
    previousEnd = end;
  });
  visualTrailing = source.slice(previousEnd);
  if (!visualParts.some(part => part.block.hasAttribute('contenteditable'))) addVisualParagraph(false);
}

function makeVisualBlock(html, editable) {
  const block = document.createElement('div');
  block.className = 'visual-block';
  block.innerHTML = html;
  for (const control of block.querySelectorAll('a, button')) control.tabIndex = -1;
  if (editable) {
    visualSnapshots.set(block, block.innerHTML);
    block.contentEditable = 'true';
    block.setAttribute('role', 'textbox');
    block.setAttribute('aria-multiline', 'true');
    block.setAttribute('aria-label', 'Visual text block');
  } else {
    block.classList.add('visual-locked');
    block.title = 'Edit this block in Markdown mode';
  }
  visualBlocks.append(block);
  return block;
}

function addVisualParagraph(focus = true) {
  const block = makeVisualBlock('<p><br></p>', true);
  visualParts.push({block, original: '', gap: '', added: true});
  if (focus) {
    block.focus();
    const selection = getSelection();
    selection.collapse(block.firstChild, 0);
    visualSelection = selection.getRangeAt(0).cloneRange();
  }
}

function visualMarkdown(node) {
  if (node.nodeType === Node.TEXT_NODE) return node.data.replaceAll('\u00a0', ' ');
  if (node.nodeType !== Node.ELEMENT_NODE) return '';
  if (node.hasAttribute('data-markdown')) return node.dataset.markdown;
  const tag = node.localName;
  if (tag === 'br') return '\n';
  if (tag === 'ul' || tag === 'ol') {
    const list = Array.from(node.children, (item, index) => {
      const marker = tag === 'ol' ? (Number(node.getAttribute('start') || 1) + index) + '. ' : '- ';
      return marker + visualChildren(item).trim().replaceAll('\n', '\n' + ' '.repeat(marker.length));
    });
    return list.join('\n') + '\n\n';
  }
  const text = visualChildren(node);
  if (/^h[1-6]$/.test(tag)) {
    return text.trim() ? '#'.repeat(Number(tag[1])) + ' ' + text.trim().replace(/\n+/g, ' ') + '\n\n' : '';
  }
  if (tag === 'div' || tag === 'p') return text.trimEnd() + '\n\n';
  const mark = {b:'**', strong:'**', i:'*', em:'*', strike:'~~', del:'~~', s:'~~'}[tag];
  if (mark && text.trim()) return text.replace(/\S(?:[^\n]*\S)?/g, value => mark + value + mark);
  return text;
}

function visualChildren(node) {
  return Array.from(node.childNodes, visualMarkdown).join('');
}

function syncVisualDraft() {
  let text = '';
  let previousChanged = false;
  for (const part of visualParts) {
    const {block, original, gap, added} = part;
    const unchanged = !block.isContentEditable || block.innerHTML === visualSnapshots.get(block);
    const value = unchanged ? original : visualChildren(block).trim();
    const changed = !unchanged && Boolean(value);
    const newBoundary = value && text.trim() && (added || changed || previousChanged)
      && !/\n[ \t]*\n$/.test(text + gap);
    text += gap + (newBoundary ? (gap.endsWith('\n') ? '\n' : '\n\n') : '') + value;
    previousChanged = changed;
    part.end = text.length;
  }
  $('source').value = text + visualTrailing;
  preserveDraft();
  rememberVisualSelection();
}

function refreshEditorUpload(marker, replacement) {
  if (editorMode !== 'visual') {
    refreshEditorOutput();
    return;
  }
  const token = Array.from(visualBlocks.querySelectorAll('[data-markdown]'))
    .find(element => element.dataset.markdown === marker);
  if (!token) {
    refreshEditorOutput();
    return;
  }
  const part = visualParts.find(part => part.block.contains(token));
  const unchanged = part.block.innerHTML === visualSnapshots.get(part.block);
  part.original = part.original.replace(marker, replacement);
  if (replacement) {
    token.dataset.markdown = replacement;
    token.innerHTML = inline(replacement);
    token.querySelector('a').tabIndex = -1;
  } else {
    token.remove();
  }
  if (unchanged) visualSnapshots.set(part.block, part.block.innerHTML);
  syncVisualDraft();
}

function editorInsertRange() {
  const source = $('source');
  if (editorMode !== 'visual') return [source.selectionStart, source.selectionEnd];
  const part = visualParts.find(part => part.block === activeVisualBlock);
  const end = part?.end ?? source.value.length;
  return [end, end];
}

function insertEditorLink(text) {
  const source = $('source');
  if (editorMode === 'visual' && activeVisualBlock?.isConnected) {
    restoreVisualSelection();
    const selection = getSelection();
    if (selection.rangeCount && activeVisualBlock.contains(selection.anchorNode)
        && activeVisualBlock.contains(selection.focusNode)) {
      const range = selection.getRangeAt(0);
      const template = document.createElement('template');
      template.innerHTML = inline(text, true);
      const token = template.content.firstChild;
      range.deleteContents();
      range.insertNode(token);
      selection.collapse(token.parentNode, Array.from(token.parentNode.childNodes).indexOf(token) + 1);
      visualSelection = selection.getRangeAt(0).cloneRange();
      syncVisualDraft();
      return;
    }
  }
  const [start, end] = editorInsertRange();
  const separated = editorMode === 'visual' && source.value.trim() ? '\n\n' + text + '\n\n' : text;
  source.setRangeText(separated, start, end, 'end');
  preserveDraft();
  refreshEditorOutput();
  if (editorMode === 'write') source.focus();
}

function restoreVisualSelection() {
  const range = visualSelection;
  activeVisualBlock.focus();
  if (!range || !activeVisualBlock.contains(range.commonAncestorContainer)) return;
  const selection = getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
}

visualBlocks.addEventListener('focusin', event => {
  if (event.target.isContentEditable) {
    if (activeVisualBlock !== event.target) visualSelection = null;
    activeVisualBlock = event.target;
  }
});
function rememberVisualSelection() {
  if (editorMode !== 'visual' || !activeVisualBlock) return;
  const selection = getSelection();
  if (selection.rangeCount && activeVisualBlock.contains(selection.anchorNode) && activeVisualBlock.contains(selection.focusNode)) {
    visualSelection = selection.getRangeAt(0).cloneRange();
  }
}
document.addEventListener('selectionchange', rememberVisualSelection);
visualBlocks.addEventListener('input', syncVisualDraft);
visualBlocks.addEventListener('click', event => {
  if (event.target.closest('a, .table-sort')) {
    event.preventDefault();
    event.stopPropagation();
  }
});
visualBlocks.addEventListener('paste', event => {
  event.preventDefault();
  const images = Array.from(event.clipboardData.files).filter(file => file.type.startsWith('image/'));
  if (images.length) addImages(images);
  else if (event.target.isContentEditable) document.execCommand('insertText', false, event.clipboardData.getData('text/plain'));
});
visualBlocks.addEventListener('drop', event => event.preventDefault());

$('visual-tools').addEventListener('mousedown', event => {
  if (event.target.closest('[data-command]')) event.preventDefault();
});
$('visual-tools').addEventListener('click', event => {
  const button = event.target.closest('[data-command]');
  if (!button) return;
  if (!activeVisualBlock) {
    const first = visualBlocks.querySelector('[contenteditable="true"]');
    first.focus();
  }
  restoreVisualSelection();
  const command = button.dataset.command;
  const selection = getSelection();
  let expandedSelection = false;
  if (command.endsWith('List') && selection.isCollapsed && selection.anchorNode) {
    const node = selection.anchorNode;
    const paragraph = (node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement)?.closest('p,h1,h2,h3,h4,h5,h6');
    if (paragraph && activeVisualBlock.contains(paragraph)) {
      const range = document.createRange();
      range.selectNodeContents(paragraph);
      selection.removeAllRanges();
      selection.addRange(range);
      expandedSelection = true;
    }
  }
  document.execCommand(command, false, button.dataset.value || null);
  if (expandedSelection && selection.rangeCount) selection.collapseToEnd();
  syncVisualDraft();
});
$('visual-add-paragraph').onclick = () => addVisualParagraph();
$('tab-visual').onclick = () => setEditorMode('visual');
