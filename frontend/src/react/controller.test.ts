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

const savedHistory = (n: number) => ({ message_id: `history-${n}`, attempt_id: `attempt-${n}`,
  client_message_id: `input-${n}`, question: `公开合成问题${n}`, text: `公开合成回答${n}`,
  mode: 'auto', route: 'general', status: 'answered', citations: [], kb_revision: 0,
  error_code: null, created_at: '2026-10-16T00:00:00Z', saved: true });

async function historyController(fetcher: typeof fetch, prompt?: () => Promise<string | null>, knowledge = false) {
  const ordinary = { ...syntheticChat, kb_id: knowledge ? syntheticBase.id : null };
  sessionStorage.setItem('citerag.workbench.selection', JSON.stringify({ kbId: ordinary.kb_id, chatId: ordinary.id }));
  let latest!: AppView;
  let dispose = () => {};
  const api = createApi(async (url, init) => {
    const path = String(url);
    if (path === '/api/agent/capability') return json({ enabled: false });
    if (path === '/api/knowledge-bases') return json({ items: knowledge ? [syntheticBase] : [] });
    if (path.startsWith('/api/conversations?')) return json({ items: [ordinary, { ...ordinary, id: 'second-chat' }] });
    return fetcher(url, init);
  });
  await mountApp(document.createElement('div'), api, undefined, 'workbench', {
    render: (view) => { latest = view; }, onDispose: (fn) => { dispose = fn; }, prompt,
  });
  return { get view() { return latest; }, dispose };
}

it('discards cached earlier pages when the server invalidates knowledge history without new attempts', async () => {
  let invalid = false;
  const old = Array.from({ length: 50 }, (_, i) => ({ ...savedHistory(i + 1), kb_revision: 1 }));
  const recent = Array.from({ length: 50 }, (_, i) => ({ ...savedHistory(i + 51), kb_revision: 1 }));
  const app = await historyController(async (url) => {
    const earlier = String(url).includes('before_message_id=');
    return json({ items: (earlier ? old : recent).map((m) => invalid ? { ...m, stale: true, hidden: true, text: '' } : m),
      has_more: !earlier, next_before_message_id: earlier ? null : 'history-51',
      history_version: (invalid ? 'b' : 'a').repeat(64) });
  }, undefined, true);
  try {
    await app.view.actions.loadMoreMessages!();
    expect(app.view.state.chatMessages).toHaveLength(100);
    invalid = true;
    await app.view.actions.refreshAgentMessages!(syntheticChat.id);
    expect(app.view.state.chatMessages).toHaveLength(50);
    expect(app.view.state.chatMessages.every((m) => m.hidden && m.stale && m.text === '')).toBe(true);
    expect(app.view.messageHistory?.hasMore).toBe(true);
  } finally { app.dispose(); }
});

it('reloads the latest page instead of combining history from before and after image expiry', async () => {
  let expired = false;
  let latestReads = 0;
  const app = await historyController(async (url) => {
    const earlier = String(url).includes('before_message_id=');
    if (earlier) expired = true; else latestReads++;
    return json({ items: [{ ...savedHistory(earlier ? 1 : 51), hidden: expired, text: expired ? '' : '公开合成旧上下文' }],
      has_more: !earlier, next_before_message_id: earlier ? null : 'history-51',
      history_version: (expired ? 'c' : 'a').repeat(64) });
  });
  try {
    await app.view.actions.loadMoreMessages!();
    expect(latestReads).toBe(2);
    expect(app.view.state.chatMessages.map((m) => m.message_id)).toEqual(['history-51']);
    expect(app.view.state.chatMessages[0]).toMatchObject({ hidden: true, text: '' });
    expect(app.view.messageHistory?.hasMore).toBe(true);
  } finally { app.dispose(); }
});

it('ignores a delayed old-version latest response after an older page triggered invalidation', async () => {
  let reads = 0;
  let release!: (response: Response) => void;
  const page = (version: string, hidden: boolean) => ({ items: [{ ...savedHistory(51), hidden,
    text: hidden ? '' : '公开合成旧正文' }], has_more: true, next_before_message_id: 'history-51', history_version: version.repeat(64) });
  const app = await historyController(async (url) => {
    if (String(url).includes('before_message_id=')) return json({ ...page('b', true), items: [], has_more: false, next_before_message_id: null });
    if (++reads === 2) return new Promise<Response>((resolve) => { release = resolve; });
    return json(page(reads === 1 ? 'a' : 'b', reads > 1));
  });
  try {
    const delayed = app.view.actions.refreshAgentMessages!(syntheticChat.id);
    await vi.waitFor(() => expect(release).toBeTypeOf('function'));
    await app.view.actions.loadMoreMessages!();
    release(json(page('a', false)));
    await delayed;
    expect(app.view.state.chatMessages[0]).toMatchObject({ hidden: true, text: '' });
  } finally { app.dispose(); }
});

