import type { AppState, Page } from '../state';
import type { Citation } from '../api/client';
import { kbStatuses } from '../state';
import { action, el, icon, alert, heading } from '../shared/dom';

interface WorkbenchActions {
  refresh: () => Promise<void>;
  selectChat: (id: string) => Promise<void>;
  createChat: () => Promise<void>;
  renameChat: () => Promise<void>;
  deleteChat: () => Promise<void>;
  retryChat: (messageId: string) => Promise<void>;
  sendChat: () => Promise<void>;
  selectKb: (id: string) => void;
  setDraft: (text: string) => void;
  setMode: (mode: 'semantic' | 'exact') => void;
  setExact: (key: 'doc_code' | 'model_code' | 'edition' | 'phrase', value: string) => void;
}

function locationText(citation: Citation): string {
  const place = citation.locator;
  if (place.kind === 'page' && Number.isInteger(place.page)) return `第 ${place.page} 页`;
  if (place.kind === 'lines' && Number.isInteger(place.line_start)) {
    return place.line_end === place.line_start ? `第 ${place.line_start} 行`
      : `第 ${place.line_start}–${place.line_end} 行`;
  }
  if (place.kind === 'paragraph' && Number.isInteger(place.paragraph)) return `第 ${place.paragraph} 段`;
  if (place.kind === 'table' && Number.isInteger(place.table) && Number.isInteger(place.row)) {
    return `表 ${place.table} 第 ${place.row} 行`;
  }
  return '来源片段';
}

function citationCard(citation: Citation): HTMLElement {
  const card = el('li', 'chat-citation');
  card.append(el('strong', '', `${citation.filename} · ${locationText(citation)}`),
    el('blockquote', '', citation.excerpt));
  const original = el('a', 'text-button', '下载原文核对');
  original.href = `/api/documents/${encodeURIComponent(citation.document_id)}/original`;
  card.append(original);
  return card;
}

function composer(state: AppState, actions: WorkbenchActions) {
  const area = el('div', 'composer-area');
  const box = el('div', 'composer');
  const input = el('textarea');
  const current = state.bases?.find((item) => item.id === state.selectedKbId);
  const enabled = current?.status === 'ready' && Boolean(state.selectedChatId);
  const busy = state.chatPending || state.chatMessages.some((item) => item.status === 'running');
  input.disabled = !enabled || busy;
  input.rows = 2;
  input.maxLength = 1000;
  input.value = state.chatDraft;
  input.placeholder = enabled ? '输入问题，答案将依据当前知识库的已核验证据' : '请先选择已就绪知识库并创建或打开聊天';
  input.setAttribute('aria-label', '问题输入');
  input.addEventListener('input', () => actions.setDraft(input.value));
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      void actions.sendChat();
    }
  });
  if (enabled) {
    const mode = el('div', 'chat-mode');
    const label = el('label', '', '查询方式'); label.htmlFor = 'chat-mode-select';
    const select = el('select', 'name-input'); select.id = 'chat-mode-select';
    const semantic = el('option', '', '普通检索'); semantic.value = 'semantic';
    const exact = el('option', '', '编号／型号／版本精确查询'); exact.value = 'exact';
    select.append(semantic, exact); select.value = state.chatMode; select.disabled = busy;
    select.addEventListener('change', () => actions.setMode(select.value as 'semantic' | 'exact'));
    mode.append(label, select);
    if (state.chatMode === 'exact') {
      for (const [key, caption] of [
        ['doc_code', '文档编号'], ['model_code', '型号'], ['edition', '版本'], ['phrase', '定位短语（可选）'],
      ] as const) {
        const field = el('input', 'name-input');
        field.value = state.exactFilter[key] ?? ''; field.maxLength = 80;
        field.placeholder = caption; field.setAttribute('aria-label', caption);
        field.disabled = busy;
        field.addEventListener('input', () => actions.setExact(key, field.value));
        mode.append(field);
      }
    }
    area.append(mode);
  }
  const toolbar = el('div', 'composer-toolbar');
  const photo = action('添加图片', 'text-button');
  photo.prepend(icon('image'));
  const voice = action('开始语音', 'button voice');
  voice.prepend(icon('mic'));
  photo.disabled = voice.disabled = true;
  const send = action('', 'send-button', () => { void actions.sendChat(); });
  send.append(icon('arrow'), el('span', 'sr-only', '发送'));
  send.setAttribute('aria-label', '发送');
  send.disabled = !enabled || busy;
  toolbar.append(photo, voice, el('span', 'composer-shortcut', 'Enter 发送'), send);
  box.append(input, toolbar);
  const hint = el('p', 'composer-note', current?.status === 'ready'
    ? '仅显示已核验、能定位到原文的来源；证据不足会拒答。图片与语音留待后续。'
    : '资料未就绪或正在维护时暂停问答；解析完成不等于已入库。');
  area.append(box, hint);
  return area;
}

