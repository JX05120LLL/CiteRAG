import { StrictMode } from 'react';
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
function fixture(messages = sampleMessages[sampleChats[0].id], chats = sampleChats) {
  const calls: { path: string; method: string; body: unknown }[] = [];
  const fetcher: typeof fetch = async (input, init) => {
    const path = String(input); const method = init?.method ?? 'GET'; calls.push({ path, method, body: init?.body });
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
  return { api: createApi(fetcher), calls };
}

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

it('uses backend offset pagination for documents and preserves disabled maintenance gates', async () => {
  const { api, calls } = fixture(); render(<CiteRagApp api={api} />);
  fireEvent.click(await screen.findByRole('menuitem', { name: /知识库/ }));
  fireEvent.click((await screen.findAllByRole('button', { name: '管理资料' }))[0]);
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
  fireEvent.click(screen.getByRole('button', { name: '打开聊天记录' }));
  fireEvent.click(await screen.findByText('其他知识库聊天'));
  await waitFor(() => expect(screen.queryByRole('dialog', { name: '我的聊天' })).toBeNull());
  fireEvent.click(screen.getByRole('menuitem', { name: /处理任务/ }));
  await waitFor(() => expect(calls.some((call) => call.path.startsWith(`/api/knowledge-bases/${other.kb_id}/documents?`))).toBe(true));
}, 20000);