it('keeps an SSE saved result when an invalidation refetch returns an older running snapshot', async () => {
  let releaseOlder!: (response: Response) => void;
  let releaseLatest!: (response: Response) => void;
  let source!: ReadableStreamDefaultController<Uint8Array>;
  let reads = 0;
  let record = savedHistory(101);
  const recent = savedHistory(51);
  const app = await historyController(async (url, init) => {
    if (String(url).endsWith('/messages/stream')) {
      record = { ...record, client_message_id: JSON.parse(String(init?.body)).client_message_id, status: 'running', saved: false, text: '' };
      return new Response(new ReadableStream<Uint8Array>({ start(controller) {
        source = controller;
        controller.enqueue(new TextEncoder().encode(`event: accepted\ndata: ${JSON.stringify(record)}\n\n`));
      } }), { headers: { 'Content-Type': 'text/event-stream' } });
    }
    if (String(url).includes('before_message_id=')) return new Promise<Response>((resolve) => { releaseOlder = resolve; });
    if (++reads === 2) return new Promise<Response>((resolve) => { releaseLatest = resolve; });
    return json({ items: reads === 1 ? [recent] : [recent, record], has_more: true,
      next_before_message_id: 'history-51', history_version: (reads === 1 ? 'a' : 'b').repeat(64) });
  });
  try {
    const older = app.view.actions.loadMoreMessages!();
    await vi.waitFor(() => expect(releaseOlder).toBeTypeOf('function'));
    app.view.actions.setDraft('公开合成请求');
    const sending = app.view.actions.sendChat();
    await vi.waitFor(() => expect(app.view.state.chatStreamAttemptId).toBe(record.attempt_id));
    releaseOlder(json({ items: [savedHistory(1)], has_more: false, next_before_message_id: null, history_version: 'b'.repeat(64) }));
    await vi.waitFor(() => expect(releaseLatest).toBeTypeOf('function'));
    const running = { ...record };
    record = { ...record, text: '公开合成已保存回答', status: 'answered', saved: true };
    source.enqueue(new TextEncoder().encode(`event: saved\ndata: ${JSON.stringify(record)}\n\n`));
    source.close();
    await sending;
    releaseLatest(json({ items: [recent, running], has_more: true, next_before_message_id: 'history-51', history_version: 'b'.repeat(64) }));
    await older;
    expect(app.view.state.chatMessages.find((m) => m.message_id === record.message_id)).toMatchObject({ status: 'answered', text: record.text });
    expect(app.view.state.chatMessages.some((m) => m.status === 'running')).toBe(false);
  } finally { app.dispose(); }
});

it('loads older message pages once and keeps them when the latest answer updates', async () => {
  let olderReads = 0;
  const old = Array.from({ length: 50 }, (_, i) => savedHistory(i + 1));
  const recent = Array.from({ length: 50 }, (_, i) => savedHistory(i + 51));
  const app = await historyController(async (url) => {
    if (String(url).includes('before_message_id=')) {
      olderReads++;
      return json({ items: old, has_more: false, next_before_message_id: null });
    }
    return json({ items: recent, has_more: true, next_before_message_id: 'history-51' });
  });
  try {
    expect(app.view.messageHistory?.hasMore).toBe(true);
    await Promise.all([app.view.actions.loadMoreMessages!(), app.view.actions.loadMoreMessages!()]);
    expect(olderReads).toBe(1);
    expect(app.view.state.chatMessages).toHaveLength(100);
    expect(app.view.state.chatMessages[0].message_id).toBe('history-1');
    await app.view.actions.refreshAgentMessages!(syntheticChat.id);
    expect(app.view.state.chatMessages).toHaveLength(100);
    expect(app.view.messageHistory?.hasMore).toBe(false);
  } finally { app.dispose(); }
});

