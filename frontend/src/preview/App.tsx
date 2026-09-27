import { useState } from 'react';
import { Alert, Button, ConfigProvider, Drawer, Empty, Menu, Select, Skeleton, Tooltip } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { Conversations, XProvider } from '@ant-design/x';
import { BookOutlined, MessageOutlined, MenuOutlined, FileTextOutlined, AudioOutlined, SafetyCertificateOutlined, ReloadOutlined, PlusOutlined } from '@ant-design/icons';
import type { Conversation, KnowledgeBase } from '../api/client';
import logo from '../../../assets/brand/mark.svg';
import { designs, pageTitles, parsePreviewLocation } from './model';
import type { PreviewLocation, PreviewPage } from './model';
import { usePreviewResource } from './useData';
import { Workbench } from './Workbench';
import { Knowledge, Tasks, Status } from './Management';
import { Voice } from './Voice';
import { DisabledAction, StateTag } from './shared';
import { theme } from '../react/theme';
export interface PreviewAppProps {
    initialLocation?: PreviewLocation;
}
export interface PageContext {
    location: PreviewLocation;
    kb: KnowledgeBase | undefined;
    chat: Conversation | undefined;
    refresh: number;
    navigate: (page: PreviewPage) => void;
}
const icons = { workbench: <MessageOutlined />, knowledge: <BookOutlined />, tasks: <FileTextOutlined />,
    status: <SafetyCertificateOutlined />, voice: <AudioOutlined /> };
