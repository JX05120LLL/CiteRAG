import { StrictMode } from 'react';
import { webcrypto } from 'node:crypto';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { createApi } from '../api/client';
import type { AgentRun } from '../api/client';
import { CiteRagApp, StandaloneVoice } from './App';
import { Voice } from './Voice';
import type { VoiceActions } from '../pages/voice';
import { sampleBases, sampleChats, sampleDocuments, sampleJobs, sampleMessages, sampleVoice } from '../preview/samples';

beforeEach(() => {
  sessionStorage.setItem('citerag.workbench.selection', JSON.stringify({ kbId: sampleBases[0].id, chatId: sampleChats[0].id }));
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal('matchMedia', (media: string) => ({ media, matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
  const computed = window.getComputedStyle.bind(window);
  vi.spyOn(window, 'getComputedStyle').mockImplementation((element) => computed(element));
});
afterEach(() => { cleanup(); sessionStorage.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
const stream = (events: [string, unknown][]) => new Response(events.map(([name, data]) =>
  `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`).join(''), { headers: { 'Content-Type': 'text/event-stream' } });

it('disables chat creation when the business database is not configured and keeps setup errors visible', async () => {
  sessionStorage.clear();
  const writes = vi.fn();
  const api = createApi(async (url, init) => {
    if (init?.method === 'POST') writes();
    if (String(url) === '/api/agent/capability') return json({ enabled: false });
    return new Response(JSON.stringify({ detail: { code: 'database_not_configured', message: 'private connection details' } }),
      { status: 503, headers: { 'Content-Type': 'application/json' } });
  });
  render(<CiteRagApp api={api} />);
  await waitFor(() => expect(screen.getAllByText(/业务数据库尚未配置/).length).toBeGreaterThan(0));
  const ordinary = screen.getByRole<HTMLButtonElement>('button', { name: '新建聊天' });
  const knowledge = screen.getByRole<HTMLButtonElement>('button', { name: '新建知识库聊天' });
  expect(ordinary.disabled).toBe(true);
  expect(knowledge.disabled).toBe(true);
  expect(ordinary.title).toContain('业务数据库尚未配置');
  fireEvent.click(ordinary);
  expect(writes).not.toHaveBeenCalled();
  expect(document.body.textContent).not.toContain('private connection details');
}, 20000);

it('keeps a knowledge chat labelled as knowledge when its catalog cannot be read', async () => {
  const { api } = fixture();
  render(<CiteRagApp api={{ ...api, knowledgeBases: async () => { throw new Error('synthetic catalog failure'); } }} />);
  await waitFor(() => expect(document.querySelector('.shared-memory-toolbar')?.textContent).toContain('当前对话固定知识库'));
  expect(document.querySelector('.shared-memory-toolbar')?.textContent).not.toContain('普通聊天');
  expect(document.querySelector('.message-pair')).toBeTruthy();
  expect(screen.getByText('当前知识库状态暂不可核实，此回答与来源已暂停展示。请刷新后重试。')).toBeTruthy();
  expect(screen.queryByText('知识库已变化，此回答与来源已暂停展示。')).toBeNull();
  expect(document.querySelector('.composer-kb')?.textContent).toContain('当前聊天固定知识库 · 暂不可读取');
  expect(document.querySelector('.composer-kb')?.textContent).not.toContain('未选择知识库');
  expect(screen.getByPlaceholderText<HTMLTextAreaElement>('当前知识库状态暂不可核实，请刷新后再提问').disabled).toBe(true);
  expect(screen.queryByRole('button', { name: /查看来源/ })).toBeNull();
  expect(screen.queryByText(sampleMessages[sampleChats[0].id][0].text)).toBeNull();
  expect(screen.getByRole<HTMLButtonElement>('button', { name: '发送问题 ↑' }).disabled).toBe(true);
}, 20000);

it.each(['hidden', 'stale'] as const)('keeps the confirmed %s invalidation reason when the catalog also fails', async (flag) => {
  const message = { ...sampleMessages[sampleChats[0].id][0], [flag]: true };
  const { api } = fixture([message]);
  render(<CiteRagApp api={{ ...api, knowledgeBases: async () => { throw new Error('synthetic catalog failure'); } }} />);
  await waitFor(() => expect(screen.getByText('知识库已变化，此回答与来源已暂停展示。')).toBeTruthy());
  expect(screen.queryByText('当前知识库状态暂不可核实，此回答与来源已暂停展示。请刷新后重试。')).toBeNull();
  expect(screen.queryByText(message.text)).toBeNull();
  expect(screen.queryByRole('button', { name: /查看来源/ })).toBeNull();
  expect(screen.getByRole<HTMLButtonElement>('button', { name: '发送问题 ↑' }).disabled).toBe(true);
}, 20000);

function assistantActions(): VoiceActions {
  return { capability: { transport: 'configured', assistant: 'configured', purpose: 'voice_assistant' },
    checking: false, capabilityError: false,
    media: { phase: 'connecting', assistantPhase: 'not_configured', muted: false, outputMuted: false,
      busy: false, remoteAudio: 0, playbackRequired: false, meterUnavailable: false, error: null,
      answers: sampleMessages[sampleChats[0].id] },
    refresh: vi.fn(async () => {}), connect: vi.fn(), hangup: vi.fn(), microphone: vi.fn(),
    output: vi.fn(), back: vi.fn(), status: vi.fn(), originalUrl: id => `/api/documents/${id}/original` };
}

it('distinguishes configured, connecting and ready voice states without false configuration failure', () => {
  const actions = assistantActions();
  const context = { chatId: 'chat', chatTitle: '合成语音', kbName: '合成库', kbReady: true, chatPending: false };
  const view = render(<Voice context={context} actions={actions} />);
  expect(screen.getByRole('status').textContent).toBe('等待助手就绪');
  expect(screen.queryByText('已配置 · 未验证连接')).toBeNull();
  view.rerender(<Voice context={context} actions={{ ...actions,
    media: { ...actions.media, phase: 'idle', assistantPhase: 'ended' } }} />);
  expect(screen.getByRole('status').textContent).toBe('尚未开始，助手连接待验证');
  expect(screen.getByText('媒体待连接')).toBeTruthy();
  expect(screen.getByText('助手待连接')).toBeTruthy();
  view.rerender(<Voice context={context} actions={{ ...actions,
    media: { ...actions.media, phase: 'connected', assistantPhase: 'listening' } }} />);
  expect(screen.getByRole('status').textContent).toBe('助手就绪，等待说话');
});

it('downloads a voice source without navigating the active call document', async () => {
  const actions = assistantActions();
  render(<Voice context={{ chatId: 'chat', chatTitle: '合成语音', kbName: '合成库', kbReady: true, chatPending: false }} actions={actions} />);
  const citation = sampleMessages[sampleChats[0].id][0].citations[0];
  fireEvent.click(screen.getByRole('button', { name: new RegExp(citation.filename) }));
  const drawer = await screen.findByRole('dialog');
  expect(within(drawer).getByRole('link', { name: '下载原文' }).getAttribute('download')).toBe(citation.filename);
  expect(actions.hangup).not.toHaveBeenCalled();
});
function fixture(messages = sampleMessages[sampleChats[0].id], chats = sampleChats, weather = false,
  toolResponse: { status?: 'failed'; loseFirstResponse?: boolean; beforeResponse?: () => void } = {}) {
  const calls: { path: string; method: string; body: unknown }[] = [];
  let archived = false;
  let toolRecords: unknown[] = [];
  let memories = [{ id: 'synthetic-memory', kb_id: sampleBases[0].id, kind: 'preference',
    content: '合成偏好：简短回答', source_conversation_id: sampleChats[0].id,
    source_message_id: messages[0].message_id, created_at: '2026-09-29T00:00:00Z', valid: true }];
  const fetcher: typeof fetch = async (input, init) => {
    const path = String(input); const method = init?.method ?? 'GET'; calls.push({ path, method, body: init?.body });
    if (path === '/api/conversations' && method === 'POST') return json({
      id: 'synthetic-general', owner_id: 'synthetic-owner', kb_id: null,
      title: '新聊天', created_at: '2026-09-29T00:00:00Z', archived_at: null,
    });
    if (path.endsWith('/memories') && method === 'GET') return json({ items: memories });
    if (path.endsWith('/tools') && method === 'GET') return json({ items: [
      { id: 'local.time', title: '本机当前时间', scope: 'any', approval_required: false,
        impact: '读取本机当前 UTC 时间；不访问知识库或外部服务。' },
      { id: 'kb.documents', title: '当前知识库资料目录', scope: 'knowledge', approval_required: false,
        impact: '只读取当前聊天绑定知识库的资料名称与处理状态；不读取正文。' },
      ...(weather ? [{ id: 'weather.current', title: '和风实时天气', scope: 'any', approval_required: true,
        impact: '合成天气测试：将坐标发送和风天气，可能按量计费。', input_schema: {
          type: 'object', additionalProperties: false, required: ['latitude', 'longitude'], properties: {
            latitude: { type: 'number', title: '纬度', minimum: -90, maximum: 90 },
            longitude: { type: 'number', title: '经度', minimum: -180, maximum: 180 },
          },
        } }] : []),
    ] });
    if (path.endsWith('/tools/calls') && method === 'GET') return json({ items: toolRecords });
    if (path.endsWith('/tools/calls') && method === 'POST') {
      const body = JSON.parse(String(init?.body));
      const existing = (toolRecords as { request_id: string }[]).find((item) => item.request_id === body.request_id);
      if (existing) return json(existing);
      const record = { id: toolRecords.length ? `synthetic-tool-call-${toolRecords.length}` : 'synthetic-tool-call', conversation_id: sampleChats[0].id,
        request_id: body.request_id, kb_id: sampleBases[0].id, tool_id: body.tool_id,
        arguments: body.arguments, impact: body.tool_id.startsWith('weather.') ?
          '合成天气测试：将坐标发送和风天气，可能按量计费。' : '读取本机当前 UTC 时间；不访问知识库或外部服务。',
        status: body.tool_id.startsWith('weather.') ? 'pending_approval' : toolResponse.status ?? 'succeeded',
        result: body.tool_id.startsWith('weather.') || toolResponse.status === 'failed' ? null : { time: '2026-09-30T00:00:00Z' },
        error_code: toolResponse.status === 'failed' ? 'mcp_tool_failed' : null,
        created_at: '2026-09-30T00:00:00Z', approved_at: null,
        finished_at: '2026-09-30T00:00:00Z', source_type: 'tool' };
      toolRecords = [record, ...toolRecords];
      toolResponse.beforeResponse?.();
      if (toolResponse.loseFirstResponse && toolRecords.length === 1) throw new TypeError('synthetic response lost');
      return json(record);
    }
    if (path.endsWith('/synthetic-tool-call/decision') && method === 'POST') {
      const approved = JSON.parse(String(init?.body)).approve;
      const record = { ...toolRecords[0] as Record<string, unknown>,
        status: approved ? 'succeeded' : 'rejected', result: approved ? {
          provider: 'QWeather', source_type: 'tool', kind: 'current',
          queried_at: '2026-10-01T00:00:00Z', location: { latitude: 0, longitude: 0 },
          current: { condition: '合成少云', temperature: { value: 25, unit: '°C' }, humidity_percent: 69 },
          attributions: ['https://developer.qweather.com/attribution.html'],
        } : null };
      toolRecords = [record]; return json(record);
    }
    if (path.includes('/memories/') && method === 'DELETE') { memories = []; return json({ deleted: true }); }
    if (path.endsWith(`/conversations/${sampleChats[0].id}/archive`) && method === 'PATCH') {
      archived = JSON.parse(String(init?.body)).archived as boolean;
      return json({ ...sampleChats[0], archived_at: archived ? '2026-09-29T00:00:00Z' : null });
    }
    if (path.startsWith('/api/conversations?')) return json({ items: path.includes('archived=true')
      ? archived ? [sampleChats[0]] : [] : archived ? chats.filter((item) => item.id !== sampleChats[0].id) : chats });
    if (path === '/api/knowledge-bases') return json({ items: sampleBases });
    if (path.endsWith('/voice/token')) return json({ server_url: 'ws://127.0.0.1:7880', token: 'synthetic', room: 'synthetic', conversation_id: sampleChats[0].id, assistant: 'not_configured', purpose: 'media_test' });
    if (path === '/api/voice/status') return json(sampleVoice);
    if (path.includes('/documents?')) return json({ items: sampleDocuments.slice(path.includes('offset=10') ? 10 : 0, path.includes('offset=10') ? 20 : 10), total: 17, counts: { ready: 16, failed: 1, deleted: 0 } });
    if (path.includes('/jobs?')) return json({ items: sampleJobs.slice(0, 10), total: 12, active_items: [], failed_count: 2 });
    if (path.endsWith('/messages')) return json({ items: messages });
    if (path.endsWith('/retry/stream') && method === 'POST') {
      const original = messages.find((item) => path.endsWith(`/messages/${encodeURIComponent(item.message_id)}/retry/stream`));
      if (!original) return new Response(null, { status: 404 });
      const { attempt_id } = JSON.parse(String(init?.body));
      const answer = { ...original, attempt_id, status: 'answered', text: '合成重试结果', citations: [], saved: true, error_code: null };
      return stream([
        ['accepted', { ...answer, status: 'running', text: '', saved: false }],
        ['delta', { attempt_id, seq: 1, text: answer.text, saved: true }],
        ['saved', answer],
      ]);
    }
    if (path.includes('/conversations/') && method === 'PATCH') return json({ ...sampleChats[0], title: '改后的合成名称' });
    if (path.endsWith(`/conversations/${sampleChats[0].id}`)) return json(sampleChats[0]);
    return json({ items: chats });
  };
  return { api: createApi(fetcher), calls, recordCount: () => toolRecords.length };
}

it('lets a waiting task stay in its chat while another chat is opened', async () => {
  const pendingMessages = [{ ...sampleMessages[sampleChats[0].id][0],
    status: 'running' as const, phase: 'waiting_input' as const }];
  const pendingRun: AgentRun = {
    id: 'pending-run', conversation_id: sampleChats[0].id,
    message_id: pendingMessages[0].message_id, attempt_id: pendingMessages[0].attempt_id,
    status: 'waiting_input', generation: 1, seq: 2, model_rounds: 4, tool_attempts: 0,
    active_ms: 20, error_code: null, voice_session_id: null,
    created_at: '2026-10-07T00:00:00Z', finished_at: null,
    waiting: { kind: 'input', prompt: 'Which location?', fields: { location: 'string' } },
  };
  const { api, calls } = fixture(pendingMessages);
  const cancelAgent = vi.fn();
  const resumeAgent = vi.fn();
  render(<CiteRagApp api={{ ...api,
    agentRuns: async (chatId) => chatId === sampleChats[0].id ? [pendingRun] : [],
    cancelAgent, resumeAgent,
  }} />);
  await waitFor(() => expect(document.querySelector('.message-pair')).toBeTruthy());
  const newChat = screen.getByRole<HTMLButtonElement>('button', { name: '新建聊天' });
  const otherChat = screen.getByRole<HTMLButtonElement>('button', { name: sampleChats[1].title });
  expect(newChat.disabled).toBe(false);
  expect(otherChat.disabled).toBe(false);
  fireEvent.click(otherChat);
  await waitFor(() => expect(JSON.parse(sessionStorage.getItem('citerag.workbench.selection') || '{}').chatId)
    .toBe(sampleChats[1].id));
  expect(cancelAgent).not.toHaveBeenCalled();
  expect(resumeAgent).not.toHaveBeenCalled();
  fireEvent.click(newChat);
  await waitFor(() => expect(calls.some((call) => call.path === '/api/conversations' &&
    call.method === 'POST' && JSON.parse(String(call.body)).kb_id === null)).toBe(true));
  expect(cancelAgent).not.toHaveBeenCalled();
  expect(resumeAgent).not.toHaveBeenCalled();
}, 20000);

it('lists chat titles without knowledge-base groups and restores archived chats', async () => {
  const { api, calls } = fixture();
  render(<CiteRagApp api={api} />);
  const rowMenu = await screen.findByRole('button', { name: `${sampleChats[0].title}的更多操作` });
  const list = screen.getByLabelText('对话列表');
  expect(within(list).getAllByRole('button', { name: sampleChats[0].title })).toHaveLength(1);
  expect(list.textContent).not.toContain(sampleBases[0].name);
  fireEvent.click(rowMenu);
  fireEvent.click(await screen.findByRole('menuitem', { name: '归档对话' }));
  await waitFor(() => expect(calls.some((call) => call.path.endsWith('/archive') && call.method === 'PATCH')).toBe(true));
  fireEvent.click(screen.getByRole('button', { name: /已归档/ }));
  fireEvent.click(await screen.findByRole('button', { name: '恢复' }));
  await waitFor(() => expect(calls.filter((call) => call.path.endsWith('/archive') && call.method === 'PATCH').length).toBe(2));
}, 20000);

it('creates an ordinary chat with one click and lists source-linked shared memory for a KB chat', async () => {
  const { api, calls } = fixture();
  render(<CiteRagApp api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: '新建聊天' }));
  await waitFor(() => expect(calls.some((call) => call.path === '/api/conversations' &&
    call.method === 'POST' && JSON.parse(String(call.body)).kb_id === null)).toBe(true));
  expect((await screen.findAllByText('普通聊天 · 不检索知识库')).length).toBeGreaterThan(0);
  expect(screen.queryByRole('combobox', { name: '查看知识库' })).toBeNull();
  expect(screen.getByText('普通聊天直接回答，不检索知识库')).toBeTruthy();
  fireEvent.click(await screen.findByRole('button', { name: sampleChats[0].title }));
  fireEvent.click(await screen.findByRole('button', { name: '同库共享摘要' }));
  expect(await screen.findByText('合成偏好：简短回答')).toBeTruthy();
  expect(calls.some((call) => call.path.endsWith(`/knowledge-bases/${sampleBases[0].id}/memories`))).toBe(true);
}, 20000);

it.each([false, true])('shows the actual automatic tool state without disabling manual registered tools (%s)', async (enabled) => {
  const { api, calls } = fixture();
  const capability = vi.fn(async () => ({ enabled }));
  render(<CiteRagApp api={{ ...api, agentCapability: capability, agentRuns: async () => [] }} />);
  fireEvent.click(await screen.findByRole('button', { name: '工具与调用记录' }, { timeout: 8000 }));
  const drawer = await screen.findByRole('dialog');
  const invoke = await within(drawer).findByRole<HTMLButtonElement>('button', { name: '调用工具：本机当前时间' });
  expect(capability).toHaveBeenCalled();
  const disabledNotice = within(drawer).queryByText('自动工具未启用，聊天不会自动调用；仍可在此手动调用可用工具。');
  if (enabled) expect(disabledNotice).toBeNull();
  else expect(disabledNotice).toBeTruthy();
  expect(invoke.disabled).toBe(false);
  fireEvent.click(invoke);
  expect(await within(drawer).findByText(/本机时间：/)).toBeTruthy();
  expect(calls.some((call) => call.path.endsWith('/tools/calls') && call.method === 'POST')).toBe(true);
}, 20000);

it('runs a registered tool through the API and displays the separate durable result', async () => {
  const { api, calls } = fixture();
  render(<CiteRagApp api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: '工具与调用记录' },
    { timeout: 8000 }));
  const drawer = await screen.findByRole('dialog');
  expect(within(drawer).getByText('当前知识库资料目录')).toBeTruthy();
  fireEvent.click(within(drawer).getByRole('button', { name: '调用工具：本机当前时间' }));
  expect(await within(drawer).findByText(/本机时间：/)).toBeTruthy();
  expect(calls.some((call) => call.path.endsWith('/tools/calls') && call.method === 'POST')).toBe(true);
  expect(within(drawer).getByText(/不作为知识库引用/)).toBeTruthy();
}, 20000);

it('reuses one request id when a completed tool call is clicked again immediately', async () => {
  const { api, calls, recordCount } = fixture();
  render(<CiteRagApp api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: '工具与调用记录' }));
  const drawer = await screen.findByRole('dialog');
  const button = within(drawer).getByRole<HTMLButtonElement>('button', { name: '调用工具：本机当前时间' });
  fireEvent.click(button);
  await waitFor(() => expect(calls.filter((call) => call.path.endsWith('/tools/calls') && call.method === 'POST').length).toBe(1));
  await waitFor(() => expect(button.disabled).toBe(false));
  fireEvent.click(button);
  await waitFor(() => expect(calls.filter((call) => call.path.endsWith('/tools/calls') && call.method === 'POST').length).toBe(2));
  const requests = calls.filter((call) => call.path.endsWith('/tools/calls') && call.method === 'POST');
  expect(JSON.parse(String(requests[1].body)).request_id).toBe(JSON.parse(String(requests[0].body)).request_id);
  expect(recordCount()).toBe(1);
}, 20000);