it('retains the older-page cursor on failure and ignores a late page after switching chats', async () => {
  let fail = true;
  let release!: (value: Response) => void;
  const app = await historyController(async (url) => {
    const path = String(url);
    if (path.includes('before_message_id=')) {
      if (fail) throw new TypeError('synthetic older page unavailable');
      return new Promise<Response>((resolve) => { release = resolve; });
    }
    return json({ items: [savedHistory(path.includes('second-chat') ? 200 : 51)],
      has_more: !path.includes('second-chat'), next_before_message_id: path.includes('second-chat') ? null : 'history-51' });
  });
  try {
    await app.view.actions.loadMoreMessages!();
    expect(app.view.messageHistory?.error).toBeInstanceOf(ApiError);
    expect(app.view.state.chatMessages[0].message_id).toBe('history-51');
    fail = false;
    const pending = app.view.actions.loadMoreMessages!();
    await vi.waitFor(() => expect(release).toBeTypeOf('function'));
    await app.view.actions.selectChat('second-chat');
    release(json({ items: [savedHistory(1)], has_more: false, next_before_message_id: null }));
    await pending;
    expect(app.view.state.chatMessages.map((m) => m.message_id)).toEqual(['history-200']);
    expect(app.view.messageHistory?.error).toBeNull();
  } finally { app.dispose(); }
});

it('keeps boundary message 51 when a newer latest page arrives during an older-page read', async () => {
  const old = Array.from({ length: 50 }, (_, i) => savedHistory(i + 1));
  const recent = Array.from({ length: 50 }, (_, i) => savedHistory(i + 51));
  let latestReads = 0;
  let releaseOlder: ((response: Response) => void) | undefined;
  let pending: Promise<void> | undefined;
  const app = await historyController(async (url) => {
    if (String(url).includes('before_message_id='))
      return new Promise<Response>((resolve) => { releaseOlder = resolve; });
    const items = latestReads++ === 0 ? recent : [...recent.slice(1), savedHistory(101)];
    return json({ items, has_more: true, next_before_message_id: items[0].message_id });
  });
  try {
    pending = app.view.actions.loadMoreMessages!();
    await vi.waitFor(() => expect(releaseOlder).toBeTypeOf('function'));
    await app.view.actions.refreshAgentMessages!(syntheticChat.id);
    releaseOlder!(json({ items: old, has_more: false, next_before_message_id: null }));
    await pending;
    expect(app.view.state.chatMessages.map((item) => item.message_id)).toEqual(
      Array.from({ length: 101 }, (_, i) => `history-${i + 1}`));
    expect(app.view.messageHistory?.error).toBeNull();
    expect(app.view.messageHistory?.hasMore).toBe(false);
  } finally {
    releaseOlder?.(json({ items: old, has_more: false, next_before_message_id: null }));
    await pending;
    app.dispose();
  }
});

it('does not confirm an old retry as stopped before its own attempt was accepted or persisted', async () => {
  vi.useFakeTimers();
  const previous = { ...savedHistory(1), status: 'interrupted', text: '' };
  const old = [previous, ...Array.from({ length: 49 }, (_, i) => savedHistory(i + 2))];
  const recent = Array.from({ length: 50 }, (_, i) => savedHistory(i + 51));
  let source: ReadableStreamDefaultController<Uint8Array> | undefined;
  let retrying: Promise<void> | undefined;
  let requestedAttempt: string | undefined;
  const app = await historyController(async (url, init) => {
    const path = String(url);
    if (path.endsWith('/retry/stream')) {
      requestedAttempt = JSON.parse(String(init?.body)).attempt_id;
      return new Response(new ReadableStream<Uint8Array>({ start(controller) { source = controller; } }),
        { headers: { 'Content-Type': 'text/event-stream' } });
    }
    if (path.includes('before_message_id=history-2'))
      return json({ items: [previous], has_more: false, next_before_message_id: null });
    if (path.includes('before_message_id='))
      return json({ items: old, has_more: false, next_before_message_id: null });
    return json({ items: recent, has_more: true, next_before_message_id: 'history-51' });
  });
  try {
    await app.view.actions.loadMoreMessages!();
    retrying = app.view.actions.retryChat(previous.message_id);
    await vi.waitFor(() => expect(source).toBeDefined());
    expect(requestedAttempt).not.toBe(previous.attempt_id);
    app.view.actions.stopChat!();
    await retrying;
    expect(app.view.state.chatMessages.find((item) => item.message_id === previous.message_id))
      .toMatchObject({ attempt_id: previous.attempt_id, status: 'interrupted' });
    expect(app.view.textAnswer?.notice).toMatch(/尚未确认|未确认|核对失败/);
    expect(app.view.textAnswer?.notice).not.toMatch(/已核对保存状态|已完成并保存/);
    expect(app.view.state.chatMessages).toHaveLength(100);
  } finally {
    app.dispose();
    try { source?.close(); } catch { /* The requested stop may already have closed the stream. */ }
    await retrying;
    await vi.runOnlyPendingTimersAsync();
    vi.useRealTimers();
  }
});

