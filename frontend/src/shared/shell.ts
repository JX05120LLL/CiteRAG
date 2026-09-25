import brandMark from '../../../assets/brand/mark.svg';
import type { KnowledgeBase } from '../api/client';
import type { AppState, Page } from '../state';
import { isKnowledgePending, kbStatuses, knowledgeLimit } from '../state';
import { action, el, icon } from './dom';

export function renderHeader(state: AppState, navigate: (page: Page) => void) {
  const {page} = state;
  const top = el('header', 'topbar');
  const brand = action('', 'brand', () => navigate('workbench'));
  brand.setAttribute('aria-label', 'CiteRAG 本地工作台');
  const mark = el('img');
  mark.src = brandMark;
  mark.alt = '';
  mark.width = 32;
  mark.height = 32;
  brand.append(mark, el('span', '', 'CiteRAG'));
  const context = el('div', 'top-context');
  if (page === 'workbench') context.append(icon('book'));
  const currentBase = state.bases?.find((base) => base.id === state.selectedKbId);
  context.append(el('span', '', page === 'knowledge' ? '我的知识库' : page === 'status' ? '系统状态'
    : currentBase?.name ?? '尚未选择知识库'));
  if (page === 'workbench') {
    const rule = el('span', 'context-rule');
    rule.append(icon('lock'), el('span', '', '一个聊天固定一个知识库'));
    context.append(rule);
  }
  top.append(brand, context, el('div', 'local-mode', '本地单用户'));
  return top;
}

export function renderSidebar(state: AppState, navigate: (page: Page) => void,
                              refresh: () => Promise<void>, createChat: () => Promise<void>,
                              selectChat: (id: string) => Promise<void>,
                              hasMoreChats: boolean, loadMoreChats: () => Promise<void>) {
  const {page, chats, loading} = state;
  const aside = el('aside', 'sidebar');
  aside.setAttribute('aria-label', '聊天与导航');
  const newChat = action('新建聊天', 'button new-chat', () => { void createChat(); });
  newChat.prepend(icon('plus'));
  newChat.disabled = state.chatPending || !state.bases?.some(
    (base) => base.id === state.selectedKbId && base.status === 'ready');
  const explanation = el('p', 'sidebar-hint', newChat.disabled
    ? '先选择已就绪知识库，再创建聊天。' : '选择已有聊天，或在当前知识库新建聊天。');
  const history = el('div', 'history');
  history.append(el('p', 'section-label', '我的聊天'));
  if (chats?.length) {
    for (const chat of chats) {
      const entry = action('', 'history-entry', () => { void selectChat(chat.id); });
      entry.append(el('span', 'history-title', chat.title));
      entry.disabled = state.chatPending;
      if (chat.id === state.selectedChatId) entry.setAttribute('aria-current', 'true');
      history.append(entry);
    }
    if (hasMoreChats) {
      const more = action('加载更多聊天', 'text-button', () => { void loadMoreChats(); });
      more.disabled = state.chatPending;
      history.append(more);
    }
  } else history.append(el('p', 'empty-history', loading ? '正在读取聊天…' : chats ? '暂无聊天' : '聊天列表暂不可用'));
  if (state.chatsError) {
    history.append(action('重新读取聊天', 'text-button', () => { void refresh(); }));
  }
  const footer = el('div', 'sidebar-footer');
  const privacy = el('p', 'privacy-note');
  privacy.append(icon('book'), el('span', '', '同一安装使用同一份本地资料'));
  footer.append(privacy);
  const nav = el('nav', 'sidebar-nav');
  nav.setAttribute('aria-label', '工作台导航');
  if (page !== 'workbench') {
    const back = action('返回工作台', 'nav-button', () => navigate('workbench'));
    back.prepend(icon('back'));
    nav.append(back);
  }
  for (const [destination, label, symbol] of [['knowledge', '我的知识库', 'folder'], ['status', '系统状态', 'status']] as const) {
    const link = action(label, `nav-button${page === destination ? ' is-active' : ''}`, () => navigate(destination));
    link.prepend(icon(symbol));
    if (page === destination) link.setAttribute('aria-current', 'page');
    nav.append(link);
  }
  footer.append(nav);
  aside.append(newChat, explanation, history, footer);
  return aside;
}

export function renderManagementSidebar(state: AppState, navigate: (page: Page) => void,
                                        openCreate: () => void, openDocuments: (base: KnowledgeBase) => void) {
  const aside = el('aside', 'management-sidebar');
  aside.setAttribute('aria-label', '我的知识库与管理导航');
  const heading = el('div', 'management-sidebar-heading');
  const create = action('', 'text-button', openCreate);
  create.append(icon('plus'));
  create.disabled = state.loading || !!state.basesError || isKnowledgePending(state) ||
    state.createDraft.error?.code === 'recovery_read_failed' ||
    ((state.bases?.length ?? 0) >= knowledgeLimit && !state.createDraft.uncertain);
  create.setAttribute('aria-label', '新建知识库');
  create.setAttribute('aria-describedby', 'kb-create-hint');
  heading.append(el('h2', '', '我的知识库'), create);
  const hint = el('p', 'management-sidebar-hint', state.loading ? '正在读取知识库…' : state.basesError ? '连接恢复后可创建知识库'
    : isKnowledgePending(state) ? '正在保存，请稍候' : (state.bases?.length ?? 0) >= knowledgeLimit ? '已达到 5 个知识库上限' : '最多 5 个知识库，可随时改名');
  hint.id = 'kb-create-hint';
  const list = el('ul', 'management-kb-list');
  if (state.bases?.length) {
    for (const base of state.bases) {
      const row = el('li', 'management-kb-entry');
      const details = action('', 'management-kb-button', () => { navigate('knowledge'); openDocuments(base); });
      details.disabled = isKnowledgePending(state);
      details.setAttribute('aria-label', `查看资料与任务：${base.name}`);
      details.append(el('span', 'knowledge-name', base.name), el('span', 'metadata', kbStatuses[base.status]));
      row.append(icon('folder'), details);
      list.append(row);
    }
  } else list.append(el('li', 'management-sidebar-empty', state.loading ? '正在读取知识库…' : state.bases ? '暂无知识库' : '知识库列表暂不可用'));
  const footer = el('div', 'management-sidebar-footer');
  const nav = el('nav', 'management-nav');
  nav.setAttribute('aria-label', '管理导航');
  for (const [destination, label, symbol] of [['workbench', '返回工作台', 'book'], ['knowledge', '我的知识库', 'folder'], ['status', '系统状态', 'status']] as const) {
    const link = action(label, `nav-button${state.page === destination ? ' is-active' : ''}`, () => navigate(destination));
    link.prepend(icon(symbol));
    if (state.page === destination) link.setAttribute('aria-current', 'page');
    nav.append(link);
  }
  footer.append(el('p', 'section-label', '本地工作台'), nav);
  aside.append(heading, hint, list, footer);
  return aside;
}
