import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Drawer, Empty, Input, Popconfirm, Select, Space, Tag, Tooltip } from 'antd';
import { Bubble, Sender } from '@ant-design/x';
import { AudioOutlined, BookOutlined, FileTextOutlined, PictureOutlined } from '@ant-design/icons';
import type { ApiClient, KnowledgeMemory, ToolCallRecord, ToolInfo } from '../api/client';
import type { AppView } from '../app';
import { answerFailure } from '../pages/workbench';
import { locationText } from '../pages/sources';
import { displayCitations } from '../preview/model';
import { StateTag } from '../preview/shared';
import { answerRouteLabel } from './answerRoute';
import { AgentTasks } from './AgentTasks';
import { ToolInvocation, WeatherResult, toolFailure } from './ToolControls';
import logo from '../../../assets/brand/mark.svg';

const messageTime = (value: string) => new Date(value).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false });

function ToolResult({ call }: { call: ToolCallRecord }) {
  const result = call.result;
  if (!result) return null;
  if (call.tool_id.startsWith('weather.') && result.provider === 'QWeather' && result.source_type === 'tool')
    return <WeatherResult result={result} />;
  if (call.tool_id === 'local.time' && typeof result.time === 'string' &&
    Number.isFinite(Date.parse(result.time))) return <p className="tool-result">
      本机时间：<time dateTime={result.time}>{new Date(result.time).toLocaleString('zh-CN')}</time>
    </p>;
  if (call.tool_id === 'kb.documents' && Array.isArray(result.items) &&
    result.items.every((item) => item && typeof item === 'object' &&
      typeof item.filename === 'string' && typeof item.status === 'string'))
    return <div className="tool-result">{result.items.length ? <ul>{result.items.map((item, index) =>
      <li key={`${item.id || index}`}>{item.filename} · {item.status}</li>)}</ul> : '当前库没有可列出的资料。'}
      {result.truncated === true && <p>仅显示最近 20 份资料。</p>}</div>;
  return <pre className="tool-result">{JSON.stringify(result, null, 2)}</pre>;
}

