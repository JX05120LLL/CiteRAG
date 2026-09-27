import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { Alert, Button, ConfigProvider, Drawer, Empty, Input, Menu, Modal, Select, Skeleton } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { Conversations, XProvider } from '@ant-design/x';
import { AudioOutlined, BookOutlined, FileTextOutlined, MenuOutlined, MessageOutlined, PlusOutlined, ReloadOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
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
  const chat = s?.chats?.find((item) => item.id === s.selectedChatId);
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
  const menu = <Menu selectedKeys={[s?.page ?? 'workbench']} items={(Object.keys(pageTitles) as Page[]).map((key) => ({ key, icon: icons[key], label: pageTitles[key] }))}
    onClick={({ key }) => navigate(key as Page)} />;
  const brand = <div className="brand"><img src={logo} alt="回响 Logo" /><strong>CiteRAG</strong></div>;
  const chatList = <><Button type="primary" icon={<PlusOutlined />} block disabled={!base || base.status !== 'ready' || busy || mediaActive || s?.loading}
    onClick={() => { setHistory(false); view?.actions.navigate('workbench'); void view?.actions.createChat(); }}>新建聊天</Button>
    {s?.chatsError && <Alert type="error" title={s.chatsError.message} />}
    <Conversations items={(s?.chats ?? []).map((item) => ({ key: item.id, label: item.title }))} activeKey={s?.selectedChatId ?? undefined}
      onActiveChange={(id) => { if (!busy && !mediaActive) { setHistory(false); view?.actions.navigate('workbench'); void view?.actions.selectChat(id); } }} />
    {view?.hasMoreChats && <Button block disabled={busy || mediaActive} onClick={() => void view.actions.loadMoreChats()}>加载更多聊天</Button>}
    {!s?.chats?.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无已读取的聊天" />}</>;
  return <Theme><div ref={host} className="citerag-app design-b">
    <a className="skip-link" href="#main-content">跳到主要内容</a>
    <aside className="primary-sidebar">{brand}{menu}<Button className="history-button" icon={<MessageOutlined />} onClick={() => setHistory(true)}>聊天</Button>
      <div className="sidebar-foot">本地工作台<span>单用户</span></div></aside>
    <div className="product-main"><header className="context-bar"><Button className="mobile-nav-button" icon={<MenuOutlined />} aria-label="打开导航" onClick={() => setNavigation(true)} />
      <div className="context-title"><BookOutlined /><span>{base?.name || '选择知识库'}</span><StateTag value={base?.status ?? 'unverified'} /></div>
      <Select className="kb-select" aria-label="查看知识库" placeholder="选择知识库" value={s?.selectedKbId ?? undefined} disabled={busy || managementBusy || mediaActive || s?.loading}
        options={(s?.bases ?? []).map((item) => ({ value: item.id, label: item.name }))}
        onChange={(id) => { view?.actions.selectKb(id); if (s?.page === 'tasks' || s?.page === 'knowledge' && view?.documents.isOpen) {
          const next = s.bases?.find((item) => item.id === id); if (next) view?.actions.openDocuments(next); } }} />
      <Button icon={<MessageOutlined />} aria-label="打开聊天记录" onClick={() => setHistory(true)} />
      <Button icon={<ReloadOutlined />} aria-label="刷新工作台" disabled={busy || managementBusy || mediaActive || s?.loading} onClick={() => void view?.actions.refresh()} /></header>
      <main id="main-content" className={`page-content page-${s?.page ?? 'workbench'}`} tabIndex={-1}>
        <div className="page-heading"><div><div className="eyebrow">CiteRAG / 本地工作台</div><h1 tabIndex={-1}>{s ? pageTitles[s.page] : '对话工作台'}</h1>
          <p>{s?.page === 'workbench' ? chat?.title || '知识随对话展开' : s?.page === 'voice' ? '同一聊天与资料范围，显式开始连接' : '资料与任务按需展开'}</p></div>
          {s?.page === 'workbench' && <Button type="primary" icon={<PlusOutlined />} disabled={!base || base.status !== 'ready' || busy || s.loading}
            onClick={() => void view?.actions.createChat()}>新建聊天</Button>}</div>
        {!view || !api ? <Skeleton active /> : <PageBoundary key={s?.page}><Suspense fallback={<Skeleton active />}>{s?.basesError && <Alert showIcon type="error" title={s.basesError.message} />}
          {s?.page === 'workbench' && <Workbench view={view} api={api} />}{s?.page === 'knowledge' && <Knowledge view={view} api={api} />}
          {s?.page === 'tasks' && <Tasks view={view} />}{s?.page === 'status' && <Status view={view} />}{s?.page === 'voice' && <Voice {...view.voice} />}</Suspense></PageBoundary>}
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
    const api = suppliedApi ?? createApi((input, init) => fetch(input, { ...init, signal: abort.signal }));
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
