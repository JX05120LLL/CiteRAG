import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { Alert, Button, ConfigProvider, Drawer, Dropdown, Empty, Input, Menu, Modal, Select, Skeleton } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { XProvider } from '@ant-design/x';
import { AudioOutlined, BookOutlined, DownOutlined, FileTextOutlined, MenuOutlined, MessageOutlined, MoreOutlined, PlusOutlined, ReloadOutlined, RightOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import type { ApiClient } from '../api/client';
import { createApi } from '../api/client';
import { mountApp, type AppView } from '../app';
import { mountVoicePage } from '../features/voice/page';
import type { RoomFactory } from '../features/voice/controller';
import type { Page } from '../state';
import { pageTitles } from '../preview/model';
import { StateTag } from '../preview/shared';
import { Voice } from './Voice';
import { theme } from './theme';
import { PageBoundary } from './PageBoundary';
import logo from '../../../assets/brand/mark.svg';
import './theme.css';

const Workbench = lazy(() => import('./Workbench').then((module) => ({ default: module.Workbench })));
const Knowledge = lazy(() => import('./Management').then((module) => ({ default: module.Knowledge })));
const Tasks = lazy(() => import('./Management').then((module) => ({ default: module.Tasks })));
const Status = lazy(() => import('./Management').then((module) => ({ default: module.Status })));
export function Theme({ children }: { children: React.ReactNode }) {
  return <ConfigProvider locale={zhCN} theme={theme} button={{ autoInsertSpace: false }}><XProvider theme={theme}>{children}</XProvider></ConfigProvider>;
}
const icons = { workbench: <MessageOutlined />, knowledge: <BookOutlined />, tasks: <FileTextOutlined />,
  status: <SafetyCertificateOutlined />, voice: <AudioOutlined /> };
interface Dialog { kind: 'confirm' | 'prompt'; text: string; value: string; resolve: (value: boolean | string | null) => void }
export function CiteRagApp({ api: suppliedApi, factory }: { api?: ApiClient; factory?: RoomFactory }) {
  const host = useRef<HTMLDivElement>(null);
  const [view, setView] = useState<AppView | null>(null);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const dialogRef = useRef<Dialog | null>(null);
  const [navigation, setNavigation] = useState(false);
  const [history, setHistory] = useState(false);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [api, setApi] = useState<ApiClient | null>(null);
  useEffect(() => {
    const abort = new AbortController(); let alive = true; let dispose = () => {};
    const client = suppliedApi ?? createApi((input, init) => fetch(input, { ...init,
      signal: init?.signal ? AbortSignal.any([init.signal, abort.signal]) : abort.signal }));
    setApi(client);
    const request = (kind: Dialog['kind'], text: string, value = '') => new Promise<boolean | string | null>((resolve) => {
      if (!alive) { resolve(kind === 'confirm' ? false : null); return; }
      dialogRef.current?.resolve(dialogRef.current.kind === 'confirm' ? false : null);
      const next: Dialog = { kind, text, value, resolve }; dialogRef.current = next; setDialog(next);
    });
    void mountApp(host.current!, client, factory, new URLSearchParams(window.location.search).get('view') === 'status' ? 'status' : 'workbench', {
      render: (next) => { if (alive) setView(next); }, onDispose: (fn) => { dispose = fn; },
      confirm: async (text) => await request('confirm', text) === true,
      prompt: async (text, value) => { const result = await request('prompt', text, value); return typeof result === 'string' ? result : null; },
    });
    return () => { alive = false; dispose(); abort.abort(); dialogRef.current?.resolve(dialogRef.current.kind === 'confirm' ? false : null); dialogRef.current = null; };
  }, [suppliedApi, factory]);
  function finishDialog(value: boolean | string | null) { dialogRef.current?.resolve(value); dialogRef.current = null; setDialog(null); }
  const s = view?.state;
  const base = s?.bases?.find((item) => item.id === s.selectedKbId);
  const headerBase = (s?.page === 'knowledge' && view?.documents.isOpen || s?.page === 'tasks') ? view?.documents.snapshot.base : base;
  const chatHeader = !!s?.selectedChatId && (s.page === 'workbench' || s.page === 'voice');
  const busy = !!s?.chatPending || !!s?.chatMessages.some((item) => item.status === 'running');
  const mediaActive = !!view && !['idle', 'failed'].includes(view.voice.actions.media.phase);
  const managementBusy = !!view?.documents.snapshot.busy;
  const navigate = (page: Page) => {
    setNavigation(false); setHistory(false); view?.actions.navigate(page);
    if (base && (page === 'tasks' || page === 'knowledge' && view?.documents.isOpen) && view?.documents.currentBaseId !== base.id)
      view?.actions.openDocuments(base, page === 'tasks');
    if (page === 'status') void view?.voice.actions.refresh();
    if (page === 'workbench' && s?.page !== 'workbench' && !busy) void view?.actions.refresh();
  };
  const menu = <Menu selectedKeys={[s?.page ?? 'workbench']} items={(Object.keys(pageTitles) as Page[]).map((key) => ({ key, icon: icons[key], label: key === 'knowledge' ? '我的知识库' : pageTitles[key] }))}
    onClick={({ key }) => navigate(key as Page)} />;
  const brand = <div className="brand"><img src={logo} alt="回响 Logo" /><strong>CiteRAG</strong></div>;
  const chatList = <><div className="sidebar-chat-heading"><span>对话记录</span><Button className="sidebar-new-chat" type="text" icon={<PlusOutlined />} aria-label="新建聊天" title="新建普通聊天"
    disabled={busy || mediaActive || s?.loading} onClick={() => { setHistory(false); view?.actions.navigate('workbench'); void view?.actions.createChat(null); }} /><Dropdown trigger={['click']} menu={{ items: [
    ...(s?.bases ?? []).map((kb) => ({ key: kb.id, label: `知识库 · ${kb.name}`, disabled: kb.status !== 'ready' })),
  ], onClick: ({ key }) => { setHistory(false); view?.actions.navigate('workbench');
    void view?.actions.createChat(key); } }}><Button className="sidebar-new-chat" type="text" icon={<DownOutlined />} aria-label="新建知识库聊天" title="新建知识库聊天"
    disabled={busy || mediaActive || s?.loading} /></Dropdown></div>
    {s?.chatsError && <Alert type="error" title={s.chatsError.message} />}
    <div className="sidebar-chat-groups" aria-label="按知识库分组的聊天">
      {(s?.chats ?? []).some((item) => item.kb_id === null) && <section className="sidebar-chat-group"><h2><MessageOutlined /><span className="sidebar-kb-name">普通聊天</span></h2>
        {(s?.chats ?? []).filter((item) => item.kb_id === null).map((item) => <div className={`sidebar-chat-row${s?.selectedChatId === item.id ? ' selected' : ''}`} key={item.id}>
          <MessageOutlined className="sidebar-chat-icon" /><Button className="sidebar-chat-title" type="text" disabled={busy || mediaActive} title={item.title}
            onClick={() => { setHistory(false); view?.actions.navigate('workbench'); void view?.actions.selectChat(item.id); }}>{item.title}</Button>
          <Dropdown trigger={['click']} menu={{ items: [
            { key: 'rename', label: '重命名对话' }, { key: 'archive', label: '归档对话' },
            { key: 'delete', label: '删除对话', danger: true },
          ], onClick: ({ key }) => { if (key === 'rename') void view?.actions.renameChat(item.id);
            if (key === 'archive') void view?.actions.setChatArchived(item.id, true);
            if (key === 'delete') void view?.actions.deleteChat(item.id); } }}>
            <Button type="text" size="small" icon={<MoreOutlined />} aria-label={`${item.title}的更多操作`} disabled={busy || mediaActive} />
          </Dropdown></div>)}</section>}
      {(s?.bases ?? []).map((kb) => { const chats = (s?.chats ?? []).filter((item) => item.kb_id === kb.id);
        if (!chats.length) return null;
        return <section className="sidebar-chat-group" key={kb.id}><h2><BookOutlined /><span className="sidebar-kb-name" title={kb.name}>{kb.name}</span><span className="sidebar-chat-count">{chats.length}</span></h2>
          {chats.map((item) => <div className={`sidebar-chat-row${s?.selectedChatId === item.id ? ' selected' : ''}`} key={item.id}>
            <MessageOutlined className="sidebar-chat-icon" /><Button className="sidebar-chat-title" type="text" disabled={busy || mediaActive} title={item.title}
              onClick={() => { setHistory(false); view?.actions.navigate('workbench'); void view?.actions.selectChat(item.id); }}>{item.title}</Button>
            <Dropdown trigger={['click']} menu={{ items: [
              { key: 'rename', label: '重命名对话', disabled: busy || mediaActive },
              { key: 'archive', label: '归档对话', disabled: busy || mediaActive },
              { key: 'delete', label: '删除对话', danger: true, disabled: busy || mediaActive },
            ], onClick: ({ key }) => { if (key === 'rename') void view?.actions.renameChat(item.id);
              if (key === 'archive') void view?.actions.setChatArchived(item.id, true);
              if (key === 'delete') void view?.actions.deleteChat(item.id); } }}>
              <Button type="text" size="small" icon={<MoreOutlined />} aria-label={`${item.title}的更多操作`} disabled={busy || mediaActive} />
            </Dropdown></div>)}</section>; })}
      {(s?.chats ?? []).filter((item) => item.kb_id !== null && !s?.bases?.some((kb) => kb.id === item.kb_id)).length > 0 &&
        <p className="muted">部分聊天所属知识库尚未读取，请刷新后查看。</p>}
    </div>
    {view?.archivedChatError && <Alert type="error" title={view.archivedChatError.message} />}
    <div className="sidebar-archive"><Button type="text" block aria-expanded={archiveOpen} icon={archiveOpen ? <DownOutlined /> : <RightOutlined />} onClick={() => { setArchiveOpen(!archiveOpen);
      if (!archiveOpen) void view?.actions.loadArchivedChats(true); }}>已归档</Button>
      {archiveOpen && <div className="sidebar-archive-list">
        {view?.archivedChatLoading && <Skeleton active paragraph={{ rows: 2 }} />}
        {!view?.archivedChatLoading && !view?.archivedChats.length && <p className="muted">暂无归档对话</p>}
        {view?.archivedChats.map((item) => <div className="sidebar-chat-row" key={item.id}>
          <span className="sidebar-chat-title" title={item.title}>{item.title}</span>
          <Button size="small" disabled={busy || mediaActive} onClick={() => void view.actions.setChatArchived(item.id, false)}>恢复</Button>
        </div>)}
        {view?.hasMoreArchivedChats && <Button block size="small" disabled={view.archivedChatLoading} onClick={() => void view.actions.loadArchivedChats()}>加载更多归档</Button>}
      </div>}</div>
    {view?.hasMoreChats && <Button block disabled={busy || mediaActive} onClick={() => void view.actions.loadMoreChats()}>加载更多聊天</Button>}
    {!s?.chats?.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无已读取的聊天" />}</>;
  return <Theme><div ref={host} className="citerag-app design-b">
    <a className="skip-link" href="#main-content">跳到主要内容</a>
    <aside className="primary-sidebar">{brand}{menu}<div className="sidebar-divider" />{chatList}
      <div className="sidebar-foot">本地工作台<span>单用户</span></div></aside>
    <div className="product-main"><header className="context-bar"><Button className="mobile-nav-button" icon={<MenuOutlined />} aria-label="打开导航" onClick={() => setNavigation(true)} />
      <div className="context-title">{chatHeader && s?.selectedKbId === null ? <MessageOutlined /> : <BookOutlined />}<span>{chatHeader ? '当前聊天' : '查看知识库'}</span></div>
      {chatHeader ? <span className="kb-select">{s?.selectedKbId === null ? '普通聊天 · 不检索知识库' : headerBase?.name ?? '知识库不可用'}</span> : <Select className="kb-select" aria-label="查看知识库" placeholder="选择知识库" value={headerBase?.id ?? undefined} disabled={busy || managementBusy || mediaActive || s?.loading}
        options={(s?.bases ?? []).map((item) => ({ value: item.id, label: item.name }))}
        onChange={(id) => { view?.actions.selectKb(id); if (s?.page === 'tasks' || s?.page === 'knowledge' && view?.documents.isOpen) {
          const next = s.bases?.find((item) => item.id === id); if (next) view?.actions.openDocuments(next); } }} />}
      {(!chatHeader || s?.selectedKbId !== null) && <StateTag value={headerBase?.status ?? 'unverified'} />}<span className="context-spacer" /><span className="context-local">本地工作台 · 单用户</span>
      <Button className="mobile-history-button" icon={<MessageOutlined />} aria-label="打开聊天记录" onClick={() => setHistory(true)} />
      <Button icon={<ReloadOutlined />} aria-label="刷新工作台" disabled={busy || managementBusy || mediaActive || s?.loading} onClick={() => void view?.actions.refresh()} /></header>
      <main id="main-content" className={`page-content page-${s?.page ?? 'workbench'}`} tabIndex={-1}>
        <div className="page-heading"><div><div className="eyebrow">CiteRAG / {s?.page === 'workbench' ? '对话工作台' : s?.page === 'knowledge' && view?.documents.isOpen ? '知识库与资料' : s ? pageTitles[s.page] : '本地工作台'}</div><h1 tabIndex={-1}>{s?.page === 'knowledge' ? view?.documents.isOpen ? view.documents.snapshot.base?.name ?? '知识库资料' : '我的知识库' : s?.page === 'workbench' ? '对话工作台' : s ? pageTitles[s.page] : '对话工作台'}{s?.page === 'knowledge' && view?.documents.isOpen && <StateTag value={view.documents.snapshot.base?.status ?? 'unverified'} />}</h1>
          <p>{s?.page === 'workbench' ? chatHeader && s.selectedKbId === null ? '普通聊天直接回答，不检索知识库' : '基于知识库资料，获得可验证的专业回答' : s?.page === 'knowledge' ? view?.documents.isOpen ? '资料上传、解析与入库分别记录' : '管理资料和固定的问答范围' : s?.page === 'tasks' ? '查看资料处理进度与失败原因' : s?.page === 'status' ? '查看本地服务与配置' : s?.page === 'voice' ? '当前聊天 · 语音和文字记录保存在一起' : '资料、任务与状态一目了然'}</p></div>
          </div>
        {!view || !api ? <Skeleton active /> : <PageBoundary key={s?.page}><Suspense fallback={<Skeleton active />}>{s?.basesError && <Alert showIcon type="error" title={s.basesError.message} />}
          {s?.page === 'workbench' && <Workbench view={view} api={api} />}{s?.page === 'knowledge' && <Knowledge view={view} api={api} />}
          {s?.page === 'tasks' && <Tasks view={view} />}{s?.page === 'status' && <Status view={view} api={api} />}{s?.page === 'voice' && <Voice {...view.voice} />}</Suspense></PageBoundary>}
      </main></div>
    <Drawer title={brand} open={navigation} onClose={() => setNavigation(false)} placement="left" size={280}>{menu}<Button block onClick={() => { setNavigation(false); setHistory(true); }}>打开聊天记录</Button></Drawer>
    <Drawer title="我的聊天" open={history} onClose={() => setHistory(false)} placement="left" size={340}>{chatList}</Drawer>
    <Modal title={dialog?.kind === 'prompt' ? dialog.text : '确认操作'} open={!!dialog} onCancel={() => finishDialog(dialog?.kind === 'confirm' ? false : null)}
      okText={dialog?.kind === 'prompt' ? '保存名称' : '确认'} okButtonProps={{ danger: dialog?.kind === 'confirm', disabled: dialog?.kind === 'prompt' && !dialog.value.trim() }}
      onOk={() => finishDialog(dialog?.kind === 'prompt' ? dialog.value : true)} destroyOnHidden>
      {dialog?.kind === 'confirm' ? <p>{dialog.text}</p> : <Input aria-label="聊天名称" autoFocus maxLength={120} value={dialog?.value ?? ''} onChange={(e) => setDialog((current) => current ? { ...current, value: e.target.value } : null)} />}
    </Modal>
  </div></Theme>;
}

export function StandaloneVoice({ api: suppliedApi, factory }: { api?: ApiClient; factory?: RoomFactory }) {
  const host = useRef<HTMLDivElement>(null); const [view, setView] = useState<AppView['voice'] | null>(null);
  useEffect(() => {
    let alive = true; let dispose = () => {}; const abort = new AbortController();
    const api = suppliedApi ?? createApi((input, init) => fetch(input, { ...init,
      signal: init?.signal ? AbortSignal.any([init.signal, abort.signal]) : abort.signal }));
    let conversation = new URLSearchParams(window.location.search).get('conversation');
    if (!conversation) try { const stored = sessionStorage.getItem('citerag.workbench.selection'); if (stored) {
      const value: unknown = JSON.parse(stored); if (value && typeof value === 'object' && 'chatId' in value && typeof value.chatId === 'string') conversation = value.chatId;
    } } catch { /* No restored convenience selection. */ }
    void mountVoicePage(host.current!, api, conversation, factory, undefined,
      { render: (value) => { if (alive) setView(value); }, onDispose: (fn) => { dispose = fn; } });
    return () => { alive = false; dispose(); abort.abort(); };
  }, [suppliedApi, factory]);
  return <Theme><div ref={host} className="citerag-app standalone-voice design-b"><header className="standalone-brand"><img src={logo} alt="回响 Logo" /><strong>CiteRAG</strong><span>语音通话</span></header>
    <main id="main-content" className="page-content"><h1>语音通话</h1>{view ? <Voice {...view} /> : <Skeleton active />}</main></div></Theme>;
}
