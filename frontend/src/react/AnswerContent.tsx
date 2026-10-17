import type { CSSProperties, ReactNode } from 'react';
import './answer-content.css';

// Deliberately a small display grammar: model HTML, links and images never become DOM or URLs.
// Very large/old records keep their complete original text without expensive Markdown parsing.
const maxFormattedChars = 100_000;
const maxFormattedLines = 2_000;
const maxTableColumns = 24;

function inline(text: string): ReactNode[] {
  const pieces: ReactNode[] = [];
  const token = /`([^`\n]+)`|\*\*([^*\n]+)\*\*|(?<![\p{L}\p{N}_])__([^_\n]+)__(?![\p{L}\p{N}_])|\*([^*\n]+)\*|(?<![\p{L}\p{N}_])_([^_\n]+)_(?![\p{L}\p{N}_])/gu;
  let end = 0;
  for (const match of text.matchAll(token)) {
    if (match.index > end) pieces.push(text.slice(end, match.index));
    const key = match.index;
    if (match[1] !== undefined) pieces.push(<code key={key}>{match[1]}</code>);
    else if (match[2] !== undefined || match[3] !== undefined)
      pieces.push(<strong key={key}>{match[2] ?? match[3]}</strong>);
    else pieces.push(<em key={key}>{match[4] ?? match[5]}</em>);
    end = match.index + match[0].length;
  }
  if (end < text.length) pieces.push(text.slice(end));
  return pieces;
}

function cells(line: string): string[] | null {
  const parts: string[] = [];
  let part = '';
  let codeTicks = 0;
  let separators = 0;
  for (let i = 0; i < line.length; i++) {
    const char = line[i];
    if (char === '\\' && line[i + 1] === '|') { part += '|'; i++; continue; }
    if (char === '`') {
      let count = 1;
      while (line[i + count] === '`') count++;
      if (!codeTicks) codeTicks = count;
      else if (codeTicks === count) codeTicks = 0;
      part += '`'.repeat(count); i += count - 1; continue;
    }
    if (char === '|' && !codeTicks) { parts.push(part.trim()); part = ''; separators++; }
    else part += char;
  }
  if (!separators) return null;
  parts.push(part.trim());
  if (!parts[0]) parts.shift();
  if (!parts[parts.length - 1]) parts.pop();
  return parts;
}

function tableHeader(lines: string[], at: number): { headings: string[]; align: CSSProperties['textAlign'][] } | null {
  const headings = cells(lines[at] ?? '');
  const divider = cells(lines[at + 1] ?? '');
  if (!headings?.length || headings.length > maxTableColumns || divider?.length !== headings.length ||
    !divider.every((value) => /^:?-{3,}:?$/.test(value))) return null;
  return { headings, align: divider.map((value) => value.endsWith(':') ? value.startsWith(':') ? 'center' : 'right' : 'left') };
}

const fence = (line: string) => /^ {0,3}(`{3,}|~{3,})([^`~]*)$/.exec(line);
function heading(line: string): string | null {
  const match = /^ {0,3}#{1,6}[ \t]+(.+)$/.exec(line);
  if (!match) return null;
  let text = match[1].trimEnd();
  let closingAt = text.length;
  while (text[closingAt - 1] === '#') closingAt--;
  // Closing hashes need a preceding space; a literal title such as C# stays intact.
  if (closingAt > 0 && closingAt < text.length && /[ \t]/.test(text[closingAt - 1]))
    text = text.slice(0, closingAt).trimEnd();
  return text || null;
}
const listItem = (line: string) => /^ {0,3}([-+*]|\d{1,6}[.)])\s+(.+)$/.exec(line);

function blocks(lines: string[]): ReactNode[] {
  const result: ReactNode[] = [];
  for (let at = 0; at < lines.length;) {
    const start = at;
    if (!lines[at].trim()) { at++; continue; }
    const code = fence(lines[at]);
    if (code) {
      const content: string[] = [];
      at++;
      while (at < lines.length && !(lines[at].trim().startsWith(code[1][0].repeat(code[1].length)) &&
        new RegExp(`^${code[1][0]}{${code[1].length},}\\s*$`).test(lines[at].trim()))) content.push(lines[at++]);
      if (at < lines.length) at++;
      result.push(<pre key={start}><code>{content.join('\n')}</code></pre>);
      continue;
    }
    const table = tableHeader(lines, at);
    if (table) {
      const rows: string[][] = [];
      at += 2;
      let row = cells(lines[at] ?? '');
      while (row?.length === table.headings.length) {
        rows.push(row); at++; row = cells(lines[at] ?? '');
      }
      result.push(<div key={start} className="answer-table-scroll" role="region" aria-label="回答表格，可横向滚动" tabIndex={0}>
        <table aria-label="回答表格"><thead><tr>{table.headings.map((value, column) =>
          <th key={column} scope="col" style={{ textAlign: table.align[column] }}>{inline(value)}</th>)}</tr></thead>
        <tbody>{rows.map((values, rowIndex) => <tr key={rowIndex}>{values.map((value, column) =>
          <td key={column} style={{ textAlign: table.align[column] }}>{inline(value)}</td>)}</tr>)}</tbody></table>
      </div>);
      continue;
    }
    const title = heading(lines[at]);
    if (title) {
      result.push(<h3 key={start}>{inline(title)}</h3>); at++; continue;
    }
    const firstItem = listItem(lines[at]);
    if (firstItem) {
      const ordered = /^\d/.test(firstItem[1]);
      const items: ReactNode[] = [];
      let item: RegExpExecArray | null = firstItem;
      while (item && /^\d/.test(item[1]) === ordered) {
        items.push(<li key={at}>{inline(item[2])}</li>); at++;
        item = listItem(lines[at] ?? '');
      }
      result.push(ordered ? <ol key={start} start={Number.parseInt(firstItem[1], 10)}>{items}</ol> : <ul key={start}>{items}</ul>);
      continue;
    }
    const paragraph = [lines[at++]];
    while (at < lines.length && lines[at].trim() && !fence(lines[at]) && !heading(lines[at]) &&
      !listItem(lines[at]) && !tableHeader(lines, at)) paragraph.push(lines[at++]);
    result.push(<p key={start}>{inline(paragraph.join('\n'))}</p>);
  }
  return result;
}

export function AnswerContent({ text, className = '' }: { text: string; className?: string }) {
  const lines = text.length <= maxFormattedChars ? text.replace(/\r\n?/g, '\n').split('\n') : null;
  return <div className={`answer-content ${className}`}>
    {lines && lines.length <= maxFormattedLines ? blocks(lines) : <p>{text}</p>}
  </div>;
}
