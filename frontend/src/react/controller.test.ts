import { afterEach, expect, it, vi } from 'vitest';
import { mountApp, type AppView } from '../app';
import { ApiError, createApi } from '../api/client';
import { createDocumentsPanel } from '../pages/documents';
import { sampleBases } from '../preview/samples';

const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } });
afterEach(() => sessionStorage.clear());

const syntheticChat = { id: 'synthetic-chat', owner_id: 'local', kb_id: 'synthetic-kb',
  title: '公开合成聊天', created_at: '2026-10-11T00:00:00Z' };
const syntheticBase = { id: 'synthetic-kb', name: '公开合成知识库', status: 'ready' as const };

it('keeps the selected saved chat when the knowledge collection cannot be read', async () => {
  sessionStorage.setItem('citerag.workbench.selection', JSON.stringify({ kbId: syntheticChat.kb_id, chatId: syntheticChat.id }));
  let latest!: AppView;
  let dispose = () => {};
  let unavailable = true;
  const reads: string[] = [];
  const api = createApi(async (url) => {
    const path = String(url); reads.push(path);
    if (path === '/api/knowledge-bases') {
      if (unavailable) throw new TypeError('synthetic connection failure');
      return json({ items: [syntheticBase] });
    }
    if (path.endsWith('/messages')) return json({ items: [] });
    return json({ items: [syntheticChat] });
  });
  await mountApp(document.createElement('div'), api, undefined, 'workbench',
    { render: (view) => { latest = view; }, onDispose: (fn) => { dispose = fn; } });
  expect(latest.state.basesError?.kind).toBe('network');
  expect(latest.state.selectedChatId).toBe(syntheticChat.id);
  expect(reads).toContain(`/api/conversations/${syntheticChat.id}/messages`);
  expect(JSON.parse(sessionStorage.getItem('citerag.workbench.selection')!)).toEqual({ kbId: syntheticChat.kb_id, chatId: syntheticChat.id });
  unavailable = false;
  await latest.actions.refresh();
  expect(latest.state.basesError).toBeNull();
  expect(latest.state.selectedChatId).toBe(syntheticChat.id);
  dispose();
});

it('clears a missing knowledge binding only after a successful collection read', async () => {
  sessionStorage.setItem('citerag.workbench.selection', JSON.stringify({ kbId: syntheticChat.kb_id, chatId: syntheticChat.id }));
  let latest!: AppView;
  let dispose = () => {};
  await mountApp(document.createElement('div'), createApi(async (url) =>
    json({ items: String(url) === '/api/knowledge-bases' ? [] : [syntheticChat] })), undefined, 'workbench',
    { render: (view) => { latest = view; }, onDispose: (fn) => { dispose = fn; } });
  expect(latest.state.selectedChatId).toBeNull();
  expect(latest.state.selectedKbId).toBeNull();
  dispose();
});

it('does not create a chat while the persistence collection is unavailable', async () => {
  let latest!: AppView;
  let dispose = () => {};
  const writes = vi.fn();
  const api = createApi(async (url, init) => {
    if (init?.method === 'POST') { writes(); return json({ ...syntheticChat, kb_id: null }); }
    if (String(url).startsWith('/api/conversations?'))
      return new Response(JSON.stringify({ detail: { code: 'database_not_configured' } }), { status: 503 });
    return json({ items: [] });
  });
  await mountApp(document.createElement('div'), api, undefined, 'workbench',
    { render: (view) => { latest = view; }, onDispose: (fn) => { dispose = fn; } });
  expect(latest.state.chatsError?.code).toBe('database_not_configured');
  await latest.actions.createChat(null);
  expect(writes).not.toHaveBeenCalled();
  dispose();
});

it('clears a history read error when a refresh restores the same saved chat', async () => {
  sessionStorage.setItem('citerag.workbench.selection', JSON.stringify({ kbId: syntheticChat.kb_id, chatId: syntheticChat.id }));
  let unavailable = true;
  let latest!: AppView;
  let dispose = () => {};
  const api = createApi(async (url) => {
    const path = String(url);
    if (path.endsWith('/messages')) {
      if (unavailable) throw new TypeError('synthetic history failure');
      return json({ items: [] });
    }
    return json({ items: path === '/api/knowledge-bases' ? [syntheticBase] : [syntheticChat] });
  });
  await mountApp(document.createElement('div'), api, undefined, 'workbench',
    { render: (view) => { latest = view; }, onDispose: (fn) => { dispose = fn; } });
  expect(latest.state.chatError).toBeInstanceOf(ApiError);
  unavailable = false;
  await latest.actions.refresh();
  expect(latest.state.chatError).toBeNull();
  dispose();
});