export function renderWorkbench(main: HTMLElement, state: AppState,
                                navigate: (page: Page) => void, actions: WorkbenchActions) {
  const {loading, basesError: error, bases} = state;
  const content = el('div', 'conversation-content');
  content.append(el('p', 'eyebrow', 'CiteRAG / 本地工作台'));
  if (loading) {
    content.append(heading('正在连接工作台'), el('p', 'intro', '正在读取本地知识库与聊天…'));
    content.setAttribute('role', 'status');
  } else if (error) {
    content.append(heading('本地资料暂不可用'), alert(error.message),
      action('重新连接', 'button secondary', () => { void actions.refresh(); }));
  } else if (!bases?.length) {
    content.append(heading('还没有知识库'), el('p', 'intro',
      '前往「我的知识库」创建知识库，上传资料并等待入库核验。'),
    action('前往我的知识库', 'button secondary', () => navigate('knowledge')));
  } else {
    content.append(heading(state.selectedChatId ? '当前聊天' : '选择资料，开始新的对话'));
    const picker = el('div', 'chat-picker');
    const label = el('label', '', '当前知识库');
    label.htmlFor = 'chat-kb';
    const select = el('select', 'name-input');
    select.id = 'chat-kb';
    const empty = el('option', '', '选择知识库'); empty.value = '';
    select.append(empty);
    for (const base of bases) {
      const option = el('option', '', `${base.name} · ${kbStatuses[base.status]}`);
      option.value = base.id;
      select.append(option);
    }
    select.value = state.selectedKbId ?? '';
    select.disabled = state.chatPending;
    select.addEventListener('change', () => actions.selectKb(select.value));
    const create = action('新建聊天', 'button secondary', () => { void actions.createChat(); });
    create.disabled = state.chatPending || bases.find((base) => base.id === state.selectedKbId)?.status !== 'ready';
    picker.append(label, select, create);
    content.append(picker);
    const libraries = el('ul', 'knowledge-list');
    for (const base of bases) {
      const row = el('li', 'knowledge-row');
      row.append(icon('folder'), el('span', 'knowledge-name', base.name),
        el('span', 'metadata', kbStatuses[base.status]));
      libraries.append(row);
    }
    if (!state.selectedKbId) content.append(libraries);
    const chats = (state.chats ?? []).filter((chat) => chat.kb_id === state.selectedKbId);
    const current = bases.find((base) => base.id === state.selectedKbId);
    if (chats.length) {
      const list = el('div', 'chat-list');
      for (const chat of chats) {
        const open = action(chat.title, chat.id === state.selectedChatId ? 'button secondary active' : 'button secondary',
          () => { void actions.selectChat(chat.id); });
        open.disabled = state.chatPending;
        list.append(open);
      }
      content.append(list);
    }
    if (state.chatError) content.append(alert(state.chatError.message));
    if (state.selectedChatId) {
      const controls = el('div', 'chat-controls');
      controls.append(action('聊天改名', 'text-button', () => { void actions.renameChat(); }),
        action('删除聊天', 'text-button', () => { void actions.deleteChat(); }));
      content.append(controls);
      const transcript = el('section', 'chat-transcript');
      transcript.setAttribute('aria-label', '聊天记录');
      for (const message of state.chatMessages) {
        const item = el('article', 'chat-message');
        item.append(el('p', 'chat-question', message.question));
        const answer = message.hidden ? '资料维护中或已重建，旧知识回答与证据暂不显示。'
          : message.status === 'answered' ? message.text
          : message.status === 'insufficient_evidence' ? '当前知识库没有足够的可核查证据，暂不作答。'
          : message.status === 'interrupted' ? '知识库发生变化或服务中断，这次回答未完成。'
          : message.status === 'partial' ? message.text
          : message.status === 'running' && state.chatStreamAttemptId === message.attempt_id &&
            state.chatStreamText ? state.chatStreamText
          : message.status === 'running' ? '提问已保存，正在检索并核对来源…'
          : '回答未完成，请检查任务与服务状态。';
        item.append(el('p', 'chat-answer', answer));
        if (message.status === 'running' && state.chatStreamText &&
            state.chatStreamAttemptId === message.attempt_id)
          item.append(el('p', 'metadata', state.chatStreamSaved
            ? '已核验的回答正在展示；完整记录已保存。'
            : '仅为来自原文的临时片段，尚未形成已核验回答或保存来源。'));
        if (!message.hidden && message.status === 'partial')
          item.append(el('p', 'metadata', '生成中断；上方片段不是完整回答，也没有可用引用。'));
        if (message.stale && !message.hidden) item.append(el('p', 'metadata', '基于旧资料；新提问会重新检索当前库。'));
        if (!message.hidden && message.status === 'answered' && message.citations.length) {
          const list = el('ul', 'chat-citations');
          for (const citation of message.citations) list.append(citationCard(citation));
          item.append(list);
        }
        if (!message.hidden && !message.stale &&
            (message.status === 'failed' || message.status === 'interrupted' || message.status === 'partial') &&
            current?.status === 'ready') {
          const retry = action('重试回答', 'text-button', () => { void actions.retryChat(message.message_id); });
          retry.disabled = state.chatPending;
          item.append(retry);
        }
        transcript.append(item);
      }
      content.append(transcript);
    }
    content.append(el('p', 'scope-note', '一个聊天固定一个知识库。换库时需要新建聊天。'));
  }
  main.append(content, composer(state, actions));
}