it('keeps the request id for a repeat immediately after a slow success', async () => {
  let now = 1000;
  vi.spyOn(Date, 'now').mockImplementation(() => now);
  const { api, calls, recordCount } = fixture(undefined, undefined, false, { beforeResponse: () => { now = 12000; } });
  render(<CiteRagApp api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: '工具与调用记录' }));
  const button = within(await screen.findByRole('dialog')).getByRole<HTMLButtonElement>('button', { name: '调用工具：本机当前时间' });
  fireEvent.click(button);
  await waitFor(() => expect(button.disabled).toBe(false));
  now = 12001;
  fireEvent.click(button);
  await waitFor(() => expect(calls.filter((call) => call.path.endsWith('/tools/calls') && call.method === 'POST').length).toBe(2));
  const requests = calls.filter((call) => call.path.endsWith('/tools/calls') && call.method === 'POST');
  expect(JSON.parse(String(requests[1].body)).request_id).toBe(JSON.parse(String(requests[0].body)).request_id);
  expect(recordCount()).toBe(1);
}, 20000);

it('uses a new request id after a durable failed tool call', async () => {
  const { api, calls, recordCount } = fixture(undefined, undefined, false, { status: 'failed' });
  render(<CiteRagApp api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: '工具与调用记录' }));
  const button = within(await screen.findByRole('dialog')).getByRole<HTMLButtonElement>('button', { name: '调用工具：本机当前时间' });
  fireEvent.click(button);
  await waitFor(() => expect(button.disabled).toBe(false));
  fireEvent.click(button);
  await waitFor(() => expect(calls.filter((call) => call.path.endsWith('/tools/calls') && call.method === 'POST').length).toBe(2));
  const requests = calls.filter((call) => call.path.endsWith('/tools/calls') && call.method === 'POST');
  expect(JSON.parse(String(requests[1].body)).request_id).not.toBe(JSON.parse(String(requests[0].body)).request_id);
  expect(recordCount()).toBe(2);
}, 20000);

