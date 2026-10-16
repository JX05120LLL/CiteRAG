import { describe, expect, it, vi } from 'vitest';
import { createApi } from './client';

const final = {
  message_id: 'm', attempt_id: 'a', client_message_id: 'key', question: '问题',
  mode: 'semantic', status: 'answered', text: '温度上限是 42 C。', citations: [],
  kb_revision: 1, error_code: null, created_at: '2026-09-24T00:00:00Z', saved: true,
};

function wire(chunks: string[]): Response {
  const bytes = new TextEncoder().encode(chunks.join(''));
  return new Response(new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(bytes.subarray(0, 19));
      controller.enqueue(bytes.subarray(19, 57));
      controller.enqueue(bytes.subarray(57));
      controller.close();
    },
  }), { headers: { 'Content-Type': 'text/event-stream' } });
}

const event = (name: string, data: unknown): string => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`;

it.each(['not-a-version', null, 123])('rejects malformed history visibility versions: %s', async (history_version) => {
  const api = createApi(async () => new Response(JSON.stringify({ items: [final], has_more: false,
    next_before_message_id: null, history_version }), { headers: { 'Content-Type': 'application/json' } }));
  await expect(api.conversationMessagePage('chat')).rejects.toMatchObject({ kind: 'invalid-response' });
});

it('reads an older history page and keeps legacy item-only responses compatible', async () => {
  const paths: string[] = [];
  const api = createApi(async (url) => {
    paths.push(String(url));
    return new Response(JSON.stringify({ items: [final], has_more: true, next_before_message_id: 'm' }),
      { headers: { 'Content-Type': 'application/json' } });
  });
  expect(await api.conversationMessagePage('chat', 'older-message')).toEqual({
    items: [final], has_more: true, next_before_message_id: 'm',
  });
  expect(paths).toEqual(['/api/conversations/chat/messages?before_message_id=older-message']);
  const legacy = createApi(async () => new Response(JSON.stringify({ items: [final] })));
  expect(await legacy.conversationMessagePage('chat')).toEqual({ items: [final], has_more: false, next_before_message_id: null });
});

it('rejects history pagination metadata that could loop or skip the oldest message', async () => {
  for (const value of [{ items: [final], has_more: true, next_before_message_id: 'other' },
    { items: [], has_more: true, next_before_message_id: 'm' },
    { items: [final], has_more: false, next_before_message_id: 'm' }]) {
    const api = createApi(async () => new Response(JSON.stringify(value)));
    await expect(api.conversationMessagePage('chat')).rejects.toMatchObject({ kind: 'invalid-response' });
  }
});

it('aborts the actual pending answer reader and rejects late progress', async () => {
  const stop = new AbortController();
  const cancel = vi.fn();
  let source!: ReadableStreamDefaultController<Uint8Array>;
  let ready!: () => void;
  const accepted = new Promise<void>((resolve) => { ready = resolve; });
  const stream = new ReadableStream<Uint8Array>({ start(controller) {
    source = controller;
    controller.enqueue(new TextEncoder().encode(event('accepted', { ...final, status: 'running', text: '', saved: false })));
  }, cancel });
  const api = createApi(async () => new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } }));
  const progress = vi.fn(() => ready());
  const result = api.askMessageStream('chat', '问题', 'key', 'semantic', undefined, progress, [], stop.signal);
  const settled = result.catch((error: unknown) => error);
  try {
    await accepted;
    stop.abort();
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(cancel).toHaveBeenCalledTimes(1);
    expect(await settled).toMatchObject({ kind: 'cancelled' });
    expect(progress).toHaveBeenCalledTimes(1);
    expect(stream.locked).toBe(false);
  } finally { try { source.close(); } catch { /* The successful cancellation already closed it. */ } }
});

it('does not submit an already cancelled answer and streams retries using the attempt identity', async () => {
  const fetcher = vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) => wire([event('accepted', { ...final, attempt_id: 'retry', status: 'running', text: '', saved: false }),
    event('saved', { ...final, attempt_id: 'retry' })]));
  const api = createApi(fetcher);
  const stop = new AbortController(); stop.abort();
  await expect(api.askMessageStream('chat', '问题', 'key', 'semantic', undefined, undefined, [], stop.signal))
    .rejects.toMatchObject({ kind: 'cancelled' });
  expect(fetcher).not.toHaveBeenCalled();
  await expect(api.retryAnswerStream('chat', 'm', 'retry')).resolves.toMatchObject({ attempt_id: 'retry', client_message_id: 'key' });
  expect(fetcher.mock.calls[0][0]).toBe('/api/conversations/chat/messages/m/retry/stream');
});

describe('checked answer stream', () => {
  it('releases a still-open response body after receiving a committed final answer', async () => {
    const cancel = vi.fn();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(event('accepted', { ...final, status: 'running', text: '', saved: false }) + event('saved', final)));
      }, cancel,
    });
    const api = createApi(async () => new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } }));
    await expect(api.askMessageStream('chat', '问题', 'key')).resolves.toEqual(final);
    expect(cancel).toHaveBeenCalledTimes(1);
    expect(stream.locked).toBe(false);
  });

  it('releases the response body when an old attempt sends a late delta', async () => {
    const cancel = vi.fn();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(event('accepted', { ...final, status: 'running', text: '', saved: false }) +
          event('delta', { attempt_id: 'old-attempt', seq: 1, text: '迟到文本', saved: false })));
      }, cancel,
    });
    const api = createApi(async () => new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } }));
    await expect(api.askMessageStream('chat', '问题', 'key')).rejects.toMatchObject({ kind: 'invalid-response' });
    expect(cancel).toHaveBeenCalledTimes(1);
    expect(stream.locked).toBe(false);
  });
  it('accepts an automatic-route answer without manual exact filters', async () => {
    const automatic = { ...final, mode: 'auto' };
    const api = createApi(async (_url, init) => {
      const body = JSON.parse(String(init?.body));
      expect(body.mode).toBe('auto');
      expect(body).not.toHaveProperty('exact');
      return wire([
        event('accepted', { ...automatic, status: 'running', text: '', saved: false }),
        event('saved', automatic),
      ]);
    });
    expect(await api.askMessageStream('chat', '问题', 'key', 'auto')).toEqual(automatic);
  });

  it('handles arbitrary UTF-8 boundaries and only accepts a saved final message', async () => {
    const seen: string[] = [];
    const api = createApi(async (_url, init) => {
      expect(init?.credentials).toBe('omit');
      return wire([
        event('accepted', { ...final, status: 'running', text: '', saved: false }),
        event('delta', { attempt_id: 'a', seq: 1, text: '温度上限是 ', saved: true }),
        event('delta', { attempt_id: 'a', seq: 2, text: '42 C。', saved: true }),
        event('saved', final),
      ]);
    });
    const result = await api.askMessageStream('chat', '问题', 'key', 'semantic', undefined,
      (progress) => { seen.push(`${progress.type}:${progress.type === 'delta' ? progress.text : ''}`); });
    expect(result).toEqual(final);
    expect(seen).toEqual(['accepted:', 'delta:温度上限是 ', 'delta:42 C。']);
  });

  it('rejects a dropped stream or a mismatched final answer', async () => {
    const dropped = createApi(async () => wire([event('accepted', {
      ...final, status: 'running', text: '', saved: false,
    })]));
    await expect(dropped.askMessageStream('chat', '问题', 'key')).rejects.toMatchObject({ kind: 'network' });
    const forged = createApi(async () => wire([
      event('delta', { attempt_id: 'a', seq: 1, text: '旧内容', saved: true }),
      event('saved', final),
    ]));
    await expect(forged.askMessageStream('chat', '问题', 'key')).rejects.toMatchObject({ kind: 'invalid-response' });
  });

  it('marks provisional evidence text unsaved and accepts a durable partial result', async () => {
    const seen: string[] = [];
    const partial = { ...final, status: 'partial', text: '温度上限是 ', error_code: 'answer_unavailable' };
    const api = createApi(async () => wire([
      event('accepted', { ...final, status: 'running', text: '', saved: false }),
      event('delta', { attempt_id: 'a', seq: 1, text: '温度上限是 ', saved: false }),
      event('saved', partial),
    ]));
    expect(await api.askMessageStream('chat', '问题', 'key', 'semantic', undefined,
      (progress) => { if (progress.type === 'delta') seen.push(`${progress.saved}:${progress.text}`); })).toEqual(partial);
    expect(seen).toEqual(['false:温度上限是 ']);
  });
});
