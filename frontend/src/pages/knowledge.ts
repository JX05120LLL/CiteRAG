import type { AppState } from '../state';
import { kbStatuses } from '../state';
import { action, el, icon, alert, heading } from '../shared/dom';

export function renderKnowledge(main: HTMLElement, state: AppState, refresh: () => Promise<void>) {
  const {basesError: error, bases, loading} = state;
  const content = el('section', 'management-content');
  const titlebar = el('div', 'management-heading');
  const title = el('div');
  title.append(heading('我的知识库'));
  const upload = action('上传资料', 'button secondary');
  upload.disabled = true;
  upload.prepend(icon('upload'));
  upload.setAttribute('aria-describedby', 'upload-unavailable');
  titlebar.append(title, upload);
  content.append(titlebar);
  if (loading) {
    const pending = el('p', 'intro', '正在读取知识库…');
    pending.setAttribute('role', 'status');
    content.append(pending);
  } else if (error) content.append(alert(error.message));
  else if (!bases?.length) {
    const empty = el('div', 'management-empty');
    empty.append(icon('folder'), el('h2', '', '还没有知识库'), el('p', 'intro', '这里将保存你的本地知识库。建库、资料上传与入库功能尚未开放。'));
    content.append(empty);
  } else {
    const table = el('table', 'knowledge-table');
    const head = el('thead');
    const row = el('tr');
    for (const label of ['知识库名称', '资料状态']) { const cell = el('th', '', label); cell.scope = 'col'; row.append(cell); }
    head.append(row);
    const body = el('tbody');
    for (const base of bases) {
      const item = el('tr');
      item.append(el('td', '', base.name), el('td', '', kbStatuses[base.status]));
      body.append(item);
    }
    table.append(head, body);
    content.append(table);
  }
  const note = el('p', 'scope-note', '资料上传与入库尚未开放。上传完成不等于资料已可用于问答。');
  note.id = 'upload-unavailable';
  const retry = action('刷新列表', 'button secondary', () => { void refresh(); });
  retry.disabled = loading;
  content.append(note, retry);
  main.append(content);
}
