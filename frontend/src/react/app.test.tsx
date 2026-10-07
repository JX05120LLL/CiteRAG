import { StrictMode } from 'react';
import { webcrypto } from 'node:crypto';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { createApi } from '../api/client';
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
    if (path.endsWith('/retry')) return json({ ...messages[0], attempt_id: 'retried', status: 'answered', text: '合成重试结果', citations: [], saved: true });
    if (path.includes('/conversations/') && method === 'PATCH') return json({ ...sampleChats[0], title: '改后的合成名称' });
    if (path.endsWith(`/conversations/${sampleChats[0].id}`)) return json(sampleChats[0]);
    return json({ items: chats });
  };
  return { api: createApi(fetcher), calls, recordCount: () => toolRecords.length };
}

it('groups chats by knowledge base and archives and restores through the API', async () => {
  const { api, calls } = fixture();
  render(<CiteRagApp api={api} />);
  const rowMenu = await screen.findByRole('button', { name: `${sampleChats[0].title}的更多操作` });
  expect(screen.getByLabelText('按知识库分组的聊天').textContent).toContain(sampleBases[0].name);
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
  fireEvent.click(await screen.findByRole('button', { name: '工具与调用记录' }));
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

it.each(['partial', 'failed', 'interrupted'] as const)('connects the %s retry control to the existing retry API', async (status) => {
  const { api, calls } = fixture([{ ...sampleMessages[sampleChats[0].id][0], status, citations: [], error_code: 'request_interrupted' }]);
  render(<CiteRagApp api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: '重试回答' }));
  await screen.findByText('合成重试结果');
  expect(calls.filter((call) => call.method !== 'GET').map((call) => call.path)).toEqual([`/api/conversations/${sampleChats[0].id}/messages/design-message-1/retry`]);
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

it('keeps a synthetic image observation separate from verified source citations', async () => {
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
  expect(screen.getByText('编号字迹模糊')).toBeTruthy();
  expect(screen.getByRole('button', { name: '确认图片编号' })).toBeTruthy();
  expect(screen.queryByRole('button', { name: '重试回答' })).toBeNull();
  expect(screen.queryByRole('button', { name: /合成产品手册.md/ })).toBeNull();
});

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