it('reuses an uncertain tool request id after timeout and browser remount', async () => {
  vi.stubGlobal('crypto', webcrypto);
  let now = 1000;
  vi.spyOn(Date, 'now').mockImplementation(() => now);
  const { api, calls, recordCount } = fixture(undefined, undefined, false,
    { loseFirstResponse: true, beforeResponse: () => { now = 12000; } });
  render(<CiteRagApp api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: '工具与调用记录' }, { timeout: 8000 }));
  fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '调用工具：本机当前时间' }));
  await waitFor(() => expect(calls.filter((call) => call.path.endsWith('/tools/calls') && call.method === 'POST').length).toBe(1));
  cleanup();
  render(<CiteRagApp api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: '工具与调用记录' }));
  const button = within(await screen.findByRole('dialog')).getByRole<HTMLButtonElement>('button', { name: '调用工具：本机当前时间' });
  now = 12001;
  fireEvent.click(button);
  await waitFor(() => expect(calls.filter((call) => call.path.endsWith('/tools/calls') && call.method === 'POST').length).toBe(2));
  const requests = calls.filter((call) => call.path.endsWith('/tools/calls') && call.method === 'POST');
  expect(JSON.parse(String(requests[1].body)).request_id).toBe(JSON.parse(String(requests[0].body)).request_id);
  expect(recordCount()).toBe(1);
}, 20000);

