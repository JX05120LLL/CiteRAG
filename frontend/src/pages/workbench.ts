import type { AppState, Page } from '../state';
import type { Citation } from '../api/client';
import brandMark from '../../../assets/brand/mark.svg';
import { locationText } from './sources';
import { kbStatuses } from '../state';
import { action, el, icon, alert, heading } from '../shared/dom';

function answerFailure(code: string | null): string {
  const reasons: Record<string, string> = {
    answer_unverifiable: '模型输出未通过格式或原文核验。请重试，或把问题缩小到资料中的具体内容。',
    answer_format_invalid: '模型返回的格式不完整或不符合要求。请重试，或缩小问题范围。',
    answer_reference_invalid: '模型给出的引用编号未通过核验。请重试；当前未展示未核验的回答。',
    answer_source_mismatch: '模型回答无法逐字匹配引用原文，或包含不允许的网址。请缩小问题范围后重试。',
    answer_unavailable: '回答模型服务暂不可用。请检查系统状态、模型配置或供应商配额后重试。',
    retrieval_failed: '检索或原文核对失败。请检查资料与任务状态后重试。',
    rerank_missing: '检索没有完成必要的重排。请检查模型服务状态后重试。',
    summary_unavailable: '聊天上下文摘要未完成。请检查模型服务后重试，或新建聊天。',
    request_interrupted: '请求连接已中断。请刷新查看保存状态后重试。',
    server_restarted: '服务重启中断了这次回答。请重试。',
    kb_changed: '知识库已变化。请等待资料就绪后重新提问。',
  };
  return (code && Object.hasOwn(reasons, code) && reasons[code]) || '回答未完成，请检查任务与服务状态后重试。';
}

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
  selectCitation: (messageId: string, evidenceId: string) => void;
}

function citationCard(citation: Citation, selected: boolean, open: () => void): HTMLElement {
  const card = el('li', 'chat-citation');
  const trigger = action(`${citation.filename} · ${locationText(citation)}`, 'citation-trigger', open);
  trigger.prepend(icon('file')); trigger.setAttribute('aria-expanded', String(selected));
  card.append(trigger);
  return card;
}

function composer(state: AppState, actions: WorkbenchActions, navigate: (page: Page) => void) {
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
  input.addEventListener('input', () => {
    actions.setDraft(input.value);
    send.disabled = !enabled || busy || !input.value.trim();
    send.title = send.disabled && !input.value.trim() ? '先输入问题' : '';
  });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      void actions.sendChat();
    }
  });
  box.append(input);
  const toolbar = el('div', 'composer-toolbar');
  const send = action('发送问题', 'button primary send-button', () => { void actions.sendChat(); });
  send.append(icon('arrow'));
  send.disabled = !enabled || busy || !state.chatDraft.trim();
  if (send.disabled) send.title = current?.status === 'maintaining' ? '知识库维护中，问答暂停'
    : current?.status === 'blocked' ? '知识库待修复，问答暂停'
    : current?.status === 'empty' ? '知识库尚无已核验资料'
    : !enabled ? '先选择就绪知识库并创建或打开聊天' : '先输入问题';
  const image = action('添加图片', 'button secondary'); image.prepend(icon('image')); image.disabled = true;
  image.title = '图片提问尚未接入'; image.setAttribute('aria-describedby', 'image-unavailable');
  const context = el('span', 'composer-context', current?.name ?? '未选择知识库'); context.prepend(icon('book'));
  const voice = action('语音通话', 'button secondary voice-entry', () => navigate('voice')); voice.prepend(icon('mic'));
  voice.disabled = busy; voice.title = '打开通话页面；语音服务尚未接入';
  toolbar.append(image, context, voice, send);
  box.append(toolbar);
  const hint = el('p', 'composer-note', current?.status === 'ready'
    ? '直接提问，编号和短语可在当前知识库中精确定位；回答只引用可核查的原文。'
    : '资料未就绪或正在维护时暂停问答；解析完成不等于已入库。');
  const capability = el('p', 'composer-capability', '图片提问尚未接入；语音页面可查看接入状态。'); capability.id = 'image-unavailable';
  area.append(box, hint, capability);
  return area;
}