export function PreviewApp({ initialLocation }: PreviewAppProps) {
    const [location, setLocation] = useState(() => initialLocation ?? parsePreviewLocation(window.location.search));
    const [refresh, setRefresh] = useState(0);
    const [kbId, setKbId] = useState('');
    const [chatId, setChatId] = useState('');
    const [navigationOpen, setNavigationOpen] = useState(false);
    const [chatsOpen, setChatsOpen] = useState(false);
    const bootstrap = usePreviewResource(location.data, `bootstrap:${refresh}`, async (api) => {
        const [bases, chats] = await Promise.all([api.knowledgeBases(), api.conversations()]);
        return { bases, chats };
    });
    const bases = bootstrap.data?.bases ?? [];
    const chats = bootstrap.data?.chats ?? [];
    const chat = chats.find((item) => item.id === chatId) ?? chats.find((item) => !kbId || item.kb_id === kbId);
    const kb = bases.find((item) => item.id === (location.page === 'workbench' || location.page === 'voice'
        ? chat?.kb_id ?? kbId : kbId)) ?? bases[0];
    function update(next: Partial<PreviewLocation>) {
        const value = { ...location, ...next };
        setLocation(value);
        const query = new URLSearchParams({ design: value.design, page: value.page, data: value.data,
            phase: value.phase, source: value.source ? 'open' : 'closed' });
        window.history.replaceState(null, '', `${window.location.pathname}?${query}`);
        setNavigationOpen(false);
    }
    const navigate = (page: PreviewPage) => update({ page });
    const context: PageContext = { location, kb, chat, refresh, navigate };
    const design = designs[location.design];
    const menu = <Menu selectedKeys={[location.page]} mode={'inline'} items={(Object.keys(pageTitles) as PreviewPage[]).map((key) => ({ key, icon: icons[key], label: pageTitles[key] }))} onClick={({ key }) => navigate(key as PreviewPage)}/>;
    const history = <div className="chat-history"><div className="section-label">我的聊天</div>
    <Conversations items={chats.map((item) => ({ key: item.id, label: item.title }))} activeKey={chat?.id} onActiveChange={(id) => {
            setChatId(id);
            setKbId(chats.find((item) => item.id === id)?.kb_id ?? '');
            navigate('workbench');
            setChatsOpen(false);
        }}/>
    {!chats.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无可读取的聊天"/>}</div>;
    const brand = <div className="brand"><img src={logo} alt="回响 Logo"/><strong>CiteRAG</strong></div>;
    return <ConfigProvider locale={zhCN} theme={theme} componentSize={design.density} button={{ autoInsertSpace: false }}>
    <XProvider theme={theme}><div className={`candidate-preview design-${location.design}`}>
      <div className="preview-toolbar">
        <div className="candidate-selector"><span className="preview-label">B 版只读设计预览</span>
          <strong className="candidate-name">{design.name}</strong></div>
        <div className="preview-tools"><Select aria-label="数据来源" value={location.data} options={[
            { value: 'live', label: '现有 API · 只读' }, { value: 'sample', label: '合成设计样例' }
        ]} onChange={(data) => { setKbId(''); setChatId(''); update({ data }); }}/>
          <Button icon={<ReloadOutlined />} aria-label="刷新读取" onClick={() => setRefresh((value) => value + 1)}/>
          <Tooltip title="设计预览禁用写操作与通话连接；实际操作请使用正式入口。"><span className="readonly-label">只读预览</span></Tooltip>
        </div>
      </div>
      {location.data === 'sample' && <div className="sample-banner" role="note">合成设计样例，不是实际业务或通话结果。</div>}
      <div className="product-shell">
        <aside className="primary-sidebar">{brand}{menu}
          <Button className="history-button" icon={<MessageOutlined />} onClick={() => setChatsOpen(true)} aria-label="打开聊天记录">聊天</Button>
          <div className="sidebar-foot">本地工作台<span>单用户 · 固定知识库</span></div></aside>
        <div className="product-main">
          <header className="context-bar">
            <Button className="mobile-nav-button" icon={<MenuOutlined />} aria-label="打开导航" onClick={() => setNavigationOpen(true)}/>
            <img className="mobile-brand-mark" src={logo} alt="CiteRAG 回响 Logo"/>
            <div className="context-title"><BookOutlined /><span>{kb?.name ?? '尚未读取知识库'}</span><StateTag value={kb?.status ?? 'unverified'}/></div>
            <Select className="kb-select" aria-label="查看知识库" value={kb?.id} placeholder="查看知识库" options={bases.map((item) => ({ value: item.id, label: item.name }))} onChange={(id) => {
            setKbId(id);
            setChatId('');
            if (location.page === 'voice' || location.page === 'workbench')
                navigate('knowledge');
        }}/>
            <Button className="chat-picker" icon={<MessageOutlined />} onClick={() => setChatsOpen(true)}>聊天记录</Button>
          </header>
          <div className={`page-grid page-${location.page}`}>
            <main className="page-content" id="preview-main">
              {bootstrap.loading && <div className="bootstrap-loading"><Skeleton active paragraph={{ rows: 1 }}/></div>}
              {bootstrap.error && <Alert type="warning" showIcon title="无法读取现有 API" description={<>
                <p>{bootstrap.error} 当前读取未完成，不展示合成成功结果。</p><Button onClick={() => { setKbId(''); setChatId(''); update({ data: 'sample' }); }}>查看设计样例</Button></>}/>}
              <div className="page-heading"><div><div className="eyebrow">CiteRAG / 本地工作台</div><h1>{pageTitles[location.page]}</h1>
                <p>{location.page === 'workbench' ? chat?.title ?? '选择聊天以查看真实记录' : design.summary}</p></div>
                <DisabledAction icon={<PlusOutlined />}>{location.page === 'workbench' ? '新建聊天' : location.page === 'knowledge' ? '创建知识库' : '写操作已禁用'}</DisabledAction></div>
              {location.page === 'workbench' && <Workbench key={`${location.data}:${chat?.id ?? ''}`} {...context}/>}
              {location.page === 'knowledge' && <Knowledge key={`${location.data}:${kb?.id ?? ''}`} {...context}/>}
              {location.page === 'tasks' && <Tasks key={`${location.data}:${kb?.id ?? ''}`} {...context}/>}
              {location.page === 'status' && <Status {...context}/>}
              {location.page === 'voice' && <Voice {...context} phase={location.phase} changePhase={(phase) => update({ phase })}/>}
            </main>
          </div>
        </div>
      </div>
      <Drawer title="页面导航" aria-label="页面导航" open={navigationOpen} onClose={() => setNavigationOpen(false)} destroyOnHidden placement="left" size={280}>
        <Menu selectedKeys={[location.page]} items={(Object.keys(pageTitles) as PreviewPage[]).map((key) => ({ key, icon: icons[key], label: pageTitles[key] }))} onClick={({ key }) => navigate(key as PreviewPage)}/></Drawer>
      <Drawer title="我的聊天" aria-label="我的聊天" open={chatsOpen} onClose={() => setChatsOpen(false)} destroyOnHidden size={360}>{history}</Drawer>
    </div></XProvider>
  </ConfigProvider>;
}