it('keeps an uncertain request id when another tool is called before a remount', async () => {
  vi.stubGlobal('crypto', webcrypto);
  let now = 1000;
  vi.spyOn(Date, 'now').mockImplementation(() => now);
  const { api, calls, recordCount } = fixture(undefined, undefined, false,
    { loseFirstResponse: true, beforeResponse: () => { now = 12000; } });
  render(<CiteRagApp api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: '工具与调用记录' }));
  const drawer = await screen.findByRole('dialog');
  fireEvent.click(within(drawer).getByRole('button', { name: '调用工具：本机当前时间' }));
  await waitFor(() => expect(calls.filter((call) => call.path.endsWith('/tools/calls') && call.method === 'POST').length).toBe(1));
  fireEvent.click(within(drawer).getByRole('button', { name: '调用工具：当前知识库资料目录' }));
  await waitFor(() => expect(calls.filter((call) => call.path.endsWith('/tools/calls') && call.method === 'POST').length).toBe(2));
  cleanup();
  render(<CiteRagApp api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: '工具与调用记录' }));
  now = 12001;
  fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '调用工具：本机当前时间' }));
  await waitFor(() => expect(calls.filter((call) => call.path.endsWith('/tools/calls') && call.method === 'POST').length).toBe(3));
  const requests = calls.filter((call) => call.path.endsWith('/tools/calls') && call.method === 'POST');
  expect(JSON.parse(String(requests[2].body)).request_id).toBe(JSON.parse(String(requests[0].body)).request_id);
  expect(recordCount()).toBe(2);
}, 20000);

