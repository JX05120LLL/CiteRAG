import type { KnowledgeBase } from '../api/client';
import type { AppState, KnowledgeDraft } from '../state';
import { isKnowledgePending, kbStatuses, knowledgeLimit } from '../state';
import { action, el, icon, alert, heading } from '../shared/dom';

interface KnowledgeActions {
  refresh: () => Promise<void>;
  createKnowledgeBase: () => Promise<void>;
  beginRename: (base: KnowledgeBase) => void;
  renameKnowledgeBase: () => Promise<void>;
  cancelRename: () => void;
  openDocuments: (base: KnowledgeBase) => void;
}

function nameField(id: string, label: string, draft: KnowledgeDraft, disabled: boolean) {
  const field = el('div', 'knowledge-field');
  const caption = el('label', '', label);
  caption.htmlFor = id;
  const input = el('input', 'name-input');
  input.id = id;
  input.name = 'name';
  input.type = 'text';
  input.value = draft.name;
  input.disabled = disabled;
  input.autocomplete = 'off';
  input.setAttribute('aria-describedby', `${id}-hint${draft.error ? ` ${id}-error` : ''}`);
  if (draft.error?.kind === 'validation') input.setAttribute('aria-invalid', 'true');
  input.addEventListener('input', () => { draft.name = input.value; });
  const hint = el('p', 'field-hint', '1–120 个字符，首尾空白会自动去除。');
  hint.id = `${id}-hint`;
  field.append(caption, input, hint);
  return { field, input };
}

function draftError(id: string, draft: KnowledgeDraft) {
  const message = alert(draft.error!.message);
  message.id = `${id}-error`;
  return message;
}

function createForm(state: AppState, create: () => Promise<void>) {
  const draft = state.createDraft;
  const atLimit = (state.bases?.length ?? 0) >= knowledgeLimit;
  const disabled = state.loading || !!state.basesError || isKnowledgePending(state) ||
    draft.error?.code === 'recovery_read_failed' || (atLimit && !draft.uncertain);
  const form = el('form', 'knowledge-form create-knowledge-form');
  form.noValidate = true;
  form.setAttribute('aria-label', '新建知识库');
  form.setAttribute('aria-busy', String(draft.pending));
  const {field, input} = nameField('kb-create-name', '新建知识库', draft, disabled);
  input.placeholder = '例如：产品使用手册';
  input.readOnly = draft.uncertain;
  const controls = el('div', 'form-controls');
  const submit = action(draft.pending ? '正在创建…' : draft.uncertain ? '重试确认创建' : '创建知识库', 'button primary');
  submit.type = 'submit';
  submit.disabled = disabled;
  controls.append(submit);
  form.append(field, controls);
  if (draft.error) form.append(draftError('kb-create-name', draft));
  if (draft.uncertain) {
    const note = el('p', 'field-hint form-note', '创建结果尚未确认。请重试同一请求，确认前保留当前名称，避免重复建库。');
    note.setAttribute('role', 'status');
    form.append(note);
  } else if (atLimit || state.loading || state.basesError) {
    const note = el('p', 'field-hint form-note', atLimit ? '已达到 5 个知识库上限。删除功能尚未开放，现有知识库仍可改名。'
      : state.loading ? '读取列表后可创建知识库。' : '知识库列表暂不可用，恢复连接并刷新后可创建。');
    note.id = 'kb-create-reason';
    submit.setAttribute('aria-describedby', note.id);
    form.append(note);
  }
  form.addEventListener('submit', (event) => { event.preventDefault(); void create(); });
  return form;
}

function renameForm(state: AppState, actions: KnowledgeActions) {
  const draft = state.renameDraft!;
  const form = el('form', 'knowledge-form rename-knowledge-form');
  form.noValidate = true;
  form.setAttribute('aria-label', '修改知识库名称');
  form.setAttribute('aria-busy', String(draft.pending));
  const {field} = nameField('kb-rename-name', '知识库名称', draft, isKnowledgePending(state));
  const controls = el('div', 'form-controls');
  const save = action(draft.pending ? '正在保存…' : '保存名称', 'button primary');
  save.type = 'submit';
  const cancel = action('取消', 'button secondary', actions.cancelRename);
  save.disabled = cancel.disabled = isKnowledgePending(state);
  controls.append(save, cancel);
  form.append(field, controls);
  if (draft.error) form.append(draftError('kb-rename-name', draft));
  form.addEventListener('submit', (event) => { event.preventDefault(); void actions.renameKnowledgeBase(); });
  return form;
}

export function renderKnowledge(main: HTMLElement, state: AppState, actions: KnowledgeActions) {
  const {basesError: error, bases, loading} = state;
  const content = el('section', 'management-content');
  const titlebar = el('div', 'management-heading');
  const title = el('div');
  title.append(heading('我的知识库'));
  title.append(el('p', 'metadata', bases ? `${bases.length} / ${knowledgeLimit} 个知识库` : '管理本地知识库名称与资料状态'));
  titlebar.append(title);
  content.append(titlebar);
  content.append(createForm(state, actions.createKnowledgeBase));
  if (state.knowledgeNotice) content.append(alert(state.knowledgeNotice, 'success'));
  if (loading) {
    const pending = el('p', 'intro', '正在读取知识库…');
    pending.setAttribute('role', 'status');
    content.append(pending);
  } else if (error) content.append(alert(error.message));
  else if (!bases?.length) {
    const empty = el('div', 'management-empty');
    empty.append(icon('folder'), el('h2', '', '还没有知识库'), el('p', 'intro', '先为资料创建一个知识库，再进入「资料与任务」上传文件。新建库暂无资料，不可问答。'));
    content.append(empty);
  } else {
    const table = el('table', 'knowledge-table');
    const head = el('thead');
    const row = el('tr');
    for (const label of ['知识库名称', '资料状态', '操作']) { const cell = el('th', '', label); cell.scope = 'col'; row.append(cell); }
    head.append(row);
    const body = el('tbody');
    for (const base of bases) {
      const item = el('tr');
      const name = el('td', 'knowledge-table-name');
      if (state.renameDraft?.id === base.id) name.append(renameForm(state, actions));
      else name.textContent = base.name;
      const controls = el('td');
      const group = el('div', 'row-actions');
      const rename = action('改名', 'text-button', () => actions.beginRename(base));
      rename.disabled = isKnowledgePending(state) || state.renameDraft?.id === base.id;
      rename.setAttribute('aria-label', `改名：${base.name}`);
      const remove = action('删除', 'text-button');
      remove.disabled = true;
      remove.setAttribute('aria-describedby', 'upload-unavailable');
      const documents = action('资料与任务', 'text-button', () => actions.openDocuments(base));
      documents.disabled = isKnowledgePending(state);
      documents.setAttribute('aria-label', `资料与任务：${base.name}`);
      group.append(documents, rename, remove);
      controls.append(group);
      item.append(name, el('td', 'knowledge-table-status', kbStatuses[base.status]), controls);
      body.append(item);
    }
    table.append(head, body);
    content.append(table);
  }
  const note = el('p', 'scope-note', '进入「资料与任务」上传、替换或删除受管资料并核查任务。上传受理不等于入库成功；知识库整体删除尚未提供。');
  note.id = 'upload-unavailable';
  const retry = action('刷新列表', 'button secondary', () => { void actions.refresh(); });
  retry.disabled = loading || isKnowledgePending(state);
  content.append(note, retry);
  main.append(content);
}
