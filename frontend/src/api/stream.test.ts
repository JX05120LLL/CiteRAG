import { describe, expect, it } from 'vitest';
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

describe('checked answer stream', () => {
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