it('settles a pending request id when approval happens after a remount', async () => {
  vi.stubGlobal('crypto', webcrypto);
  let now = 1000;
  vi.spyOn(Date, 'now').mockImplementation(() => now);
  const { api, calls, recordCount } = fixture(undefined, undefined, true);
  render(<CiteRagApp api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: '工具与调用记录' }));
  let drawer = await screen.findByRole('dialog');
  fireEvent.change(within(drawer).getByLabelText('纬度'), { target: { value: '0' } });
  fireEvent.change(within(drawer).getByLabelText('经度'), { target: { value: '0' } });
  fireEvent.click(within(drawer).getByRole('button', { name: '调用工具：和风实时天气' }));
  await within(drawer).findByText('待确认');
  await waitFor(() => expect(within(drawer).getByRole<HTMLButtonElement>('button', { name: '确认执行' }).disabled).toBe(false));
  cleanup();
  render(<CiteRagApp api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: '工具与调用记录' }));
  drawer = await screen.findByRole('dialog');
  await within(drawer).findByText('待确认');
  now = 12000;
  fireEvent.click(within(drawer).getByRole('button', { name: '确认执行' }));
  fireEvent.click(await screen.findByRole('button', { name: '确定' }));
  await waitFor(() => expect(calls.filter((item) => item.path.endsWith('/decision')).length).toBe(1));
  fireEvent.change(within(drawer).getByLabelText('纬度'), { target: { value: '0' } });
  fireEvent.change(within(drawer).getByLabelText('经度'), { target: { value: '0' } });
  await waitFor(() => expect(within(drawer).getByRole<HTMLButtonElement>('button', { name: '调用工具：和风实时天气' }).disabled).toBe(false));
  now = 14000;
  fireEvent.click(within(drawer).getByRole('button', { name: '调用工具：和风实时天气' }));
  await waitFor(() => expect(calls.filter((call) => call.path.endsWith('/tools/calls') && call.method === 'POST').length).toBe(2));
  const requests = calls.filter((call) => call.path.endsWith('/tools/calls') && call.method === 'POST');
  expect(JSON.parse(String(requests[1].body)).request_id).not.toBe(JSON.parse(String(requests[0].body)).request_id);
  expect(recordCount()).toBe(2);
}, 20000);

it.each([false, true])('shows disabled automatic tools before sending and opens existing manual tools (%s knowledge)', async (knowledge) => {
  const chat = { ...sampleChats[0], kb_id: knowledge ? sampleBases[0].id : null };
  sessionStorage.setItem('citerag.workbench.selection', JSON.stringify({ kbId: chat.kb_id, chatId: chat.id }));
  const { api, calls } = fixture(undefined, [chat]);
  render(<CiteRagApp api={{ ...api, agentCapability: async () => ({ enabled: false }) }} />);
  const notice = await screen.findByText('自动工具未启用，实时天气等外部信息不会自动查询');
  expect(document.querySelector('.composer')?.contains(notice)).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '查看可用工具' }));
  const drawer = await screen.findByRole('dialog');
  await within(drawer).findByRole('button', { name: '调用工具：本机当前时间' });
  expect(calls.some((call) => call.path.endsWith('/tools'))).toBe(true);
  expect(calls.every((call) => call.method === 'GET')).toBe(true);
}, 20000);

it('does not report disabled automatic tools when the assistant is enabled', async () => {
  const { api } = fixture();
  render(<CiteRagApp api={{ ...api, agentCapability: async () => ({ enabled: true }), agentRuns: async () => [] }} />);
  await screen.findByRole('button', { name: '工具与调用记录' });
  await waitFor(() => expect(document.querySelector('.agent-tasks')).toBeTruthy());
  expect(screen.queryByText('自动工具未启用，实时天气等外部信息不会自动查询')).toBeNull();
  expect(screen.queryByRole('button', { name: '查看可用工具' })).toBeNull();
}, 20000);

it('waits for the actual automatic tool capability before showing the disabled notice', async () => {
  const { api, calls } = fixture();
  let resolveCapability!: (value: { enabled: boolean }) => void;
  const capability = new Promise<{ enabled: boolean }>((resolve) => { resolveCapability = resolve; });
  render(<CiteRagApp api={{ ...api, agentCapability: () => capability }} />);
  await screen.findByRole('button', { name: '工具与调用记录' });
  expect(screen.queryByText('自动工具未启用，实时天气等外部信息不会自动查询')).toBeNull();
  expect(screen.queryByRole('button', { name: '查看可用工具' })).toBeNull();
  resolveCapability({ enabled: false });
  await screen.findByText('自动工具未启用，实时天气等外部信息不会自动查询');
  expect(calls.every((call) => call.method === 'GET')).toBe(true);
}, 20000);

it('does not offer manual tool invocation before a chat is selected', async () => {
  const { api } = fixture(undefined, []);
  render(<CiteRagApp api={{ ...api, agentCapability: async () => ({ enabled: false }) }} />);
  await screen.findByPlaceholderText('先新建或打开聊天');
  expect(screen.queryByText('自动工具未启用，实时天气等外部信息不会自动查询')).toBeNull();
  expect(screen.queryByRole('button', { name: '查看可用工具' })).toBeNull();
}, 20000);

it.each([false, true])('distinguishes failed tool capability and refreshes to confirmed %s without writes', async (enabled) => {
  const { api, calls } = fixture();
  const capability = vi.fn().mockRejectedValueOnce(new TypeError('synthetic capability unavailable'))
    .mockResolvedValue({ enabled });
  render(<CiteRagApp api={{ ...api, agentCapability: capability, agentRuns: async () => [] }} />);
  const failure = await screen.findByText('自动工具状态读取失败，请刷新后重试');
  const composer = document.querySelector('.composer') as HTMLElement;
  expect(composer.contains(failure)).toBe(true);
  expect(screen.queryByText('自动工具未启用，实时天气等外部信息不会自动查询')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '工具与调用记录' }));
  const drawer = await screen.findByRole('dialog');
  expect(within(drawer).getByText('自动工具状态读取失败，请刷新后重试')).toBeTruthy();
  expect(within(drawer).queryByText('自动工具未启用，聊天不会自动调用；仍可在此手动调用可用工具。')).toBeNull();
  fireEvent.click(within(composer).getByRole('button', { name: '刷新工具状态' }));
  await waitFor(() => expect(capability).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.queryAllByText('自动工具状态读取失败，请刷新后重试')).toHaveLength(0));
  if (enabled) {
    await waitFor(() => expect(document.querySelector('.agent-tasks')).toBeTruthy());
    expect(screen.queryByText('自动工具未启用，实时天气等外部信息不会自动查询')).toBeNull();
    expect(within(drawer).queryByText('自动工具未启用，聊天不会自动调用；仍可在此手动调用可用工具。')).toBeNull();
  } else {
    expect(screen.getByText('自动工具未启用，实时天气等外部信息不会自动查询')).toBeTruthy();
    expect(within(drawer).getByText('自动工具未启用，聊天不会自动调用；仍可在此手动调用可用工具。')).toBeTruthy();
  }
  expect(calls.every((call) => call.method === 'GET')).toBe(true);
  expect(document.body.textContent).not.toContain('synthetic capability unavailable');
}, 20000);