it('keeps the committed answer when stop races with saved-frame reader cleanup', async () => {
  let record: ReturnType<typeof savedHistory> | null = null;
  let releaseCleanup!: () => void;
  const cleanup = new Promise<void>((resolve) => { releaseCleanup = resolve; });
  let cleanupStarted = false;
  let sending: Promise<void> | undefined;
  const app = await historyController(async (url, init) => {
    if (String(url).endsWith('/messages/stream')) {
      const body = JSON.parse(String(init?.body));
      const accepted = { ...savedHistory(1), client_message_id: body.client_message_id,
        status: 'running', text: '', saved: false };
      record = { ...accepted, status: 'answered', text: '公开合成已持久回答', saved: true };
      return new Response(new ReadableStream<Uint8Array>({ start(controller) {
        controller.enqueue(new TextEncoder().encode(
          `event: accepted\ndata: ${JSON.stringify(accepted)}\n\nevent: saved\ndata: ${JSON.stringify(record)}\n\n`));
      }, cancel() { cleanupStarted = true; return cleanup; } }),
      { headers: { 'Content-Type': 'text/event-stream' } });
    }
    return json({ items: record ? [record] : [], has_more: false, next_before_message_id: null });
  });
  try {
    app.view.actions.setDraft('公开合成提交与停止竞争');
    sending = app.view.actions.sendChat();
    await vi.waitFor(() => expect(cleanupStarted).toBe(true));
    app.view.actions.stopChat!();
    releaseCleanup();
    await sending;
    expect(app.view.state.chatMessages).toHaveLength(1);
    expect(app.view.state.chatMessages[0]).toMatchObject({ status: 'answered', saved: true,
      text: '公开合成已持久回答' });
    expect(app.view.state.chatPending).toBe(false);
    expect(app.view.textAnswer?.canStop).toBe(false);
    expect(app.view.textAnswer?.notice).toContain('已完成');
  } finally {
    releaseCleanup();
    app.dispose();
    await sending;
  }
});

it.each(['interrupted', 'failed'])('updates an old retry through a before-window poll to %s without appending unloaded history', async (settledStatus) => {
  vi.useFakeTimers();
  const original = { ...savedHistory(51), status: 'partial', text: '公开合成旧片段' };
  const recent = [original, ...Array.from({ length: 49 }, (_, i) => savedHistory(i + 52))];
  const older = Array.from({ length: 50 }, (_, i) => savedHistory(i + 1));
  let record = original;
  let recoveryReads = 0;
  let source: ReadableStreamDefaultController<Uint8Array> | undefined;
  let retrying: Promise<void> | undefined;
  const app = await historyController(async (url, init) => {
    const path = String(url);
    if (path.endsWith('/retry/stream')) {
      record = { ...original, attempt_id: JSON.parse(String(init?.body)).attempt_id,
        status: 'running', text: '', saved: false };
      return new Response(new ReadableStream<Uint8Array>({ start(controller) {
        source = controller;
        controller.enqueue(new TextEncoder().encode(`event: accepted\ndata: ${JSON.stringify(record)}\n\n`));
      } }), { headers: { 'Content-Type': 'text/event-stream' } });
    }
    if (path.includes('before_message_id=history-52')) {
      if (++recoveryReads > 1) record = { ...record, status: settledStatus, saved: true };
      return json({ items: [...older.slice(1), record], has_more: true, next_before_message_id: 'history-2' });
    }
    if (path.includes('before_message_id=history-51'))
      return json({ items: older, has_more: false, next_before_message_id: null });
    return json({ items: recent, has_more: true, next_before_message_id: 'history-51' });
  });
  try {
    retrying = app.view.actions.retryChat(original.message_id);
    await vi.waitFor(() => expect(app.view.state.chatStreamAttemptId).toBe(record.attempt_id));
    app.view.actions.stopChat!();
    await retrying;
    expect(recoveryReads).toBe(1);
    await vi.advanceTimersByTimeAsync(1000);
    expect(recoveryReads).toBe(2);
    expect(app.view.state.chatMessages.map((item) => item.message_id)).toEqual(
      recent.map((item) => item.message_id));
    expect(app.view.state.chatMessages[0]).toMatchObject({ status: settledStatus, saved: true });
    expect(app.view.textAnswer?.notice).toContain(settledStatus === 'interrupted' ? '已停止' : '已结束');
    await app.view.actions.loadMoreMessages!();
    expect(app.view.messageHistory?.error).toBeNull();
    expect(app.view.state.chatMessages.map((item) => item.message_id)).toEqual(
      [...older, ...recent].map((item) => item.message_id));
  } finally {
    app.dispose();
    try { source?.close(); } catch { /* Stop cancellation closes a pending reader. */ }
    await retrying;
    await vi.runOnlyPendingTimersAsync();
    vi.useRealTimers();
  }
});

