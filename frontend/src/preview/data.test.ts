import { describe, expect, it, vi } from 'vitest';
import { createReadPreviewApi } from './data';
import { displayCitations, parsePreviewLocation } from './model';
import type { ChatMessage } from '../api/client';

const citation = { evidence_id: 'E1', document_id: 'sample-document', filename: '合成设计说明.md',
  locator: { paragraph: 2 }, excerpt: '仅为设计样例。' };
const answered: ChatMessage = { message_id: 'sample-message', attempt_id: 'sample-attempt',
  client_message_id: 'sample-input', question: '设计样例问题', mode: 'auto', route: 'semantic',
  status: 'answered', text: '设计样例回答', citations: [citation], kb_revision: 1,
  error_code: null, created_at: '2026-09-27T09:21:00Z', saved: true };

describe('candidate preview data boundaries', () => {
  it('defaults to real read-only data and accepts only known designs and routes', () => {
    expect(parsePreviewLocation('?design=b&page=tasks&data=sample&phase=failed&source=closed'))
      .toEqual({ design: 'b', page: 'tasks', data: 'sample', phase: 'failed', source: false });
    expect(parsePreviewLocation('?design=unknown&page=../api&data=success&phase=online').data).toBe('live');
    expect(parsePreviewLocation('?design=c').design).toBe('b');
    expect(parsePreviewLocation('?design=a').design).toBe('b');
  });

  it.each(['running', 'failed', 'interrupted', 'partial', 'insufficient_evidence'] as const)(
    'does not publish sources for %s output even if a stale source is present', (status) => {
      expect(displayCitations({ ...answered, status })).toEqual([]);
    });
  it.each([{ saved: false }, { hidden: true }, { stale: true }, { route: 'general' as const },
    { route: 'chat' as const }])('does not show sources outside saved knowledge answers: %j', (change) => {
    expect(displayCitations({ ...answered, ...change })).toEqual([]);
  });
  it('preserves genuine saved knowledge citations', () => {
    expect(displayCitations(answered)).toEqual([citation]);
  });

  it('exposes only read operations and requests backend pagination rather than all documents', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({
      items: [], total: 27, counts: { ready: 27 },
    }), { status: 200 }));
    const preview = createReadPreviewApi(fetcher);
    expect(Object.keys(preview.api)).not.toContain('voiceToken');
    expect(Object.keys(preview.api)).not.toContain('askMessageStream');
    expect(Object.keys(preview.api)).not.toContain('deleteDocument');
    const result = await preview.api.documentPage('library-design', 'deleted', 20);
    expect(result.total).toBe(27);
    expect(fetcher.mock.calls[0][0]).toBe('/api/knowledge-bases/library-design/documents?scope=deleted&limit=10&offset=20');
    expect(fetcher.mock.calls[0][1]?.method).toBe('GET');
    preview.dispose();
  });

  it('aborts in-flight reads on unmount and refuses further requests', async () => {
    let requestSignal: AbortSignal | null = null;
    const fetcher: typeof fetch = async (_input, init) => {
      requestSignal = init?.signal as AbortSignal;
      return await new Promise<Response>((_resolve, reject) => {
        requestSignal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
      });
    };
    const preview = createReadPreviewApi(fetcher);
    const read = preview.api.knowledgeBases().catch(() => undefined);
    preview.dispose();
    expect((requestSignal as AbortSignal | null)?.aborted).toBe(true);
    await read;
    await expect(preview.api.health()).rejects.toThrow();
  });

  it('keeps the abort signal attached while the response body is still being read', async () => {
    let signal: AbortSignal | undefined;
    let finishBody: (value: unknown) => void = () => {};
    let startBody = () => {};
    const bodyStarted = new Promise<void>((resolve) => { startBody = resolve; });
    const preview = createReadPreviewApi(async (_input, init) => {
      signal = init?.signal as AbortSignal;
      return { ok: true, status: 200, json: () => new Promise((resolve) => { finishBody = resolve; startBody(); }) } as Response;
    });
    const read = preview.api.knowledgeBases();
    await bodyStarted;
    preview.dispose();
    expect(signal?.aborted).toBe(true);
    finishBody({ items: [] }); await read;
  });
});