it('does not let an older failed capability read overwrite a successful refresh', async () => {
  const { api, calls } = fixture();
  let rejectInitial!: (error: Error) => void;
  const initial = new Promise<{ enabled: boolean }>((_, reject) => { rejectInitial = reject; });
  const capability = vi.fn().mockReturnValueOnce(initial).mockResolvedValue({ enabled: true });
  render(<CiteRagApp api={{ ...api, agentCapability: capability, agentRuns: async () => [] }} />);
  fireEvent.click(await screen.findByRole('button', { name: '刷新工作台' }));
  await waitFor(() => expect(capability).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(document.querySelector('.agent-tasks')).toBeTruthy());
  await act(async () => rejectInitial(new TypeError('synthetic late capability failure')));
  expect(screen.queryByText('自动工具状态读取失败，请刷新后重试')).toBeNull();
  expect(screen.queryByText('自动工具未启用，实时天气等外部信息不会自动查询')).toBeNull();
  expect(document.querySelector('.agent-tasks')).toBeTruthy();
  expect(calls.every((call) => call.method === 'GET')).toBe(true);
}, 20000);

it('submits explicit weather coordinates and only shows an attributed result after approval', async () => {
  const { api, calls } = fixture(undefined, undefined, true);
  render(<CiteRagApp api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: '工具与调用记录' }, { timeout: 8000 }));
  const drawer = await screen.findByRole('dialog');
  const call = within(drawer).getByRole<HTMLButtonElement>('button', { name: '调用工具：和风实时天气' });
  expect(call.disabled).toBe(true);
  fireEvent.change(within(drawer).getByLabelText('纬度'), { target: { value: 'NaN' } });
  fireEvent.change(within(drawer).getByLabelText('经度'), { target: { value: '0' } });
  expect(call.disabled).toBe(true);
  fireEvent.change(within(drawer).getByLabelText('纬度'), { target: { value: '0' } });
  expect(call.disabled).toBe(false);
  fireEvent.click(call);
  expect(await within(drawer).findByText('待确认')).toBeTruthy();
  const request = calls.find((item) => item.path.endsWith('/tools/calls') && item.method === 'POST');
  expect(JSON.parse(String(request?.body)).arguments).toEqual({ latitude: 0, longitude: 0 });
  expect(within(drawer).queryByText('合成少云')).toBeNull();
  fireEvent.click(within(drawer).getByRole('button', { name: '确认执行' }));
  fireEvent.click(await screen.findByRole('button', { name: '确定' }));
  expect(await within(drawer).findByText(/合成少云/)).toBeTruthy();
  expect(within(drawer).getByText(/69%/)).toBeTruthy();
  expect(within(drawer).getByText('https://developer.qweather.com/attribution.html')).toBeTruthy();
  expect(within(drawer).getByText(/查询时间/)).toBeTruthy();
  expect(calls.filter((item) => item.path.endsWith('/decision')).length).toBe(1);
}, 20000);

it('restores fixed chat, opens a verified source and restores focus without writes under StrictMode', async () => {
  const { api, calls } = fixture();
  render(<StrictMode><CiteRagApp api={api} /></StrictMode>);
  const source = await screen.findByRole('button', { name: /合成产品手册.md/ }, { timeout: 5000 }); source.focus(); fireEvent.click(source);
  const drawer = await screen.findByRole('dialog');
  expect(within(drawer).getByText(sampleMessages[sampleChats[0].id][0].citations[0].excerpt)).toBeTruthy();
  expect(within(drawer).getByRole('link', { name: '下载原文' }).getAttribute('href')).toContain('/original');
  fireEvent.click(within(drawer).getByRole('button', { name: '关闭' }));
  await waitFor(() => expect(document.activeElement).toBe(source));
  expect(calls.every((call) => call.method === 'GET')).toBe(true);
}, 20000);

it.each(['partial', 'failed', 'interrupted'] as const)('connects the %s retry control to the retry stream API', async (status) => {
  const { api, calls } = fixture([{ ...sampleMessages[sampleChats[0].id][0], status, citations: [], error_code: 'request_interrupted' }]);
  render(<CiteRagApp api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: '重试回答' }, { timeout: 8000 }));
  await screen.findByText('合成重试结果');
  const writes = calls.filter((call) => call.method !== 'GET');
  expect(writes.map((call) => call.path)).toEqual([`/api/conversations/${sampleChats[0].id}/messages/design-message-1/retry/stream`]);
  expect(JSON.parse(String(writes[0].body))).toEqual({ attempt_id: expect.any(String) });
  expect(JSON.parse(String(writes[0].body)).attempt_id).not.toBe('design-attempt-1');
}, 20000);

it('does not publish uncommitted text or sources', async () => {
  const { api } = fixture([{ ...sampleMessages[sampleChats[0].id][0], saved: false }]);
  render(<CiteRagApp api={api} />);
  await screen.findByText('正在生成并核验，正文提交后再展示。');
  expect(screen.queryByRole('button', { name: /合成产品手册.md/ })).toBeNull();
  expect(screen.queryByText(sampleMessages[sampleChats[0].id][0].text)).toBeNull();
}, 20000);

it('shows the same ordinary and precise knowledge route labels in text and voice', async () => {
  const original = sampleMessages[sampleChats[0].id][0];
  const messages = [
    { ...original, message_id: 'ordinary', question: '你好', text: '合成问候回答', route: 'general' as const, citations: [] },
    { ...original, message_id: 'precise', question: '查询编号', route: 'literal' as const },
  ];
  const { api } = fixture(messages);
  const mounted = render(<CiteRagApp api={api} />);
  expect(await screen.findByText('普通回答 · 未检索知识库')).toBeTruthy();
  expect(screen.getByText('知识库回答 · 精确检索')).toBeTruthy();
  mounted.unmount();
  const actions = assistantActions();
  actions.media.answers = messages;
  render(<Voice context={{ chatId: sampleChats[0].id, chatTitle: '合成聊天', kbName: '合成库',
    kbReady: true, chatPending: false }} actions={actions} />);
  expect(screen.getByText('普通回答 · 未检索知识库')).toBeTruthy();
  expect(screen.getByText('知识库回答 · 精确检索')).toBeTruthy();
});

it('does not label an unrouted failed image as a knowledge answer or voice input', async () => {
  const failed = { ...sampleMessages[sampleChats[0].id][0], message_id: 'failed-image',
    question: '这是什么?', text: '', status: 'failed' as const, route: null,
    error_code: 'answer_unavailable', citations: [], images: [{ id: 'pending-image',
      filename: 'synthetic.png', mime_type: 'image/png' as const, width: 32, height: 32,
      size: 512, observation: null, observation_status: 'pending' as const,
      needs_confirmation: false, confirmed_identifier: null,
      expires_at: '2026-10-08T00:00:00Z' }] };
  const { api } = fixture([failed]);
  const mounted = render(<CiteRagApp api={api} />);
  expect((await screen.findAllByText('回答方式未确定')).length).toBeGreaterThan(0);
  expect(screen.getByText('synthetic.png')).toBeTruthy();
  expect(screen.queryByText('知识库回答')).toBeNull();
  mounted.unmount();

  const actions = assistantActions();
  actions.media.answers = [failed];
  render(<Voice context={{ chatId: sampleChats[0].id, chatTitle: '普通聊天', kbName: '',
    kbReady: true, chatPending: false }} actions={actions} />);
  expect(screen.getAllByText('回答方式未确定').length).toBeGreaterThan(0);
  expect(screen.getByText('用户')).toBeTruthy();
  expect(screen.queryByText('用户（语音输入）')).toBeNull();
});