it('keeps an unaccepted question and its request key while answering is disabled', async () => {
  const ordinary = { ...syntheticChat, kb_id: null };
  sessionStorage.setItem('citerag.workbench.selection', JSON.stringify({ kbId: null, chatId: ordinary.id }));
  let latest!: AppView;
  let dispose = () => {};
  const requests: Array<Record<string, unknown>> = [];
  const api = createApi(async (url, init) => {
    const path = String(url);
    if (path.endsWith('/messages/stream')) {
      requests.push(JSON.parse(String(init?.body)));
      return new Response(JSON.stringify({ detail: { code: 'answer_disabled' } }), { status: 503 });
    }
    if (path.endsWith('/messages')) return json({ items: [] });
    return json({ items: path === '/api/knowledge-bases' ? [] : [ordinary] });
  });
  await mountApp(document.createElement('div'), api, undefined, 'workbench',
    { render: (view) => { latest = view; }, onDispose: (fn) => { dispose = fn; } });
  latest.actions.setDraft('公开合成普通问题');
  await latest.actions.sendChat();
  expect(latest.state.chatDraft).toBe('公开合成普通问题');
  expect(latest.state.chatError?.code).toBe('answer_disabled');
  expect(latest.state.chatPending).toBe(false);
  expect(latest.state.chatMessages).toEqual([]);
  await latest.actions.sendChat();
  expect(requests).toHaveLength(2);
  expect(requests[1].client_message_id).toBe(requests[0].client_message_id);
  expect(requests[1]).toMatchObject({ mode: 'auto', text: '公开合成普通问题' });
  expect(latest.state.selectedKbId).toBeNull();
  dispose();
});

it('does not submit a second question when a lost stream already committed the input', async () => {
  const ordinary = { ...syntheticChat, kb_id: null };
  sessionStorage.setItem('citerag.workbench.selection', JSON.stringify({ kbId: null, chatId: ordinary.id }));
  let latest!: AppView;
  let dispose = () => {};
  let saved: Record<string, unknown> | null = null;
  let submissions = 0;
  const api = createApi(async (url, init) => {
    const path = String(url);
    if (path.endsWith('/messages/stream')) {
      submissions++;
      const body = JSON.parse(String(init?.body));
      saved = { message_id: 'synthetic-message', attempt_id: 'synthetic-attempt', client_message_id: body.client_message_id,
        question: body.text, text: '公开合成已保存回答', mode: 'auto', route: 'general', status: 'answered',
        kb_revision: 0, citations: [], error_code: null, created_at: '2026-10-11T00:00:00Z', saved: true };
      throw new TypeError('synthetic lost response');
    }
    if (path.endsWith('/messages')) return json({ items: saved ? [saved] : [] });
    return json({ items: path === '/api/knowledge-bases' ? [] : [ordinary] });
  });
  await mountApp(document.createElement('div'), api, undefined, 'workbench',
    { render: (view) => { latest = view; }, onDispose: (fn) => { dispose = fn; } });
  latest.actions.setDraft('公开合成普通问题');
  await latest.actions.sendChat();
  expect(latest.state.chatMessages).toHaveLength(1);
  expect(latest.state.chatMessages[0]).toMatchObject({ saved: true, text: '公开合成已保存回答', route: 'general', citations: [] });
  expect(latest.state.chatDraft).toBe('');
  expect(latest.state.chatRequestKey).toBeNull();
  await latest.actions.sendChat();
  expect(submissions).toBe(1);
  dispose();
});

it('publishes one business state to React, does not mount native UI or start media', async () => {
  const root = document.createElement('div');
  const views: AppView[] = [];
  const calls: string[] = [];
  const methods: string[] = [];
  let dispose = () => {};
  await mountApp(root, createApi(async (url, init) => {
    methods.push(init?.method ?? 'GET');
    calls.push(String(url)); return json({ items: [] });
  }), undefined, 'workbench', { render: (view) => views.push(view), onDispose: (fn) => { dispose = fn; } });
  expect(root.childNodes).toHaveLength(0);
  expect(views.at(-1)?.state.loading).toBe(false);
  views.at(-1)!.actions.setDraft('待发送');
  expect(views.at(-1)?.state.chatDraft).toBe('待发送');
  expect([...calls].sort()).toEqual(['/api/agent/capability', '/api/conversations?limit=20&offset=0', '/api/knowledge-bases']);
  expect(methods).toEqual(['GET', 'GET', 'GET']);
  dispose();
});

it('registers disposal before initial requests resolve and ignores their late result', async () => {
  let resolve!: (response: Response) => void;
  const late = new Promise<Response>((done) => { resolve = done; });
  const render = vi.fn();
  let dispose = () => {};
  const mounted = mountApp(document.createElement('div'), createApi(() => late), undefined, 'workbench',
    { render, onDispose: (fn) => { dispose = fn; } });
  dispose(); const count = render.mock.calls.length;
  expect(count).toBeGreaterThan(0);
  resolve(json({ items: [] })); await mounted;
  expect(render).toHaveBeenCalledTimes(count);
});

it('closing a detail does not invalidate a pending collection refresh', async () => {
  let delayed = false;
  let release!: (response: Response) => void;
  const api = createApi(async (input) => {
    const path = String(input);
    if (path === '/api/knowledge-bases') return delayed ? await new Promise<Response>((resolve) => { release = resolve; }) : json({ items: sampleBases });
    if (path.includes('/jobs?')) return json({ items: [], total: 0, active_items: [], failed_count: 0 });
    return json({ items: [], total: 0, counts: { ready: 0, failed: 0, deleted: 0 } });
  });
  const panel = createDocumentsPanel(api, () => {}, () => {});
  await panel.open(sampleBases[0]); delayed = true;
  const refreshed = panel.refresh(); expect(panel.snapshot.loading).toBe(true);
  panel.closeInspector(); release(json({ items: sampleBases })); await refreshed;
  expect(panel.snapshot.loading).toBe(false);
  expect(panel.snapshot.unavailable).toBe(false);
});
