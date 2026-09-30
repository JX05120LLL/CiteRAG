import brandMark from '../../../assets/brand/mark.svg';
import type { KnowledgeBase } from '../api/client';
import type { AppState, Page } from '../state';
import { isKnowledgePending, kbStatuses, knowledgeLimit } from '../state';
import { action, el, icon } from './dom';

export function renderHeader(state: AppState, navigate: (page: Page) => void, toggleNavigation: () => void) {
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
  context.append(el('span', '', page === 'knowledge' ? '我的知识库' : page === 'status' ? '系统状态' : page === 'voice' ? '语音通话'
    : currentBase?.name ?? '普通聊天'));
  if (page === 'workbench') {
    const rule = el('span', 'context-rule');
    rule.append(icon('lock'), el('span', '', state.selectedKbId ? '一个聊天固定一个知识库' : '不检索知识库'));
    context.append(rule);
  }
  const menu = action('', 'icon-button navigation-toggle', toggleNavigation);
  menu.append(icon('menu')); menu.setAttribute('aria-label', '切换导航');
  menu.setAttribute('aria-expanded', String(state.navigationOpen === true));
  menu.setAttribute('aria-controls', 'app-navigation');
  if (page === 'voice') menu.hidden = true;
  top.append(brand, menu, context, el('div', 'local-mode', '本地工作台 · 单用户'));
  return top;
}

export function renderSidebar(state: AppState, navigate: (page: Page) => void,
                              refresh: () => Promise<void>, createChat: () => Promise<void>,
                              selectChat: (id: string) => Promise<void>,
                              hasMoreChats: boolean, loadMoreChats: () => Promise<void>,
                              openDocuments?: (base: KnowledgeBase, focusTasks?: boolean) => void) {
  const {page, chats, loading} = state;
  const aside = el('aside', 'sidebar');
  aside.setAttribute('aria-label', '聊天与导航');
  aside.id = 'app-navigation';
  const newChat = action('新建聊天', 'button new-chat', () => { void createChat(); });
  newChat.prepend(icon('plus'));
  newChat.disabled = state.chatPending || state.bases === null ||
    (state.selectedKbId !== null && !state.bases.some(
      (base) => base.id === state.selectedKbId && base.status === 'ready'));
  const explanation = el('p', 'sidebar-hint', newChat.disabled
    ? '选择普通聊天或已就绪知识库，再创建聊天。' : '选择已有聊天，或新建普通聊天／知识库聊天。');
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
  const nav = el('nav', 'sidebar-nav primary-nav');
  nav.setAttribute('aria-label', '工作台导航');
  if (page !== 'workbench') {
    const back = action('返回工作台', 'nav-button', () => navigate('workbench'));
    back.prepend(icon('back'));
    nav.append(back);
  }
  for (const [destination, label, symbol] of [['workbench', '对话工作台', 'book'], ['knowledge', '我的知识库', 'folder']] as const) {
    const link = action(label, `nav-button${page === destination ? ' is-active' : ''}`, () => navigate(destination));
    link.prepend(icon(symbol));
    if (page === destination) link.setAttribute('aria-current', 'page');
    nav.append(link);
  }
  const taskBase = state.bases?.find((base) => base.id === state.selectedKbId);
  const tasks = action('处理任务', 'nav-button', () => {
    if (taskBase) { navigate('knowledge'); openDocuments?.(taskBase, true); }
  });
  tasks.prepend(icon('file')); tasks.disabled = !taskBase || !openDocuments;
  tasks.title = taskBase ? '查看当前知识库的资料处理任务' : '先选择知识库，再查看该库的处理任务';
  nav.append(tasks);
  const status = action('系统状态', 'nav-button', () => navigate('status')); status.prepend(icon('status'));
  footer.append(status);
  const library = el('section', 'sidebar-libraries'); library.append(el('p', 'section-label', '我的知识库'));
  for (const base of state.bases ?? []) {
    const entry = action(base.name, 'nav-button library-entry', () => { navigate('knowledge'); openDocuments?.(base); });
    entry.prepend(icon('book')); entry.append(el('span', `status-dot base-${base.status}`));
    entry.setAttribute('aria-label', `查看知识库：${base.name} · ${kbStatuses[base.status]}`);
    library.append(entry);
  }
  aside.append(newChat, explanation, nav, history, library, footer);
  return aside;
}

export function renderManagementSidebar(state: AppState, navigate: (page: Page) => void,
                                        openCreate: () => void, openDocuments: (base: KnowledgeBase, focusTasks?: boolean) => void,
                                        viewedKbId?: string | null) {
  const aside = el('aside', 'management-sidebar');
  aside.setAttribute('aria-label', '我的知识库与管理导航');
  aside.id = 'app-navigation';
  const back = action('返回工作台', 'button new-chat', () => navigate('workbench')); back.prepend(icon('back'));
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
  const nav = el('nav', 'management-nav primary-nav');
  nav.setAttribute('aria-label', '管理导航');
  for (const [destination, label, symbol] of [['knowledge', '我的知识库', 'folder']] as const) {
    const link = action(label, `nav-button${state.page === destination ? ' is-active' : ''}`, () => navigate(destination));
    link.prepend(icon(symbol));
    if (state.page === destination) link.setAttribute('aria-current', 'page');
    nav.append(link);
  }
  const taskBase = state.bases?.find((base) => base.id === (viewedKbId ?? state.selectedKbId)) ?? state.bases?.[0];
  const tasks = action('处理任务', 'nav-button', () => {
    if (taskBase) { navigate('knowledge'); openDocuments(taskBase, true); }
  });
  tasks.prepend(icon('file')); tasks.disabled = !taskBase || isKnowledgePending(state);
  tasks.title = taskBase ? `查看 ${taskBase.name} 的处理任务` : '创建知识库后可查看该库的处理任务';
  nav.append(tasks);
  const status = action('系统状态', `nav-button${state.page === 'status' ? ' is-active' : ''}`, () => navigate('status'));
  status.prepend(icon('status')); if (state.page === 'status') status.setAttribute('aria-current', 'page');
  footer.append(el('p', 'section-label', '本地工作台'), status);
  aside.append(back, nav, heading, hint, list, footer);
  return aside;
}