it('shows a knowledge evidence miss clearly without publishing a general answer or source', async () => {
  const missing = { ...sampleMessages[sampleChats[0].id][0], status: 'insufficient_evidence' as const,
    route: 'semantic' as const, text: '', citations: [] };
  const clarification = { ...missing, message_id: 'clarification', status: 'needs_clarification' as const,
    route: 'needs_clarification' as const, text: '请补充具体对象' };
  const { api } = fixture([missing, clarification]);
  render(<CiteRagApp api={api} />);
  expect(await screen.findByText('当前知识库没有足够的可核查证据，暂不作答。')).toBeTruthy();
  expect(screen.getByText('知识库回答 · 语义检索')).toBeTruthy();
  expect(screen.getByText('需要澄清')).toBeTruthy();
  expect(screen.getByText('请补充具体对象')).toBeTruthy();
  expect(screen.queryByRole('button', { name: /合成产品手册.md/ })).toBeNull();
});

it.each([null, 'KX-17'])('keeps image observation details collapsed without hiding the answer or confirmed identifier (%s)', async (identifier) => {
  const image = { id: 'image-1', filename: '合成花朵.png', mime_type: 'image/png' as const,
    width: 320, height: 240, size: 1024, observation: '合成观察：一株开花植物与一只蝴蝶。',
    observation_status: 'ready' as const, needs_confirmation: !!identifier, confirmed_identifier: identifier,
    expires_at: '2099-10-01T00:00:00Z' };
  const message = { ...sampleMessages[sampleChats[0].id][0], route: 'general' as const,
    text: '合成图片回答：这是一株开花植物。', citations: [], images: [image] };
  const { api } = fixture([message]);
  render(<CiteRagApp api={api} />);
  expect(await screen.findByText(message.text)).toBeTruthy();
  expect(screen.getByRole('img', { name: '提问图片：合成花朵.png' })).toBeTruthy();
  const summary = screen.getByText('查看图片识别详情');
  const details = summary.closest('details') as HTMLDetailsElement;
  expect(details).toBeTruthy();
  expect(details.open).toBe(false);
  expect(screen.getByText(image.observation).closest('details')).toBe(details);
  fireEvent.click(summary);
  expect(details.open).toBe(true);
  expect(within(details).getByText(image.observation)).toBeTruthy();
  fireEvent.click(summary);
  expect(details.open).toBe(false);
  expect(screen.queryByRole('button', { name: '确认图片编号' })).toBeNull();
  if (identifier) expect(screen.getByText(`已确认编号：${identifier}`)).toBeTruthy();
  expect(screen.getByText('普通回答 · 未检索知识库')).toBeTruthy();
  expect(screen.queryByRole('button', { name: /查看来源/ })).toBeNull();
}, 20000);

it('keeps image observation failure and retry visible without empty details', async () => {
  const image = { id: 'image-failed', filename: '合成失败.png', mime_type: 'image/png' as const,
    width: 320, height: 240, size: 1024, observation: null, observation_status: 'failed' as const,
    needs_confirmation: false, confirmed_identifier: null, expires_at: '2099-10-01T00:00:00Z' };
  const message = { ...sampleMessages[sampleChats[0].id][0], status: 'failed' as const,
    route: null, text: '', citations: [], error_code: 'image_observation_unavailable', images: [image] };
  const { api } = fixture([message]);
  render(<CiteRagApp api={api} />);
  expect(await screen.findByText('图片识别失败，具体原因见下方。排除原因后可重试回答。')).toBeTruthy();
  expect(screen.getByText(/错误代码：image_observation_unavailable/)).toBeTruthy();
  expect(screen.getByRole<HTMLButtonElement>('button', { name: '重试回答' }).disabled).toBe(false);
  expect(screen.queryByText('查看图片识别详情')).toBeNull();
});

it.each(['failed', 'interrupted', 'partial'] as const)('keeps an unrouted ordinary %s answer readable and connects its retry to the API', async (status) => {
  const ordinary = { ...sampleChats[0], kb_id: null, title: '合成普通聊天' };
  sessionStorage.setItem('citerag.workbench.selection', JSON.stringify({ kbId: null, chatId: ordinary.id }));
  const image = { id: 'ordinary-image-failed', filename: '合成普通失败.png', mime_type: 'image/png' as const,
    width: 32, height: 32, size: 512, observation: null, observation_status: 'failed' as const,
    needs_confirmation: false, confirmed_identifier: null, expires_at: '2099-10-01T00:00:00Z' };
  const error = { failed: 'image_observation_unavailable', interrupted: 'request_interrupted',
    partial: 'answer_output_limit' }[status];
  const message = { ...sampleMessages[sampleChats[0].id][0], status, route: null, kb_revision: 0,
    question: '合成普通聊天问题', text: status === 'partial' ? '合成未完成回答' : '', citations: [],
    error_code: error, saved: true, images: status === 'failed' ? [image] : [] };
  const { api, calls } = fixture([message], [ordinary]);
  render(<CiteRagApp api={api} />);
  expect(await screen.findByText(message.question)).toBeTruthy();
  expect(screen.getByText('普通聊天 · 无知识库访问')).toBeTruthy();
  expect(screen.queryByText('知识库已变化，此回答与来源已暂停展示。')).toBeNull();
  expect(screen.queryByText('当前知识库状态暂不可核实，此回答与来源已暂停展示。请刷新后重试。')).toBeNull();
  expect(screen.getByText(new RegExp(`错误代码：${error}`))).toBeTruthy();
  if (status === 'failed') expect(screen.getByText('图片识别未完成，请重试；若持续失败，请检查图片模型状态。')).toBeTruthy();
  if (status === 'partial') expect(screen.getByText(message.text)).toBeTruthy();
  expect(screen.queryByRole('button', { name: /查看来源/ })).toBeNull();
  const retry = screen.getByRole<HTMLButtonElement>('button', { name: '重试回答' });
  expect(retry.disabled).toBe(false);
  fireEvent.click(retry);
  expect(await screen.findByText('合成重试结果')).toBeTruthy();
  const retryCalls = calls.filter((call) => call.path.endsWith(`/messages/${message.message_id}/retry/stream`) && call.method === 'POST');
  expect(retryCalls).toHaveLength(1);
  expect(JSON.parse(String(retryCalls[0].body))).toEqual({ attempt_id: expect.any(String) });
  expect(JSON.parse(String(retryCalls[0].body)).attempt_id).not.toBe(message.attempt_id);
}, 20000);

