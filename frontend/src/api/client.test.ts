import { describe, expect, it } from 'vitest';
import { createApi } from './client';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const status = { status: 'partial', mode: 'local_single_user', database: 'available', rag: 'not_configured', models: 'not_configured' };

describe('local same-origin API boundary', () => {
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
