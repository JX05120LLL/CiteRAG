import type { Citation } from '../api/client';
import { action, el, icon } from '../shared/dom';

export function locationText(citation: Citation): string {
  const place = citation.locator;
  if (place.kind === 'page' && Number.isInteger(place.page)) return `第 ${place.page} 页`;
  if (place.kind === 'lines' && Number.isInteger(place.line_start)) return place.line_end === place.line_start
    ? `第 ${place.line_start} 行` : `第 ${place.line_start}–${place.line_end} 行`;
  if (place.kind === 'paragraph' && Number.isInteger(place.paragraph)) return `第 ${place.paragraph} 段`;
  if (place.kind === 'table' && Number.isInteger(place.table) && Number.isInteger(place.row)) return `表 ${place.table} 第 ${place.row} 行`;
  return '来源片段';
}

export function renderSources(citations: Citation[], selected: Citation, originalUrl: (id: string) => string,
                              select: (citation: Citation) => void, close: () => void) {
  const panel = el('aside', 'source-inspector');
  panel.setAttribute('aria-label', '来源核查');
  const top = el('div', 'inspector-heading');
  const dismiss = action('×', 'icon-button', close);
  dismiss.setAttribute('aria-label', '关闭来源');
  top.append(el('h2', '', '来源核查'), dismiss);
  const label = el('p', 'inspector-subtitle', '引用原文');
  const position = citations.indexOf(selected);
  const pager = el('div', 'source-pager');
  pager.append(el('span', 'metadata', `引用 ${position + 1} / ${citations.length}`));
  const previous = action('上一条', 'text-button', () => select(citations[position - 1]));
  const next = action('下一条', 'text-button', () => select(citations[position + 1]));
  previous.disabled = position <= 0; next.disabled = position >= citations.length - 1;
  pager.append(previous, next);
  const body = el('div', 'inspector-body');
  const file = el('div', 'source-document');
  const text = el('div'); text.append(el('strong', '', selected.filename), el('p', 'metadata', locationText(selected)));
  file.append(icon('file'), text);
  const excerpt = el('blockquote', 'source-excerpt', selected.excerpt);
  const download = el('a', 'button secondary', '下载原文核对');
  download.href = originalUrl(selected.document_id); download.download = ''; download.rel = 'noreferrer';
  body.append(file, excerpt, el('p', 'field-hint', '以上片段来自本条已核验回答的保存引用。'), download);
  const related = el('div', 'related-sources'); related.append(el('h3', '', '本条回答的其他来源'));
  for (const citation of citations.filter((item) => item.evidence_id !== selected.evidence_id)) {
    const entry = action(`${citation.filename} · ${locationText(citation)}`, 'related-source', () => select(citation));
    entry.prepend(icon('file')); related.append(entry);
  }
  if (citations.length > 1) body.append(related);
  panel.append(top, label, pager, body);
  panel.addEventListener('keydown', (event) => { if (event.key === 'Escape') close(); });
  return panel;
}