it.each(['hidden', 'stale'] as const)('keeps explicit %s invalidation for an unrouted ordinary answer', async (flag) => {
  const ordinary = { ...sampleChats[0], kb_id: null, title: '合成普通聊天' };
  sessionStorage.setItem('citerag.workbench.selection', JSON.stringify({ kbId: null, chatId: ordinary.id }));
  const message = { ...sampleMessages[sampleChats[0].id][0], route: null, status: 'failed' as const,
    text: '合成已失效回答', citations: [], error_code: 'image_observation_unavailable', [flag]: true };
  const { api } = fixture([message], [ordinary]);
  render(<CiteRagApp api={api} />);
  expect(await screen.findByText(message.question)).toBeTruthy();
  expect(screen.getByText('普通聊天 · 无知识库访问')).toBeTruthy();
  expect(screen.getByText('知识库已变化，此回答与来源已暂停展示。')).toBeTruthy();
  expect(screen.queryByText(message.text)).toBeNull();
  expect(screen.queryByText(/错误代码：image_observation_unavailable/)).toBeNull();
  expect(screen.queryByRole('button', { name: '重试回答' })).toBeNull();
}, 20000);

it.each(['unavailable', 'maintaining'] as const)('keeps the %s knowledge gate for an unrouted failed knowledge answer', async (state) => {
  const message = { ...sampleMessages[sampleChats[0].id][0], route: null, status: 'failed' as const,
    text: '合成暂停回答', error_code: 'image_observation_unavailable' };
  const { api } = fixture([message]);
  render(<CiteRagApp api={{ ...api, knowledgeBases: async () => {
    if (state === 'unavailable') throw new Error('合成目录暂不可用');
    return [{ ...sampleBases[0], status: 'maintaining' }];
  } }} />);
  expect(await screen.findByText(message.question)).toBeTruthy();
  expect(screen.getByText(state === 'unavailable' ? '当前知识库状态暂不可核实，此回答与来源已暂停展示。请刷新后重试。'
    : '知识库已变化，此回答与来源已暂停展示。')).toBeTruthy();
  expect(screen.queryByText(message.text)).toBeNull();
  expect(screen.queryByRole('button', { name: '重试回答' })).toBeNull();
  expect(screen.queryByRole('button', { name: /查看来源/ })).toBeNull();
}, 20000);

it('keeps collapsed synthetic observation available for identifier confirmation without source citations', async () => {
  const image = { id: 'image-1', filename: '合成铭牌.png', mime_type: 'image/png' as const,
    width: 320, height: 240, size: 1024, observation: '编号字迹模糊',
    observation_status: 'ready' as const, needs_confirmation: true, confirmed_identifier: null,
    expires_at: '2026-10-01T00:00:00Z' };
  const message = { ...sampleMessages[sampleChats[0].id][0],
    status: 'needs_clarification' as const, route: 'needs_clarification' as const,
    text: '请确认图片编号', citations: [], images: [image] };
  const { api } = fixture([message]);
  render(<CiteRagApp api={api} />);
  expect(await screen.findByText('合成铭牌.png')).toBeTruthy();
  const summary = screen.getByText('核对图片识别详情');
  const details = summary.closest('details') as HTMLDetailsElement;
  expect(details).toBeTruthy();
  expect(details.open).toBe(false);
  expect(screen.getByText('图片编号需人工确认。')).toBeTruthy();
  fireEvent.click(summary);
  expect(details.open).toBe(true);
  expect(within(details).getByText('编号字迹模糊')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '确认图片编号' }));
  const confirmation = await screen.findByRole('dialog');
  expect(within(confirmation).getByText('请核对图片原文，输入确认的完整编号')).toBeTruthy();
  expect(screen.queryByRole('button', { name: '重试回答' })).toBeNull();
  expect(screen.queryByRole('button', { name: /合成产品手册.md/ })).toBeNull();
}, 20000);

it('uses backend offset pagination for documents and preserves disabled maintenance gates', async () => {
  const { api, calls } = fixture(); render(<CiteRagApp api={api} />);
  fireEvent.click(await screen.findByRole('menuitem', { name: /知识库/ }));
  fireEvent.click((await screen.findAllByRole('button', { name: '管理资料' },
    { timeout: 8000 }))[0]);
  await screen.findByText('合成产品手册.md');
  fireEvent.click(screen.getByTitle('2'));
  await screen.findByText('合成操作说明_17.md');
  expect(calls.some((call) => call.path.endsWith('scope=current&limit=10&offset=10'))).toBe(true);
}, 20000);

it('does not start standalone media during StrictMode and disconnects on unmount', async () => {
  const { api, calls } = fixture();
  const room = { connect: vi.fn(async () => {}), microphone: vi.fn(async () => {}), output: vi.fn(async () => {}), disconnect: vi.fn(async () => {}) };
  const factory = vi.fn(async () => room);
  const { unmount } = render(<StrictMode><StandaloneVoice api={api} factory={factory} /></StrictMode>);
  const connect = await screen.findByRole<HTMLButtonElement>('button', { name: '测试本地音频连接' });
  await waitFor(() => expect(connect.disabled).toBe(false));
  expect(factory).not.toHaveBeenCalled(); expect(calls.every((call) => call.method === 'GET')).toBe(true);
  fireEvent.click(connect); await screen.findByRole('heading', { name: '媒体已连接' });
  expect(factory).toHaveBeenCalledTimes(1); expect(room.microphone).toHaveBeenCalledWith(true);
  unmount(); await waitFor(() => expect(room.disconnect).toHaveBeenCalledTimes(1));
}, 20000);

it('loads tasks for the newly selected chat knowledge base rather than the previously opened library', async () => {
  const other = { ...sampleChats[0], id: 'design-other-chat', kb_id: sampleBases[1].id, title: '其他知识库聊天' };
  const { api, calls } = fixture(undefined, [...sampleChats, other]);
  render(<CiteRagApp api={api} />);
  fireEvent.click(await screen.findByRole('menuitem', { name: /知识库/ }));
  fireEvent.click((await screen.findAllByRole('button', { name: '管理资料' }))[0]);
  await screen.findByText('合成产品手册.md');
  fireEvent.click(screen.getByRole('menuitem', { name: /对话工作台/ }));
  fireEvent.click(await screen.findByRole('button', { name: '其他知识库聊天' }));
  fireEvent.click(screen.getByRole('menuitem', { name: /处理任务/ }));
  await waitFor(() => expect(calls.some((call) => call.path.startsWith(`/api/knowledge-bases/${other.kb_id}/documents?`))).toBe(true));
}, 20000);
