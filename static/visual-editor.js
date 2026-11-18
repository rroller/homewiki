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
  markdown(source, false, 0, (firstLine, nextLine, html, kind) => {
    const start = offsets[firstLine];
    let end = nextLine < offsets.length ? offsets[nextLine] - 1 : source.length;
    if (kind === 'list') end = start + source.slice(start, end).replace(/(?:\r?\n[ \t\r]*)+$/, '').length;
    const original = source.slice(start, end);
    const block = makeVisualBlock(html, kind, original);
    visualParts.push({block, kind, original, gap: source.slice(previousEnd, start), end});
    previousEnd = end;
  });
  visualTrailing = source.slice(previousEnd);
  if (!visualBlocks.querySelector('[contenteditable], textarea')) addVisualParagraph(false);
}

function makeVisualBlock(html, kind, original) {
  const block = document.createElement('div');
  block.className = 'visual-block';
  block.innerHTML = html;
  for (const control of block.querySelectorAll('a, button, input')) control.tabIndex = -1;
  if (kind === 'table') {
    for (const cell of block.querySelectorAll('th, td')) {
      cell.contentEditable = 'true';
      cell.setAttribute('aria-label', 'Visual table cell');
      visualSnapshots.set(cell, cell.innerHTML);
    }
  } else if (kind === 'code') {
    const editor = document.createElement('textarea');
    editor.className = 'visual-code';
    editor.setAttribute('aria-label', 'Visual code block');
    editor.spellcheck = false;
    editor.value = block.textContent;
    editor.rows = Math.min(20, Math.max(3, editor.value.split('\n').length));
    visualSnapshots.set(editor, editor.value);
    block.replaceChildren(editor);
  } else if (kind) {
    visualSnapshots.set(block, block.innerHTML);
    block.contentEditable = 'true';
    block.setAttribute('role', 'textbox');
    block.setAttribute('aria-multiline', 'true');
    block.setAttribute('aria-label', 'Visual text block');
  }
  visualBlocks.append(block);
  return block;
}

function serializeTable(block, original) {
  const lines = original.split('\n');
  for (const [index, row] of Array.from(block.querySelector('table').rows).entries()) {
    const changed = Array.from(row.cells).filter(cell => cell.innerHTML !== visualSnapshots.get(cell));
    if (!changed.length) continue;
    const lineIndex = index ? index + 1 : 0;
    const line = lines[lineIndex];
    const cells = tableCells(line);
    for (const cell of changed) {
      while (cells.length <= cell.cellIndex) cells.push(' ');
      cells[cell.cellIndex] = ' ' + visualCellMarkdown(cell) + ' ';
    }
    const leading = /^([ \t]*\|)/.exec(line)?.[1] || line.match(/^[ \t]*/)[0];
    const trailing = /(?<!\\)\|[ \t\r]*$/.exec(line)?.[0] || '';
    lines[lineIndex] = leading + cells.join('|') + trailing;
    if (!trailing && line.endsWith('\r')) lines[lineIndex] += '\r';
  }
  return lines.join('\n');
}

function visualCellMarkdown(cell) {
  return visualChildren(cell).trim().replace(/\n+/g, ' ').replace(/(?<!\\)\|/g, '\\|');
}

function fencedMarkdown(code, opening = '```', closing = '') {
  const match = /^(\s*)(`{3,}|~{3,})(.*)$/.exec(opening);
  const [, indent, fence, info] = match;
  let longest = fence.length - 1;
  const leadingFence = fence[0] === '~' ? /^~+/ : /^`+/;
  for (const line of code.split('\n')) longest = Math.max(longest, line.match(leadingFence)?.[0].length || 0);
  const safeFence = fence[0].repeat(longest + 1);
  const end = closing ? safeFence + closing.slice(fence.length) : safeFence;
  return indent + safeFence + info + '\n' + code + '\n' + end;
}

function serializeVisualPart({block, kind, original}) {
  if (!kind) return original;
  if (kind === 'table') return serializeTable(block, original);
  if (kind === 'code') {
    const field = block.firstChild;
    if (field.value === visualSnapshots.get(field)) return original;
    const lines = original.replace(/\r\n/g, '\n').split('\n');
    const opening = lines[0];
    const fence = opening.trimStart().match(/^(`{3,}|~{3,})/)[0];
    return fencedMarkdown(field.value, opening, lines.at(-1).startsWith(fence) && lines.length > 1 ? lines.at(-1) : '');
  }
  return block.innerHTML === visualSnapshots.get(block) ? original : visualChildren(block).trim();
}

function addVisualParagraph(focus = true) {
  const block = makeVisualBlock('<p><br></p>', 'text');
  visualParts.push({block, kind: 'text', original: '', gap: '', added: true});
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
  if (tag === 'input' && node.classList.contains('task-box')) return node.checked ? '[x] ' : '[ ] ';
  if (tag === 'pre') return fencedMarkdown(node.textContent, node.dataset.fence || '```') + '\n\n';
  if (tag === 'table') {
    const rows = Array.from(node.rows, row => '| ' + Array.from(row.cells, visualCellMarkdown).join(' | ') + ' |');
    rows.splice(1, 0, '| ' + Array.from(node.rows[0].cells, () => '---').join(' | ') + ' |');
    return rows.join('\n') + '\n\n';
  }
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
  if (tag === 'blockquote') return text.trimEnd().split('\n').map(line => '> ' + line).join('\n') + '\n\n';
  if (tag === 'hr') return '---\n\n';
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
    const {original, gap, added} = part;
    const value = serializeVisualPart(part);
    const changed = value !== original && Boolean(value);
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
  const part = visualParts.find(part => part.block.contains(activeVisualBlock));
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
  if (event.target.isContentEditable || event.target.matches('.visual-code')) {
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
  const images = Array.from(event.clipboardData.files).filter(file => file.type.startsWith('image/'));
  if (event.target.matches('.visual-code') && !images.length) return;
  event.preventDefault();
  if (images.length) addImages(images);
  else if (event.target.isContentEditable) {
    const text = event.clipboardData.getData('text/plain');
    document.execCommand('insertText', false, event.target.matches('td, th') ? text.replace(/\r?\n/g, ' ') : text);
  }
});
visualBlocks.addEventListener('beforeinput', event => {
  if (event.target.matches('td, th') && ['insertParagraph', 'insertLineBreak'].includes(event.inputType)) event.preventDefault();
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
    if (!first) return;
    first.focus();
  }
  restoreVisualSelection();
  const command = button.dataset.command;
  if (!activeVisualBlock.isContentEditable) return;
  if (activeVisualBlock.matches('td, th') && !['bold', 'italic'].includes(command)) return;
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