export function Workbench({ view, api }: { view: AppView; api: ApiClient }) {
  const { state: s, actions: a } = view;
  const base = s.bases?.find((item) => item.id === s.selectedKbId);
  const busy = s.chatPending || s.chatMessages.some((item) => item.status === 'running');
  const enabled = (s.selectedKbId === null || base?.status === 'ready') && !!s.selectedChatId && !busy && !s.loading;
  const memorySources = s.chatMessages.filter((item) => item.status === 'answered' && !item.stale && !item.images?.length);
  const sourceMessage = base?.status === 'ready' ? s.chatMessages.find((item) => item.message_id === s.selectedCitation?.messageId && displayCitations(item).length > 0) : undefined;
  const citation = sourceMessage?.citations.find((item) => item.evidence_id === s.selectedCitation?.evidenceId);
  const root = useRef<HTMLDivElement>(null);
  const imageInput = useRef<HTMLInputElement>(null);
  const trigger = useRef('');
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [memories, setMemories] = useState<KnowledgeMemory[]>([]);
  const [memoryError, setMemoryError] = useState('');
  const [memoryBusy, setMemoryBusy] = useState(false);
  const [memorySource, setMemorySource] = useState('');
  const [memoryKind, setMemoryKind] = useState<KnowledgeMemory['kind']>('background');
  const [memoryText, setMemoryText] = useState('');
  const [toolOpen, setToolOpen] = useState(false);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [toolCalls, setToolCalls] = useState<ToolCallRecord[]>([]);
  const [toolError, setToolError] = useState('');
  const [toolBusy, setToolBusy] = useState(false);
  const [loadedToolChatId, setLoadedToolChatId] = useState<string | null>(null);
  const visibleTools = loadedToolChatId === s.selectedChatId ? tools : [];
  const visibleToolCalls = loadedToolChatId === s.selectedChatId ? toolCalls : [];
  const memorySourceEligible = memorySources.some((item) => item.message_id === memorySource);
  useEffect(() => {
    if (!memoryOpen || !base?.id) return;
    let active = true;
    void api.knowledgeMemories(base.id).then((items) => { if (active) { setMemories(items); setMemoryError(''); } },
      () => { if (active) setMemoryError('共享摘要读取失败，请重试。'); });
    return () => { active = false; };
  }, [memoryOpen, base?.id, api]);
  useEffect(() => {
    if (!toolOpen || !s.selectedChatId) return;
    let active = true;
    const chatId = s.selectedChatId;
    void Promise.all([api.conversationTools(chatId), api.toolCalls(chatId)]).then(([catalog, calls]) => {
      if (active) { setTools(catalog); setToolCalls(calls); setLoadedToolChatId(chatId); setToolError(''); }
    }, () => { if (active) setToolError('工具目录或记录读取失败，请重试。'); });
    return () => { active = false; };
  }, [toolOpen, s.selectedChatId, api]);
  async function runTool(toolId: string, arguments_: Record<string, unknown>) {
    if (!s.selectedChatId || toolBusy) return;
    const chatId = s.selectedChatId;
    setToolBusy(true); setToolError('');
    try {
      await api.invokeTool(chatId, toolId, crypto.randomUUID(), arguments_);
      setToolCalls(await api.toolCalls(chatId)); setLoadedToolChatId(chatId);
    } catch { setToolError('工具调用未确认成功，请检查聊天状态与服务后刷新记录。'); }
    finally { setToolBusy(false); }
  }
  async function decideTool(callId: string, approve: boolean) {
    if (!s.selectedChatId || toolBusy) return;
    const chatId = s.selectedChatId;
    setToolBusy(true); setToolError('');
    try {
      await api.decideTool(chatId, callId, approve);
      setToolCalls(await api.toolCalls(chatId)); setLoadedToolChatId(chatId);
    } catch { setToolError('审批未确认成功，请刷新记录并核对状态。'); }
    finally { setToolBusy(false); }
  }
  async function saveMemory() {
    if (!base || !memorySourceEligible || !memoryText.trim() || memoryBusy) return;
    setMemoryBusy(true); setMemoryError('');
    try {
      await api.createKnowledgeMemory(base.id, memorySource, memoryKind, memoryText.trim());
      setMemories(await api.knowledgeMemories(base.id)); setMemoryText('');
    } catch { setMemoryError('共享摘要保存失败：请核对来源、长度、知识库状态或数量限制。'); }
    finally { setMemoryBusy(false); }
  }
  async function deleteMemory(id: string) {
    if (!base || memoryBusy) return;
    setMemoryBusy(true); setMemoryError('');
    try { await api.deleteKnowledgeMemory(base.id, id); setMemories(await api.knowledgeMemories(base.id)); }
    catch { setMemoryError('删除失败，请刷新后重试。'); }
    finally { setMemoryBusy(false); }
  }
  useEffect(() => {
    if (citation || !trigger.current) return;
    let frame = 0;
    const restore = () => {
      if (document.querySelector('.formal-source-drawer')) return;
      observer.disconnect();
      frame = requestAnimationFrame(() => Array.from(root.current?.querySelectorAll<HTMLButtonElement>('[data-source-key]') ?? [])
        .find((button) => button.dataset.sourceKey === trigger.current)?.focus());
    };
    const observer = new MutationObserver(restore);
    observer.observe(document.body, { childList: true, subtree: true }); restore();
    return () => { observer.disconnect(); cancelAnimationFrame(frame); };
  }, [citation]);
  return <div ref={root} className="conversation-column formal-conversation">
    {s.selectedChatId && <div className="shared-memory-toolbar">
      <span>{base ? `当前对话固定知识库 · ${base.name}` : '普通聊天 · 无知识库访问'}</span>
      <Space wrap>{base && <Button size="small" onClick={() => setMemoryOpen(true)}>同库共享摘要</Button>}
        <Button size="small" onClick={() => setToolOpen(true)}>工具与调用记录</Button></Space>
    </div>}
    {s.chatError && <Alert showIcon type="error" title={s.chatError.message} />}
    {s.agentEnabled && s.selectedChatId && <AgentTasks key={s.selectedChatId} chatId={s.selectedChatId}
      api={api} changed={a.refreshAgentMessages} />}
    <div className="message-scroll" aria-live="polite" aria-busy={busy}>
      {!s.chatMessages.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={s.selectedChatId ? s.selectedKbId === null ? '直接提问；本聊天不会检索知识库' : '直接提问，自动区分交流与资料查询' : '新建普通聊天或选择知识库聊天'} />}
      {s.chatMessages.map((m) => {
        const readable = !m.hidden && !m.stale && (m.route === 'general' || m.route === 'chat' || base?.status === 'ready');
        const cited = base?.status === 'ready' && displayCitations(m).length > 0;
        const streaming = m.status === 'running' && s.chatStreamAttemptId === m.attempt_id;
        const content = !readable ? '知识库已变化，此回答与来源已暂停展示。'
          : m.phase === 'waiting_approval' ? '等待你核对并审批工具操作，本轮回答尚未完成。'
            : m.phase === 'waiting_input' ? '等待你补充参数，本轮回答尚未完成。'
              : m.status === 'running' || !m.saved ? '正在生成并核验，正文提交后再展示。'
          : ['failed', 'interrupted'].includes(m.status) ? answerFailure(m.error_code)
            : m.status === 'insufficient_evidence' ? '当前知识库没有足够的可核查证据，暂不作答。'
              : m.status === 'needs_clarification' ? m.text || '请补充查询对象或范围。'
                : m.status === 'conflicting_evidence' ? m.text || '当前资料存在冲突，请核查原文。' : m.text;
        return <article key={m.message_id} className="message-pair"><div className="question-line"><Bubble placement="end" className="question-bubble" content={m.question} /><time dateTime={m.created_at}>{messageTime(m.created_at)}</time></div>
          {!!m.images?.length && <div className="message-images">{m.images.map((image) =>
            <div key={image.id} className="message-image"><span>{image.filename}</span>
              {Date.parse(image.expires_at) > Date.now() ?
                <img src={api.imageUrl(s.selectedChatId!, image.id)} alt={`提问图片：${image.filename}`} /> :
                <span className="muted">图片已过期</span>}
              {image.observation && <p><strong>图片观察：</strong>{image.observation}</p>}
              {image.observation_status === 'failed' && <p className="failure-reason">图片识别失败，具体原因见下方。排除原因后可重试回答。</p>}
              {image.needs_confirmation && !image.confirmed_identifier && <Button disabled={busy}
                onClick={() => void a.confirmChatImage(m.message_id, image.id)}>确认图片编号</Button>}
              {image.confirmed_identifier && <p>已确认编号：{image.confirmed_identifier}</p>}
            </div>)}</div>}
          <div className="answer-line"><span className="answer-avatar"><img src={logo} alt="" /></span><div className="answer-card"><div className="answer-heading"><strong>{answerRouteLabel(m.route).split(' · ')[0]}</strong><StateTag value={m.status === 'answered' && !m.saved ? 'uncommitted' : m.status} />
            {!m.saved && <Tag>尚未保存</Tag>}</div>
            <div className="answer-text">{content}</div>
            {streaming && <p className="muted">{s.chatStreamSaved ? '结果已保存，等待最终提交' : '核验与保存处理中'}</p>}
            {readable && m.route !== 'needs_clarification' && <p className="muted">{answerRouteLabel(m.route)}</p>}
            {readable && m.error_code && <p className="failure-reason">{!['failed', 'interrupted'].includes(m.status) && <>{answerFailure(m.error_code)}<br /></>}{ /^[a-z][a-z0-9_]{0,63}$/.test(m.error_code) && <>错误代码：{m.error_code}</>}</p>}
            {cited && <div className="source-buttons">{m.citations.map((c) => <Button key={c.evidence_id} icon={<FileTextOutlined />} data-source-key={`${m.message_id}:${c.evidence_id}`}
              onClick={() => { trigger.current = `${m.message_id}:${c.evidence_id}`; a.selectCitation(m.message_id, c.evidence_id); }}>查看来源 · {c.filename} · {locationText(c)} <span aria-hidden="true">›</span></Button>)}</div>}
            {readable && (['failed', 'interrupted', 'partial'].includes(m.status) || m.status === 'needs_clarification' &&
              !!m.images?.some((image) => image.needs_confirmation) &&
              !!m.images?.every((image) => !image.needs_confirmation || !!image.confirmed_identifier)) && <Button className="retry-answer" disabled={busy || (s.selectedKbId !== null && base?.status !== 'ready')}
                onClick={() => void a.retryChat(m.message_id)}>重试回答</Button>}
          </div><time className="answer-time" dateTime={m.created_at}>{messageTime(m.created_at)}</time></div></article>;
      })}
    </div>
    <div className="composer"><input ref={imageInput} type="file" accept="image/png,image/jpeg" multiple hidden
      aria-label="选择提问图片" onChange={(event) => { a.selectChatImages(Array.from(event.target.files ?? [])); event.target.value = ''; }} />
      {!!s.chatImages.length && <div className="selected-images" aria-label="待发送图片">{s.chatImages.map((file, index) =>
        <span key={`${file.name}-${index}`}>{file.name} <Button size="small" disabled={busy}
          onClick={() => a.selectChatImages(s.chatImages.filter((_, item) => item !== index))}>移除</Button></span>)}</div>}
      <Sender value={s.chatDraft} onChange={(value) => a.setDraft(value.slice(0, 1000))} onSubmit={() => { if (enabled && s.chatDraft.trim()) void a.sendChat(); }}
      disabled={!enabled} loading={busy} autoSize={{ minRows: 2, maxRows: 6 }}
      placeholder={enabled ? s.selectedKbId === null ? '输入问题，直接使用普通回答' : '输入问题，自动识别普通交流或知识库查询'
        : busy && s.selectedChatId ? '当前任务未结束，请先补充、审批或取消' : '先新建或打开聊天'}
      suffix={false} footer={<div className="composer-controls"><Space wrap><Tooltip title="每条问题最多 2 张 PNG/JPEG，每张 10 MiB"><Button icon={<PictureOutlined />}
        disabled={!enabled} onClick={() => imageInput.current?.click()}>添加图片</Button></Tooltip>
        <span className="composer-kb"><BookOutlined />{s.selectedKbId === null ? '普通聊天' : base?.name || '未选择知识库'}</span></Space>
        <Space wrap><Button icon={<AudioOutlined />} disabled={busy} onClick={() => a.navigate('voice')}>语音通话</Button>
          <Button type="primary" loading={busy} disabled={!enabled || !s.chatDraft.trim()} onClick={() => void a.sendChat()}>发送问题 ↑</Button></Space></div>} />
      <p className="composer-note">{s.selectedKbId === null ? 'Enter 发送 · Shift+Enter 换行。本聊天不会检索知识库。' : 'Enter 发送 · Shift+Enter 换行。资料回答只展示已提交且有原文依据的结果。'}</p></div>
    <Drawer title="原文来源" aria-label="原文来源" rootClassName="formal-source-drawer" focusable={{ focusTriggerAfterClose: false }} open={!!citation} onClose={a.closeCitation} size={460} destroyOnHidden
      footer={citation && <div className="source-drawer-actions"><Button aria-label="下载原文" href={api.originalUrl(citation.document_id)} icon={<FileTextOutlined />}>下载原文</Button><Button type="primary" onClick={a.closeCitation}>关闭来源</Button></div>}>
      {citation && <div className="source-content"><div className="source-file-card"><h2><FileTextOutlined />{citation.filename}</h2><p><BookOutlined />{locationText(citation)}</p></div>
        <h3>原文内容（节选）</h3><blockquote>{citation.excerpt}</blockquote>
        <p className="muted">摘录、文件和定位均来自已保存的核验结果。</p>
        <div className="source-buttons">{sourceMessage?.citations.map((c) => <Button key={c.evidence_id} onClick={() => a.selectCitation(sourceMessage.message_id, c.evidence_id)}>{c.filename} · {locationText(c)}</Button>)}</div></div>}
    </Drawer>
    <Drawer title={`同库共享摘要 · ${base?.name || ''}`} open={memoryOpen && !!base} onClose={() => setMemoryOpen(false)} size={460} destroyOnHidden>
      <p className="muted">仅用于理解偏好和讨论背景。资料结论仍须重新检索当前知识库；这些条目不是引用。</p>
      {memoryError && <Alert type="error" showIcon title={memoryError} />}
      {memories.length ? memories.map((item) => <div key={item.id} className="memory-entry">
        <Tag color={item.valid ? 'blue' : 'default'}>{item.valid ? '可用' : '已失效'}</Tag>
        <strong>{item.kind === 'preference' ? '偏好' : '讨论背景'}</strong><p>{item.content}</p>
        <p className="muted">来源聊天：{s.chats?.find((chat) => chat.id === item.source_conversation_id)?.title || item.source_conversation_id} · 消息 {item.source_message_id.slice(0, 8)} · <time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString('zh-CN')}</time></p>
        <Popconfirm title="删除这条共享摘要？" onConfirm={() => void deleteMemory(item.id)}><Button size="small" danger disabled={memoryBusy}>删除</Button></Popconfirm>
      </div>) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无共享摘要" />}
      {!!s.selectedChatId && !!memorySources.length && <div className="memory-create"><h3>从本聊天提炼</h3>
        <Select aria-label="来源消息" style={{ width: '100%' }} placeholder="选择来源消息" value={memorySourceEligible ? memorySource : undefined}
          options={memorySources.map((item) => ({ value: item.message_id, label: item.question.slice(0, 80) }))}
          onChange={setMemorySource} />
        <Select aria-label="摘要类别" style={{ width: '100%' }} value={memoryKind} onChange={setMemoryKind}
          options={[{ value: 'preference', label: '偏好' }, { value: 'background', label: '讨论背景' }]} />
        <Input.TextArea aria-label="共享摘要内容" value={memoryText} maxLength={300} showCount rows={3}
          onChange={(event) => setMemoryText(event.target.value)} placeholder="只写可复用的偏好或讨论背景，不复制资料结论" />
        <Button type="primary" loading={memoryBusy} disabled={!memorySourceEligible || !memoryText.trim()}
          onClick={() => void saveMemory()}>保存共享摘要</Button>
      </div>}
      {!!s.selectedChatId && !memorySources.length && <p className="muted">本聊天暂无可作为来源的已完成纯文字提问。</p>}
    </Drawer>
    <Drawer title="工具与调用记录" open={toolOpen && !!s.selectedChatId} onClose={() => setToolOpen(false)} size="min(460px, 100vw)" destroyOnHidden>
      <p className="muted">工具调用由服务端检查聊天范围，结果单独记录，不作为知识库引用。仅显示已登记工具；历史结果只反映调用当时的状态。</p>
      {toolError && <Alert type="error" showIcon title={toolError} />}
      <h3>可用工具</h3>
      {visibleTools.map((tool) => <div key={tool.id} className="tool-entry"><strong>{tool.title}</strong>
        <p className="muted">{tool.impact}</p>
        <ToolInvocation tool={tool} disabled={toolBusy || busy} invoke={(arguments_) => void runTool(tool.id, arguments_)} /></div>)}
      {!visibleTools.length && <p className="muted">当前聊天暂无可用工具。</p>}
      <h3>最近调用</h3>
      {visibleToolCalls.map((call) => <div key={call.id} className="tool-entry"><strong>{call.tool_id}</strong>
        <Tag>{({ pending_approval: '待确认', running: '执行中', succeeded: '已完成', failed: '失败',
          rejected: '已拒绝', interrupted: '已中断', unknown: '结果未知，禁止自动重试' } as const)[call.status]}</Tag>
        <p className="muted">{call.impact} · <time dateTime={call.created_at}>{new Date(call.created_at).toLocaleString('zh-CN')}</time></p>
        {call.status === 'succeeded' && <ToolResult call={call} />}
        {call.error_code && <p className="failure-reason">{toolFailure(call.error_code)}<br />错误代码：{call.error_code}</p>}
        {call.status === 'pending_approval' && call.run_id && <p>此调用由任务执行器管理，请使用任务审批卡片。</p>}
        {call.status === 'pending_approval' && !call.run_id && <div><p>待确认参数：{JSON.stringify(call.arguments)}</p>
          <Space><Popconfirm title="确认按所示参数执行？" description={call.impact}
            onConfirm={() => void decideTool(call.id, true)}><Button size="small" type="primary" disabled={toolBusy}>确认执行</Button></Popconfirm>
            <Button size="small" disabled={toolBusy} onClick={() => void decideTool(call.id, false)}>拒绝</Button></Space></div>}
      </div>)}
      {!visibleToolCalls.length && <p className="muted">暂无调用记录。</p>}
    </Drawer>
  </div>;
}