export function renderWorkbench(main: HTMLElement, state: AppState,
                                navigate: (page: Page) => void, actions: WorkbenchActions) {
  const {loading, basesError: error, bases} = state;
  const content = el('div', 'conversation-content');
  main.classList.toggle('is-home', !state.selectedChatId);
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
    const titlebar = el('div', 'workspace-heading');
    const titles = el('div');
    if (!state.selectedChatId) {
      const mark = el('img', 'welcome-mark'); mark.src = brandMark; mark.alt = ''; mark.width = 48; mark.height = 48;
      titles.append(mark);
    }
    titles.append(heading(state.selectedChatId ? state.chats?.find((chat) => chat.id === state.selectedChatId)?.title ?? '当前聊天' : '从你的知识库开始提问'));
    const selectedBase = bases.find((base) => base.id === state.selectedKbId);
    titles.append(el('p', 'metadata', state.selectedChatId
      ? `固定知识库：${selectedBase?.name ?? '原知识库暂不可用'} · ${selectedBase ? kbStatuses[selectedBase.status] : '状态待确认'}`
      : '先选择已就绪知识库，再创建聊天。每个聊天固定一个知识库。'));
    titlebar.append(titles);
    if (state.selectedChatId) {
      const controls = el('details', 'chat-controls');
      controls.append(el('summary', '', '聊天操作'));
      const rename = action('聊天改名', 'button secondary', () => { void actions.renameChat(); });
      const remove = action('删除聊天', 'button secondary', () => { void actions.deleteChat(); });
      rename.disabled = remove.disabled = state.chatPending;
      controls.append(rename, remove); titlebar.append(controls);
    }
    content.append(titlebar);
    const picker = el('div', 'chat-picker');
    const label = el('label', '', '当前知识库');
    label.htmlFor = 'chat-kb';
    label.textContent = state.selectedChatId ? '新聊天使用的知识库' : '选择知识库';
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
    if (state.selectedChatId) {
      const switcher = el('details', 'chat-switcher');
      switcher.append(el('summary', '', '切换知识库并新建聊天'), picker);
      content.append(switcher);
    } else content.append(picker);
    const libraries = el('ul', 'knowledge-list');
    for (const base of bases) {
      const row = el('li', 'knowledge-row');
      row.append(icon('folder'), el('span', 'knowledge-name', base.name),
        el('span', 'metadata', kbStatuses[base.status]));
      libraries.append(row);
    }
    if (!state.selectedKbId) content.append(libraries);
    const current = bases.find((base) => base.id === state.selectedKbId);
    if (state.chatError) content.append(alert(state.chatError.message));
    if (state.selectedChatId) {
      const transcript = el('section', 'chat-transcript');
      transcript.setAttribute('aria-label', '聊天记录');
      if (!state.chatMessages.length) transcript.append(el('p', 'chat-empty', '还没有问题。输入区会向当前固定知识库检索，并在回答旁展示可核查来源。'));
      for (const message of state.chatMessages) {
        const item = el('article', 'chat-message');
        const question = el('div', 'chat-question');
        question.append(el('span', 'message-role', '我的问题'), el('p', '', message.question));
        item.append(question);
        const answer = message.hidden ? '资料维护中或已重建，旧知识回答与证据暂不显示。'
          : message.status === 'answered' ? message.text
          : message.status === 'insufficient_evidence' ? '当前知识库没有足够的可核查证据，暂不作答。'
          : message.status === 'needs_clarification' ? message.text || '请补充或确认编号、型号、版本等查询条件。'
          : message.status === 'conflicting_evidence' ? message.text || '现有资料存在冲突，请核查原文。'
          : message.status === 'interrupted' ? '知识库发生变化或服务中断，这次回答未完成。'
          : message.status === 'partial' ? message.text
          : message.status === 'running' && state.chatStreamAttemptId === message.attempt_id &&
            state.chatStreamText ? state.chatStreamText
          : message.status === 'running' ? '提问已保存，正在检索并核对来源…'
          : answerFailure(message.error_code);
        const response = el('div', 'chat-response');
        const status = message.hidden ? '已隐藏' : message.status === 'answered' ? '已保存并核验'
          : message.status === 'running' ? '处理中' : message.status === 'partial' ? '部分回答，未完成'
          : message.status === 'insufficient_evidence' ? '证据不足'
          : message.status === 'needs_clarification' && message.route === 'unsupported' ? '当前不可查询'
          : message.status === 'needs_clarification' ? '需要澄清'
          : message.status === 'conflicting_evidence' ? '依据冲突' : '回答失败';
        response.append(el('span', 'message-role', 'CiteRAG 回答'), el('span', `message-status status-${message.status}`, status),
          el('p', 'chat-answer', answer));
        item.append(response);
        if (!message.hidden && ['failed', 'interrupted', 'partial'].includes(message.status) && message.error_code) {
          if (message.status !== 'failed') response.append(el('p', 'field-hint', answerFailure(message.error_code)));
          if (/^[a-z][a-z0-9_]{0,63}$/.test(message.error_code)) response.append(el('p', 'metadata', `错误代码：${message.error_code}`));
        }
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
          for (const citation of message.citations) list.append(citationCard(citation,
            state.selectedCitation?.messageId === message.message_id && state.selectedCitation.evidenceId === citation.evidence_id,
            () => actions.selectCitation(message.message_id, citation.evidence_id)));
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
    if (!state.selectedChatId && state.chats?.length) {
      const recent = el('section', 'recent-chats'); recent.append(el('h2', '', '继续最近聊天'));
      for (const chat of state.chats.slice(0, 3)) {
        const entry = action(chat.title, 'recent-chat', () => { void actions.selectChat(chat.id); });
        entry.prepend(icon('book')); entry.disabled = state.chatPending;
        entry.append(el('span', 'metadata', bases.find((base) => base.id === chat.kb_id)?.name ?? '知识库暂不可用'));
        recent.append(entry);
      }
      content.append(recent);
    }
  }
  main.append(content, composer(state, actions, navigate));
  if (!state.selectedChatId) {
    const recent = content.querySelector('.recent-chats');
    if (recent) main.append(recent);
  }
}
