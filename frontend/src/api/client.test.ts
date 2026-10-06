import { describe, expect, it, vi } from 'vitest';
import { createApi } from './client';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const status = { status: 'partial', mode: 'local_single_user', database: 'available', rag: 'not_configured', models: 'not_configured' };

describe('local same-origin API boundary', () => {
  it('waits for a tool decision beyond the ordinary ten-second request timeout', async () => {
    vi.useFakeTimers();
    try {
      const api = createApi((_input, init) => new Promise((resolve, reject) => {
        init?.signal?.addEventListener('abort', () => reject(new Error('aborted')));
        setTimeout(() => resolve(json({ id: 'synthetic-call', conversation_id: 'chat',
          request_id: 'synthetic-request', kb_id: null, error_code: 'weather_timeout',
          tool_id: 'weather.current', arguments: { latitude: 0, longitude: 0 },
          status: 'failed', result: null, impact: 'Synthetic weather timeout',
          created_at: '2026-10-01T00:00:00Z', source_type: 'tool' })), 12000);
      }));
      const finished = api.decideTool('chat', 'synthetic-call', true).catch((error: unknown) => error);
      await vi.advanceTimersByTimeAsync(12000);
      expect(await finished).toMatchObject({ status: 'failed', error_code: 'weather_timeout', result: null });
      expect(vi.getTimerCount()).toBe(0);
    } finally { vi.useRealTimers(); }
  });

  it('sends explicit tool arguments and preserves the zero-argument default', async () => {
    const bodies: Record<string, unknown>[] = [];
    const api = createApi(async (_input, init) => {
      const body = JSON.parse(String(init?.body)); bodies.push(body);
      return json({ id: 'synthetic-call', conversation_id: 'chat', request_id: body.request_id,
        kb_id: null, error_code: null,
        tool_id: body.tool_id, arguments: body.arguments, status: 'pending_approval', result: null,
        impact: 'Synthetic tool approval', created_at: '2026-10-01T00:00:00Z', source_type: 'tool' });
    });
    await api.invokeTool('chat', 'weather.current', 'synthetic-request', { latitude: 0, longitude: 0 });
    expect(bodies[0].arguments).toEqual({ latitude: 0, longitude: 0 });
    await api.invokeTool('chat', 'local.time', 'synthetic-request-2');
    expect(bodies[1].arguments).toEqual({});
  });

  it('uploads private image bytes, validates metadata, and confirms uncertain IDs through same-origin APIs', async () => {
    const image = { id: 'image-1', filename: 'synthetic.png', mime_type: 'image/png',
      width: 16, height: 12, size: 81, observation: null, observation_status: 'pending',
      needs_confirmation: false, confirmed_identifier: null, expires_at: '2026-10-01T00:00:00Z' };
    const calls: Array<[string, RequestInit | undefined]> = [];
    const api = createApi(async (input, init) => {
      calls.push([String(input), init]);
      if (init?.method === 'DELETE') return new Response(null, { status: 204 });
      return json(image, init?.body instanceof FormData ? 201 : 200);
    });
    const file = new File(['synthetic bytes'], 'synthetic.png', { type: 'image/png' });
    expect(await api.uploadImage('chat/1', file)).toEqual(image);
    expect(calls[0][0]).toBe('/api/conversations/chat%2F1/attachments');
    expect(calls[0][1]?.body).toBeInstanceOf(FormData);
    expect(new Headers(calls[0][1]?.headers).has('Content-Type')).toBe(false);
    expect(await api.confirmImage('chat/1', 'image-1', 'AB-42')).toEqual(image);
    expect(JSON.parse(String(calls[1][1]?.body))).toEqual({ identifier: 'AB-42' });
    await expect(api.deletePendingImage('chat/1', 'image-1')).resolves.toBeUndefined();
    expect(api.imageUrl('chat/1', 'image-1')).toBe('/api/conversations/chat%2F1/attachments/image-1');
    expect(calls.every(([, init]) => init?.credentials === 'omit')).toBe(true);
  });

  it('rejects malformed image metadata rather than claiming the upload succeeded', async () => {
    const api = createApi(async () => json({ id: 'image', filename: 'bad.png', mime_type: 'image/png',
      width: 0, height: 0, size: 10, observation: null, observation_status: 'ready',
      needs_confirmation: false, confirmed_identifier: null, expires_at: '2026-10-01T00:00:00Z' }, 201));
    await expect(api.uploadImage('chat', new File(['x'], 'bad.png', { type: 'image/png' })))
      .rejects.toMatchObject({ kind: 'invalid-response' });
  });
  it('uses an in-memory control header for session actions, never URL credentials', async () => {
    const calls: Array<[string, RequestInit | undefined]> = [];
    const api = createApi(async (url, init) => { calls.push([String(url), init]); return json({ status: 'ended' }); });
    await api.voiceEnd('session', 'synthetic-control');
    expect(calls[0][0]).toBe('/api/voice/sessions/session/end');
    expect(calls[0][1]?.headers).toMatchObject({ 'X-CiteRAG-Voice-Control': 'synthetic-control' });
    expect(calls[0][1]?.credentials).toBe('omit');
    expect(localStorage.length).toBe(0);
  });
  it('accepts speech timing and caption events from the voice SSE stream', async () => {
    const events = [
      { type: 'phase', phase: 'generating', seq: 0, session_id: 'session', generation: 1 },
      { type: 'timing', metric: 'first_text', elapsed_ms: 329, seq: 1, session_id: 'session', generation: 1 },
      { type: 'speech_text', text: '合成回答。', seq: 2, session_id: 'session', generation: 1 },
    ];
    const payload = events.map((event) => `event: voice\ndata: ${JSON.stringify(event)}\n\n`).join('');
    const api = createApi(async () => new Response(new ReadableStream({
      start(controller) { controller.enqueue(new TextEncoder().encode(payload)); controller.close(); },
    }), { headers: { 'Content-Type': 'text/event-stream' } }));
    const received: string[] = [];
    await api.voiceEvents('session', 'synthetic-control', new AbortController().signal,
      (event) => received.push(event.type));
    expect(received).toEqual(['phase', 'timing', 'speech_text']);
  });
  it('reads media configuration and requests a chat-bound token without storing credentials', async () => {
    const calls: Array<[string, RequestInit | undefined]> = [];
    const capability = { transport: 'configured', assistant: 'not_configured', purpose: 'media_test' };
    const connection = { server_url: 'ws://127.0.0.1:17880', token: 'synthetic-token', room: 'synthetic-room',
      conversation_id: 'chat/1', assistant: 'not_configured', purpose: 'media_test' };
    const api = createApi(async (url, init) => {
      calls.push([String(url), init]); return json(String(url).endsWith('/status') ? capability : connection);
    });
    expect(await api.voiceStatus()).toEqual(capability);
    expect(await api.voiceToken('chat/1')).toEqual(connection);
    expect(calls.map(([url, init]) => [url, init?.method, init?.credentials])).toEqual([
      ['/api/voice/status', 'GET', 'omit'], ['/api/conversations/chat%2F1/voice/token', 'POST', 'omit'],
    ]);
  });

  it.each(['wss://remote.example', 'ws://localhost.evil:7880', 'ws://user:secret@localhost:7880',
    'ws://127.0.0.1:7880/path', 'ws://127.0.0.1:7880?token=secret'])('rejects unsafe media server %s', async (server_url) => {
    const api = createApi(async () => json({ server_url, token: 'synthetic-token', room: 'synthetic-room',
      conversation_id: 'chat', assistant: 'not_configured', purpose: 'media_test' }));
    await expect(api.voiceToken('chat')).rejects.toMatchObject({ kind: 'invalid-response' });
  });

  it('rejects mismatched chat tokens and false assistant readiness', async () => {
    const connection = { server_url: 'ws://127.0.0.1:7880', token: 'synthetic-token', room: 'synthetic-room',
      conversation_id: 'other', assistant: 'not_configured', purpose: 'media_test' };
    await expect(createApi(async () => json(connection)).voiceToken('chat')).rejects.toMatchObject({ kind: 'invalid-response' });
    await expect(createApi(async () => json({ transport: 'configured', assistant: 'available', purpose: 'media_test' })).voiceStatus())
      .rejects.toMatchObject({ kind: 'invalid-response' });
  });

  it('creates and renames knowledge bases with JSON bodies and omitted credentials', async () => {
    const calls: Array<[string, RequestInit | undefined]> = [];
    const base = { id: 'kb-1', name: '本地资料', status: 'empty' };
    const api = createApi(async (input, init) => {
      calls.push([String(input), init]);
      return json(base, init?.method === 'POST' ? 201 : 200);
    });
    expect(await api.createKnowledgeBase('本地资料', 'request-id')).toEqual(base);
    expect(await api.renameKnowledgeBase('kb/1', '改名')).toEqual(base);
    expect(calls.map(([path, init]) => [path, init?.method, JSON.parse(String(init?.body))])).toEqual([
      ['/api/knowledge-bases', 'POST', { name: '本地资料', client_request_id: 'request-id' }],
      ['/api/knowledge-bases/kb%2F1', 'PATCH', { name: '改名' }],
    ]);
    for (const [, init] of calls) {
      expect(init?.credentials).toBe('omit');
      expect(new Headers(init?.headers).get('Content-Type')).toBe('application/json');
    }
  });

  it.each([
    [409, 'capacity_exceeded', 'conflict', '最多可创建 5 个知识库'],
    [409, 'idempotency_conflict', 'conflict', '请求与先前的名称不一致'],
    [404, 'kb_not_found', 'not-found', '知识库已不存在或不属于当前本地安装'],
    [422, '', 'validation', '名称须为 1–120 个字符'],
  ])('maps mutation failure %s/%s to safe typed feedback', async (status, code, kind, message) => {
    const api = createApi(async () => json({ detail: { code, message: '<script>private</script>' } }, Number(status)));
    await expect(api.createKnowledgeBase('资料', 'request-id')).rejects.toMatchObject({ kind, code });
    await expect(api.createKnowledgeBase('资料', 'request-id')).rejects.toThrow(String(message));
  });

  it('explains that a pending or running tool blocks a streamed question', async () => {
    const api = createApi(async () => new Response(
      'event: error\ndata: {"status":409,"code":"tool_in_progress"}\n\n',
      { headers: { 'Content-Type': 'text/event-stream' } },
    ));
    await expect(api.askMessageStream('chat', '合成问题', 'request-id', 'auto'))
      .rejects.toThrow('当前聊天的工具调用尚未结束。请在“工具与调用记录”中处理待确认调用，或等待运行中的调用完成，再继续提问；也可新建聊天。');
  });

  it('rejects malformed mutation results instead of claiming success', async () => {
    const api = createApi(async () => json({ id: 'kb', name: '资料', status: 'unknown' }, 201));
    await expect(api.createKnowledgeBase('资料', 'request-id')).rejects.toMatchObject({ kind: 'invalid-response' });
  });

  it('rejects an array status in list, create, and rename responses', async () => {
    const invalid = { id: 'kb', name: '资料', status: ['empty'] };
    const api = createApi(async (_input, init) => json(init?.method === 'GET' ? { items: [invalid] } : invalid));
    await expect(api.knowledgeBases()).rejects.toMatchObject({ kind: 'invalid-response' });
    await expect(api.createKnowledgeBase('资料', 'request-id')).rejects.toMatchObject({ kind: 'invalid-response' });
    await expect(api.renameKnowledgeBase('kb', '资料')).rejects.toMatchObject({ kind: 'invalid-response' });
  });

  it('reads local collections without sending session credentials', async () => {
    const api = createApi(async (input, init) => {
      expect(input).toBe('/api/knowledge-bases');
      expect(init?.method).toBe('GET');
      expect(init?.credentials).toBe('omit');
      expect(new Headers(init?.headers).has('X-CSRF-Token')).toBe(false);
      return json({ items: [] });
    });
    expect(await api.knowledgeBases()).toEqual([]);
  });

  it('requires a validated correction generation before accepting its acknowledgement', async () => {
    const call = (value: unknown) => createApi(async () => json(value)).voiceCorrection('session', 'control', 1, 2, 'synthetic correction');
    expect(await call({ status: 'accepted', generation: 2 })).toEqual({ generation: 2 });
    for (const value of [{ status: 'accepted' }, { status: 'accepted', generation: -1 }, { status: 'stopped', generation: 2 }]) {
      await expect(call(value)).rejects.toMatchObject({ kind: 'invalid-response' });
    }
  });

  it.each([[401, 'http'], [403, 'forbidden'], [503, 'unavailable'], [500, 'http']])('distinguishes HTTP %s without leaking server details', async (httpStatus, kind) => {
    const api = createApi(async () => json({ detail: { code: 'test_error', message: 'private infrastructure details' } }, httpStatus));
    await expect(api.knowledgeBases()).rejects.toMatchObject({ kind, code: 'test_error' });
    await expect(api.knowledgeBases()).rejects.not.toHaveProperty('message', 'private infrastructure details');
  });

  it.each([
    ['database_not_configured', '业务数据库尚未配置'],
    ['database_unavailable', '业务数据库暂不可用'],
  ])('gives database recovery instructions for %s', async (code, message) => {
    const api = createApi(async () => json({ detail: { code } }, 503));
    await expect(api.knowledgeBases()).rejects.toThrow(message);
  });

  it('distinguishes an unreachable network from an HTTP failure', async () => {
    const api = createApi(async () => { throw new TypeError('connection refused'); });
    await expect(api.knowledgeBases()).rejects.toMatchObject({ kind: 'network' });
  });

  it('rejects invalid knowledge states and incomplete conversation results', async () => {
    await expect(createApi(async () => json({ items: [{ id: 'kb', name: 'notes', status: 'unknown' }] })).knowledgeBases()).rejects.toMatchObject({ kind: 'invalid-response' });
    await expect(createApi(async () => json({ items: [{ id: 'chat', title: 'notes' }] })).conversations()).rejects.toMatchObject({ kind: 'invalid-response' });
  });

  it('returns empty database lists without sample records', async () => {
    const api = createApi(async () => json({ items: [] }));
    expect(await api.knowledgeBases()).toEqual([]);
    expect(await api.conversations()).toEqual([]);
  });

  it('retrieves status through the local endpoint without requiring a database', async () => {
    const api = createApi(async (input) => {
      expect(input).toBe('/api/status');
      return json({ ...status, database: 'not_configured' });
    });
    expect(await api.health()).toEqual({ ...status, database: 'not_configured' });
  });

  it.each([
    { status: 'ready' },
    { ...status, mode: 'multi_user' },
    { ...status, database: 'maybe' },
    { ...status, rag: 'maybe' },
  ])('rejects unsupported status instead of inventing readiness: %j', async (value) => {
    await expect(createApi(async () => json(value)).health()).rejects.toMatchObject({ kind: 'invalid-response' });
  });

  it.each(['not_configured', 'unverified', 'available', 'unavailable'])('accepts both capability states: %s', async (capability) => {
    const payload = { ...status, rag: capability, models: capability };
    expect(await createApi(async () => json(payload)).health()).toEqual(payload);
  });

  it('accepts allow-listed verification metadata and rejects malformed fields', async () => {
    const payload = {
      ...status, models: 'available', rag: 'unverified',
      models_info: { region: 'cn-beijing', model_names: ['qwen-flash'], last_verified_at: '2026-09-23T00:00:00+00:00' },
      rag_info: { lightrag_commit: 'a'.repeat(40), postgresql_major: 17, vector_version: '0.8.1' },
    };
    expect(await createApi(async () => json(payload)).health()).toEqual(payload);
    for (const bad of [
      { ...payload, models_info: { ...payload.models_info, model_names: ['<img>'] } },
      { ...payload, rag_info: { ...payload.rag_info, postgresql_major: '17' } },
      { ...payload, models_info: { ...payload.models_info, last_verified_at: 'yesterday' } },
      { ...payload, rag_info: { ...payload.rag_info, password: 'must-not-render' } },
    ]) {
      await expect(createApi(async () => json(bad)).health()).rejects.toMatchObject({ kind: 'invalid-response' });
    }
  });
});