it('keeps loaded pages and retries an old image message after confirming its identifier', async () => {
  const image = { id: 'synthetic-old-image', filename: 'public-synthetic.png', mime_type: 'image/png',
    width: 10, height: 10, size: 100, observation: '公开合成编号待确认', observation_status: 'ready',
    needs_confirmation: true, confirmed_identifier: null as string | null, expires_at: '2026-11-16T00:00:00Z' };
  let record = { ...savedHistory(1), status: 'needs_clarification', images: [image] };
  const old = Array.from({ length: 50 }, (_, i) => savedHistory(i + 1));
  const recent = Array.from({ length: 50 }, (_, i) => savedHistory(i + 51));
  let confirmations = 0;
  let retries = 0;
  const app = await historyController(async (url, init) => {
    const path = String(url);
    if (path.endsWith('/confirm')) {
      confirmations++;
      expect(JSON.parse(String(init?.body))).toEqual({ identifier: 'PUBLIC-123' });
      record = { ...record, images: [{ ...image, confirmed_identifier: 'PUBLIC-123' }] };
      return json(record.images[0]);
    }
    if (path.endsWith('/retry/stream')) {
      retries++;
      record = { ...record, attempt_id: JSON.parse(String(init?.body)).attempt_id, status: 'answered' };
      return new Response(new ReadableStream<Uint8Array>({ start(controller) {
        controller.enqueue(new TextEncoder().encode(`event: saved\ndata: ${JSON.stringify(record)}\n\n`));
        controller.close();
      } }), { headers: { 'Content-Type': 'text/event-stream' } });
    }
    if (path.includes('before_message_id=history-2'))
      return json({ items: [record], has_more: false, next_before_message_id: null });
    if (path.includes('before_message_id='))
      return json({ items: [record, ...old.slice(1)], has_more: false, next_before_message_id: null });
    return json({ items: recent, has_more: true, next_before_message_id: 'history-51' });
  }, async () => 'PUBLIC-123');
  try {
    await app.view.actions.loadMoreMessages!();
    await app.view.actions.confirmChatImage(record.message_id, image.id);
    expect(confirmations).toBe(1);
    expect(retries).toBe(1);
    expect(app.view.state.chatMessages).toHaveLength(100);
    expect(app.view.state.chatMessages[0]).toMatchObject({ message_id: 'history-1', status: 'answered',
      images: [{ confirmed_identifier: 'PUBLIC-123' }] });
    expect(app.view.messageHistory?.hasMore).toBe(false);
  } finally { app.dispose(); }
});

it.each(['interrupted', 'partial', 'answered'])('stops an actual text stream and trusts only the persisted %s result', async (status) => {
  let record: ReturnType<typeof savedHistory> | null = null;
  let cancelled = 0;
  let requestSignal: AbortSignal | null | undefined;
  const app = await historyController(async (url, init) => {
    if (String(url).endsWith('/messages/stream')) {
      const body = JSON.parse(String(init?.body));
      requestSignal = init?.signal;
      record = { ...savedHistory(1), client_message_id: body.client_message_id, status: 'running', text: '', saved: false };
      const stream = new ReadableStream<Uint8Array>({ start(controller) {
        controller.enqueue(new TextEncoder().encode(`event: accepted\ndata: ${JSON.stringify(record)}\n\n`));
      }, cancel() {
        cancelled++;
        record = { ...record!, status, text: status === 'partial' ? '已保存片段' : status === 'answered' ? '已提交回答' : '', saved: true };
      } });
      return new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } });
    }
    return json({ items: record ? [record] : [], has_more: false, next_before_message_id: null });
  });
  try {
    app.view.actions.setDraft('公开合成停止问题');
    const sending = app.view.actions.sendChat();
    await vi.waitFor(() => expect(app.view.state.chatStreamAttemptId).toBe('attempt-1'));
    expect(app.view.textAnswer?.canStop).toBe(true);
    app.view.actions.stopChat!(); app.view.actions.stopChat!();
    await sending;
    expect(requestSignal?.aborted).toBe(true);
    expect(cancelled).toBe(1);
    expect(app.view.state.chatPending).toBe(false);
    expect(app.view.state.chatError).toBeNull();
    expect(app.view.state.chatMessages[0]).toMatchObject({ status, saved: true });
    expect(app.view.textAnswer?.canStop).toBe(false);
    expect(app.view.textAnswer?.notice).toContain(status === 'answered' ? '已完成' : '已停止');
  } finally { app.dispose(); }
});

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
