import { afterEach, describe, expect, it, vi } from 'vitest';
import { mountApp } from './app';
import { createApi } from './api/client';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const failure = (status: number, code: string) => json({ detail: { code, message: 'private infrastructure details' } }, status);
const button = (root: HTMLElement, text: string) => Array.from(root.querySelectorAll('button')).find((element) => element.textContent?.includes(text));
const status = { status: 'partial', mode: 'local_single_user', database: 'not_configured', rag: 'not_configured', models: 'not_configured' };

async function page(fetcher: typeof fetch) {
  const root = document.createElement('div');
  document.body.append(root);
  await mountApp(root, createApi(fetcher));
  return root;
}

afterEach(() => { document.body.replaceChildren(); sessionStorage.clear(); });

describe('M0 local workbench', () => {
  it.each(['general', 'chat'] as const)('labels %s answers without suggesting knowledge verification', async (route) => {
    const root = await page(async (url) => {
      const path = String(url);
      if (path.includes('knowledge-bases')) return json({ items: [{ id: 'kb', name: '合成库', status: 'ready' }] });
      if (path.endsWith('/messages')) return json({ items: [{
        message_id: 'm', attempt_id: 'a', client_message_id: 'c', question: '合成交流',
        text: '合成通用回答', mode: 'auto', route, status: 'answered', kb_revision: 1,
        error_code: null, created_at: '2026-09-24T00:00:00Z', saved: true, citations: [],
      }] });
      return json({ items: [{ id: 'chat', owner_id: 'local', kb_id: 'kb', title: '合成聊天', created_at: '2026-09-24T00:00:00Z' }] });
    });
    button(root, '合成聊天')?.click();
    await vi.waitFor(() => expect(root.textContent).toContain('合成通用回答'));
    expect(root.textContent).toContain('普通回答 · 未检索知识库');
    expect(root.querySelector('.citation-trigger')).toBeNull();
    expect(root.querySelector('.message-status')?.textContent).not.toContain('核验');
  });
  it('opens the real status view when returning from the standalone voice page', async () => {
    const root = document.createElement('div'); document.body.append(root);
    const calls: string[] = [];
    await mountApp(root, createApi(async (url) => {
      calls.push(String(url)); return json(url === '/api/status' ? status : { items: [] });
    }), undefined, 'status');
    expect(root.querySelector('h1')?.textContent).toBe('系统状态');
    expect(calls).toContain('/api/status');
  });
  it('connects from the voice page and disconnects media when returning to the same chat', async () => {
    const root = document.createElement('div'); document.body.append(root);
    const room = { connect: vi.fn(async () => {}), microphone: vi.fn(async () => {}),
      output: vi.fn(async () => {}), disconnect: vi.fn(async () => {}) };
    let tokenRequests = 0;
    await mountApp(root, createApi(async (url) => {
      const path = String(url);
      if (path === '/api/voice/status') return json({ transport: 'configured', assistant: 'not_configured', purpose: 'media_test' });
      if (path.endsWith('/voice/token')) { tokenRequests++; return json({ server_url: 'ws://127.0.0.1:7880', token: 'synthetic',
        room: 'synthetic', conversation_id: 'chat', assistant: 'not_configured', purpose: 'media_test' }); }
      if (path.includes('knowledge-bases')) return json({ items: [{ id: 'kb', name: '合成库', status: 'ready' }] });
      if (path.endsWith('/messages')) return json({ items: [] });
      return json({ items: [{ id: 'chat', owner_id: 'local', kb_id: 'kb', title: '合成媒体测试聊天', created_at: '2026-09-24T00:00:00Z' }] });
    }), async () => room);
    button(root, '合成媒体测试聊天')?.click();
    await vi.waitFor(() => expect(root.textContent).toContain('固定知识库：合成库'));
    button(root, '语音通话')?.click();
    await vi.waitFor(() => expect(button(root, '测试本地音频连接')?.disabled).toBe(false));
    expect(tokenRequests).toBe(0);
    button(root, '测试本地音频连接')?.click();
    await vi.waitFor(() => expect(root.textContent).toContain('媒体已连接'));
    expect(room.microphone).toHaveBeenCalledWith(true);
    expect(button(root, '字幕')?.disabled).toBe(true);
    const microphone = button(root, '静音')!;
    microphone.focus(); microphone.click();
    await vi.waitFor(() => expect(button(root, '取消静音')).toBeDefined());
    expect(document.activeElement).toBe(microphone);
    button(root, '挂断并返回聊天')?.click();
    await vi.waitFor(() => expect(room.disconnect).toHaveBeenCalled());
    await vi.waitFor(() => expect(root.querySelector('textarea')).not.toBeNull());
    expect(root.textContent).toContain('合成媒体测试聊天');
  });

  it('reads voice configuration without requesting microphone or issuing a room token', async () => {
    const calls: string[] = [];
    const root = await page(async (url) => {
      calls.push(String(url));
      if (String(url) === '/api/voice/status') return json({ transport: 'disabled', assistant: 'not_configured', purpose: 'media_test' });
      return json({ items: String(url).includes('knowledge-bases')
        ? [{ id: 'kb', name: '合成库', status: 'ready' }] : [] });
    });
    button(root, '语音通话')?.click();
    expect(root.querySelector('h1')?.textContent).toBe('语音通话');
    expect(root.textContent).toContain('语音助手尚未接入');
    expect(button(root, '测试本地音频连接')?.disabled).toBe(true);
    expect(button(root, '静音')?.disabled).toBe(true);
    expect(root.querySelector('.voice-transcript')).toBeNull();
    expect(calls.filter((path) => /voice|room|audio/.test(path))).toEqual(['/api/voice/status']);
    button(root, '返回聊天')?.click();
    await vi.waitFor(() => expect(root.querySelector('textarea')).not.toBeNull());
    expect(button(root, '添加图片')?.disabled).toBe(true);
    expect(root.textContent).toContain('图片提问尚未接入');
  });

  it('opens a verified citation in an inspector and removes it when maintenance hides the answer', async () => {
    let hidden = false;
    const root = await page(async (url) => {
      const path = String(url);
      if (path.includes('knowledge-bases')) return json({ items: [{ id: 'kb', name: '合成库', status: hidden ? 'maintaining' : 'ready' }] });
      if (path.endsWith('/messages')) return json({ items: [{
        message_id: 'm', attempt_id: 'a', client_message_id: 'c', question: '合成问题',
        text: '核验后的回答', mode: 'semantic', status: 'answered', kb_revision: 1,
        error_code: null, created_at: '2026-09-24T00:00:00Z', saved: true, hidden,
        citations: [{ evidence_id: 'e1', document_id: 'doc', filename: '合成原文.txt',
          locator: { kind: 'lines', line_start: 2, line_end: 3 }, excerpt: '仅来自 API 的原文片段' }],
      }] });
      return json({ items: [{ id: 'chat', owner_id: 'local', kb_id: 'kb', title: '合成聊天', created_at: '2026-09-24T00:00:00Z' }] });
    });
    button(root, '合成聊天')?.click();
    await vi.waitFor(() => expect(root.querySelector('.citation-trigger')).not.toBeNull());
    (root.querySelector('.citation-trigger') as HTMLButtonElement).click();
    const panel = root.querySelector('[aria-label="来源核查"]');
    expect(panel?.textContent).toContain('仅来自 API 的原文片段');
    expect(panel?.textContent).toContain('第 2–3 行');
    expect(panel?.querySelector('a')?.getAttribute('href')).toBe('/api/documents/doc/original');
    hidden = true;
    button(root, '我的知识库')?.click();
    button(root, '返回工作台')?.click();
    await vi.waitFor(() => expect(root.textContent).toContain('旧知识回答与证据暂不显示'));
    expect(root.querySelector('[aria-label="来源核查"]')).toBeNull();
  });

  it('opens and closes mobile navigation with the same accessible control', async () => {
    const root = await page(async () => json({ items: [] }));
    const toggle = root.querySelector<HTMLButtonElement>('[aria-label="切换导航"]');
    expect(toggle?.getAttribute('aria-expanded')).toBe('false');
    toggle?.click();
    expect(root.querySelector('[aria-label="切换导航"]')?.getAttribute('aria-expanded')).toBe('true');
    (root.querySelector('[aria-label="关闭导航"]') as HTMLButtonElement)?.click();
    expect(root.querySelector('[aria-label="切换导航"]')?.getAttribute('aria-expanded')).toBe('false');
  });

  it('closes navigation when selecting a saved chat', async () => {
    const root = await page(async (url) => {
      const path = String(url);
      if (path.includes('knowledge-bases')) return json({ items: [{ id: 'kb', name: '合成库', status: 'ready' }] });
      if (path.endsWith('/messages')) return json({ items: [] });
      return json({ items: [{ id: 'chat', owner_id: 'local', kb_id: 'kb', title: '合成聊天', created_at: '2026-09-24T00:00:00Z' }] });
    });
    (root.querySelector('[aria-label="切换导航"]') as HTMLButtonElement).click();
    button(root, '合成聊天')?.click();
    await vi.waitFor(() => expect(root.querySelector('[aria-label="切换导航"]')?.getAttribute('aria-expanded')).toBe('false'));
    expect(root.querySelector('[aria-label="关闭导航"]')).toBeNull();
  });

  it('opens processing tasks for the selected knowledge base through the document and job APIs', async () => {
    const reads: string[] = [];
    const root = await page(async (url) => {
      const path = String(url); reads.push(path);
      if (path.includes('/documents?')) return json({ items: [], total: 0, counts: {} });
      if (path.includes('/jobs?')) return json({ items: [], total: 0, active_items: [], failed_count: 0 });
      return json({ items: path === '/api/knowledge-bases' ? [{ id: 'kb', name: '合成库', status: 'ready' }] : [] });
    });
    const picker = root.querySelector<HTMLSelectElement>('#chat-kb')!;
    picker.value = 'kb'; picker.dispatchEvent(new Event('change'));
    expect(button(root, '处理任务')).toBeDefined();
    button(root, '处理任务')?.click();
    await vi.waitFor(() => expect(root.textContent).toContain('暂无进行中的任务'));
    expect(reads).toContain('/api/knowledge-bases/kb/documents?scope=current&limit=10&offset=0');
    expect(reads).toContain('/api/knowledge-bases/kb/jobs?scope=history&limit=10&offset=0');
  });

  it('opens tasks for the library being viewed instead of the fixed chat library', async () => {
    const reads: string[] = [];
    const root = await page(async (url) => {
      const path = String(url); reads.push(path);
      if (path.includes('/documents?')) return json({ items: [], total: 0, counts: {} });
      if (path.includes('/jobs?')) return json({ items: [], total: 0, active_items: [], failed_count: 0 });
      return json({ items: path === '/api/knowledge-bases'
        ? [{ id: 'a', name: '库 A', status: 'ready' }, { id: 'b', name: '库 B', status: 'ready' }] : [] });
    });
    const picker = root.querySelector<HTMLSelectElement>('#chat-kb')!;
    picker.value = 'a'; picker.dispatchEvent(new Event('change'));
    button(root, '我的知识库')?.click();
    (root.querySelector('[aria-label="查看资料与任务：库 B"]') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(root.textContent).toContain('暂无进行中的任务'));
    reads.length = 0;
    button(root, '处理任务')?.click();
    await vi.waitFor(() => expect(reads).toContain('/api/knowledge-bases/b/jobs?scope=history&limit=10&offset=0'));
    expect(reads.some(path => path.includes('/knowledge-bases/a/'))).toBe(false);
  });

  it.each([
    ['answer_unverifiable', '未通过格式或原文核验'],
    ['answer_format_invalid', '模型返回的格式不完整'],
    ['answer_reference_invalid', '引用编号未通过核验'],
    ['answer_source_mismatch', '引用摘录无法匹配原文'],
    ['answer_unsupported_claims', '未得到资料支持的事实'],
    ['answer_verification_unavailable', '事实核验未完成'],
    ['answer_unavailable', '模型服务暂不可用'],
    ['answer_output_limit', '生成达到输出长度上限'],
    ['constructor', '回答未完成，请检查任务与服务状态'],
    ['future_answer_error', '回答未完成，请检查任务与服务状态'],
  ])('shows a safe explanation for %s while keeping retry available', async (code, reason) => {
    const root = await page(async (input) => {
      const path = String(input);
      if (path === '/api/knowledge-bases') return json({ items: [{ id: 'kb', name: '合成库', status: 'ready' }] });
      if (path === '/api/conversations/chat/messages') return json({ items: [{
        message_id: 'message', attempt_id: 'attempt', client_message_id: 'client', question: '合成问题',
        text: '', mode: 'auto', status: 'failed', kb_revision: 1, error_code: code,
        created_at: '2026-09-24T00:00:00Z', saved: true, citations: [],
      }] });
      return json({ items: [{ id: 'chat', owner_id: 'local', kb_id: 'kb', title: '合成聊天',
        created_at: '2026-09-24T00:00:00Z' }] });
    });
    button(root, '合成聊天')?.click();
    await vi.waitFor(() => expect(root.querySelector('.chat-answer')?.textContent).toContain(reason));
    expect(root.textContent).toContain(code);
    expect(button(root, '重试回答')?.disabled).toBe(false);
  });
  it('restores a selected chat from session storage and reloads saved messages after refresh', async () => {
    let messageReads = 0;
    const fetcher: typeof fetch = async (input) => {
      const path = String(input);
      if (path === '/api/knowledge-bases') return json({ items: [{ id: 'kb', name: '合成库', status: 'ready' }] });
      if (path === '/api/conversations?limit=20&offset=0') return json({ items: [
        { id: 'chat', owner_id: 'local', kb_id: 'kb', title: '合成聊天', created_at: '2026-09-24T00:00:00Z' },
      ] });
      if (path === '/api/conversations/chat/messages') {
        messageReads++;
        return json({ items: [{ message_id: 'message', attempt_id: 'attempt', client_message_id: 'client',
          question: '合成问题', text: '合成回答', mode: 'semantic', status: 'answered', kb_revision: 1,
          error_code: null, created_at: '2026-09-24T00:00:00Z', saved: true, citations: [] }] });
      }
      return json({ items: [] });
    };
    const first = await page(fetcher);
    button(first, '合成聊天')?.click();
    await vi.waitFor(() => expect(first.textContent).toContain('合成回答'));
    first.remove();
    const restored = await page(fetcher);
    await vi.waitFor(() => expect(restored.textContent).toContain('合成回答'));
    expect(restored.textContent).toContain('固定知识库：合成库');
    expect(messageReads).toBe(2);
  });

  it('recovers a saved answer after SSE disconnect without resubmitting the question', async () => {
    let historyReads = 0;
    let submissions = 0;
    let accepted: Record<string, unknown> | null = null;
    const root = await page(async (input, init) => {
      const path = String(input);
      if (path === '/api/knowledge-bases')
        return json({ items: [{ id: 'kb', name: '合成库', status: 'ready' }] });
      if (path === '/api/conversations?limit=20&offset=0')
        return json({ items: [{ id: 'chat', owner_id: 'local', kb_id: 'kb', title: '合成聊天',
          created_at: '2026-09-24T00:00:00Z' }] });
      if (path === '/api/conversations/chat/messages' && init?.method !== 'POST') {
        historyReads++;
        return json({ items: historyReads === 1 || !accepted ? [] : [{ ...accepted,
          ...(historyReads >= 3 ? { status: 'answered', text: '已核验合成回答', saved: true } : {}) }] });
      }
      if (path === '/api/conversations/chat/messages/stream') {
        submissions++;
        const body = JSON.parse(String(init?.body));
        accepted = { message_id: 'message', attempt_id: 'attempt', client_message_id: body.client_message_id,
          question: body.text, text: '', mode: 'semantic', status: 'running', kb_revision: 1,
          error_code: null, created_at: '2026-09-24T00:00:00Z', saved: false, citations: [] };
        return new Response(`event: accepted\ndata: ${JSON.stringify(accepted)}\n\n`,
          { headers: { 'Content-Type': 'text/event-stream' } });
      }
      return json({ items: [] });
    });
    button(root, '合成聊天')?.click();
    await vi.waitFor(() => expect(root.querySelector('textarea')?.disabled).toBe(false));
    const input = root.querySelector('textarea')!;
    input.value = '合成问题';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    button(root, '发送')?.click();
    await vi.waitFor(() => expect(root.textContent).toContain('已核验合成回答'), { timeout: 3500 });
    expect(submissions).toBe(1);
  });
  it('opens the workbench without an identity request or login and keeps future capabilities disabled', async () => {
    const calls: string[] = [];
    const root = await page(async (input) => {
      calls.push(String(input));
      return json({ items: [] });
    });
    expect(calls.sort()).toEqual(['/api/conversations?limit=20&offset=0', '/api/knowledge-bases']);
    expect(root.textContent).toContain('本地工作台');
    expect(root.textContent).toContain('暂无聊天');
    expect(root.textContent).toContain('还没有知识库');
    expect(root.querySelector('form')).toBeNull();
    expect(root.textContent).not.toMatch(/管理员|登录|退出|账号/);
    for (const label of ['新建聊天', '发送']) expect(button(root, label)?.disabled).toBe(true);
    expect(button(root, '添加图片')?.disabled).toBe(true);
    expect(button(root, '开始语音')).toBeUndefined();
    expect(root.querySelector('textarea')?.disabled).toBe(true);
  });

  it('keeps management and status accessible when the database is not configured', async () => {
    const root = await page(async (input) => input === '/api/status' ? json(status) : failure(503, 'database_not_configured'));
    expect(root.textContent).toContain('业务数据库尚未配置');
    expect(root.textContent).not.toContain('还没有知识库');
    expect(root.querySelector('form')).toBeNull();
    expect(button(root, '我的知识库')?.disabled).toBe(false);
    expect(button(root, '系统状态')?.disabled).toBe(false);
    button(root, '我的知识库')?.click();
    expect(root.querySelector('h1')?.textContent).toBe('我的知识库');
    expect(root.textContent).toContain('业务数据库尚未配置');
    expect(button(root, '资料与任务')).toBeUndefined();
    expect(root.querySelector<HTMLButtonElement>('[aria-label="新建知识库"]')?.disabled).toBe(true);
    button(root, '系统状态')?.click();
    await vi.waitFor(() => expect(root.querySelector('.health-list')?.textContent).toContain('尚未配置'));
    expect(root.textContent).not.toMatch(/管理员|登录|账号/);
  });

  it('navigates between the local knowledge collection and chat sidebar without role gates', async () => {
    const root = await page(async (input) => input === '/api/knowledge-bases'
      ? json({ items: [{ id: 'test-kb', name: '研发资料', status: 'empty' }] }) : json({ items: [] }));
    button(root, '我的知识库')?.click();
    const navigation = root.querySelector('aside[aria-label="我的知识库与管理导航"]');
    expect(navigation?.textContent).toContain('研发资料');
    expect(button(root, '新建聊天')).toBeUndefined();
    button(root, '返回工作台')?.click();
    expect(root.querySelector('aside[aria-label="聊天与导航"]')).not.toBeNull();
    expect(root.textContent).toContain('同一安装使用同一份本地资料');
    expect(root.textContent).not.toContain('仅自己可见');
  });

  it('retries a failed collection request and replaces the error with database results', async () => {
    let connected = false;
    const root = await page(async () => connected ? json({ items: [] }) : failure(503, 'database_unavailable'));
    expect(root.textContent).toContain('业务数据库暂不可用');
    expect(root.textContent).not.toContain('还没有知识库');
    connected = true;
    button(root, '重新连接')?.click();
    await vi.waitFor(() => expect(root.textContent).toContain('还没有知识库'));
    expect(root.querySelector('[role=alert]')).toBeNull();
  });

  it('keeps navigation available during slow collection loading without showing empty data', async () => {
    let resolveRequest!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => { resolveRequest = resolve; });
    const root = document.createElement('div');
    document.body.append(root);
    const mounted = mountApp(root, createApi(async (input) => input === '/api/knowledge-bases' ? pending : json({ items: [] })));
    button(root, '我的知识库')?.click();
    expect(root.querySelector('h1')?.textContent).toBe('我的知识库');
    expect(root.textContent).toContain('正在读取知识库');
    expect(root.textContent).not.toContain('还没有知识库');
    resolveRequest(json({ items: [] }));
    await mounted;
    expect(root.querySelector('h1')?.textContent).toBe('我的知识库');
    expect(root.textContent).toContain('还没有知识库');
  });

  it('renders knowledge names and chat titles as text without executing markup', async () => {
    const markup = '<img src=x onerror=alert(1)>';
    const root = await page(async (input) => input === '/api/knowledge-bases'
      ? json({ items: [{ id: 'kb', name: markup, status: 'maintaining' }] })
      : json({ items: [{ id: 'chat', owner_id: 'local', kb_id: 'kb', title: markup, created_at: '2026-09-22T00:00:00Z' }] }));
    expect(root.querySelector('.knowledge-name')?.textContent).toBe(markup);
    expect(root.querySelector('.history-title')?.textContent).toBe(markup);
    expect(root.querySelector('img[onerror]')).toBeNull();
    button(root, '我的知识库')?.click();
    expect(root.querySelector('tbody')?.textContent).toContain(markup);
    expect(root.querySelector('img[onerror]')).toBeNull();
    expect(root.textContent).toContain('维护中，问答暂停');
  });

  it('reports a network failure without interpreting it as an empty knowledge base', async () => {
    const root = await page(async () => { throw new TypeError('unreachable'); });
    expect(root.textContent).toContain('无法连接服务');
    expect(root.textContent).not.toContain('还没有知识库');
    expect(button(root, '重新连接')?.disabled).toBe(false);
    expect(button(root, '系统状态')?.disabled).toBe(false);
  });

  it('fetches local service status and shows unavailable capabilities without claiming readiness', async () => {
    const root = await page(async (input) => input === '/api/status' ? json({ ...status, database: 'available' }) : json({ items: [] }));
    button(root, '系统状态')?.click();
    await vi.waitFor(() => expect(root.textContent).toContain('部分能力可用'));
    expect(root.querySelector('.health-list')?.textContent).toContain('本地单用户');
    expect(root.textContent).toContain('尚未配置');
    expect(root.textContent).not.toContain('所有服务正常');
    expect(root.textContent).not.toMatch(/账号|登录|管理员/);
  });

  it('retries status independently of database-backed list failures', async () => {
    let statusAvailable = false;
    const root = await page(async (input) => input === '/api/status'
      ? statusAvailable ? json(status) : failure(500, 'status_unavailable')
      : failure(503, 'database_not_configured'));
    button(root, '系统状态')?.click();
    await vi.waitFor(() => expect(root.querySelector('[role=alert]')).not.toBeNull());
    statusAvailable = true;
    button(root, '刷新状态')?.click();
    await vi.waitFor(() => expect(root.querySelector('.health-list')).not.toBeNull());
    expect(root.querySelector('[role=alert]')).toBeNull();
  });

  it('keeps a successful knowledge collection visible when only chat loading fails', async () => {
    let chatAvailable = false;
    const root = await page(async (input) => input === '/api/knowledge-bases'
      ? json({ items: [{ id: 'kb', name: '本地资料', status: 'ready' }] })
      : chatAvailable ? json({ items: [] }) : failure(503, 'persistence_failed'));
    expect(root.querySelector('.knowledge-list')?.textContent).toContain('本地资料');
    expect(root.querySelector('aside')?.textContent).toContain('聊天列表暂不可用');
    button(root, '我的知识库')?.click();
    expect(root.querySelector('tbody')?.textContent).toContain('本地资料');
    expect(root.querySelector('main [role=alert]')).toBeNull();
    button(root, '返回工作台')?.click();
    await vi.waitFor(() => expect(button(root, '重新读取聊天')).toBeDefined());
    chatAvailable = true;
    button(root, '重新读取聊天')?.click();
    await vi.waitFor(() => expect(root.querySelector('aside')?.textContent).toContain('暂无聊天'));
    expect(root.querySelector('.knowledge-list')?.textContent).toContain('本地资料');
  });

  it('keeps successful chat titles when only the knowledge collection fails', async () => {
    const root = await page(async (input) => input === '/api/knowledge-bases'
      ? failure(503, 'database_unavailable')
      : json({ items: [{ id: 'chat', owner_id: 'local', kb_id: 'kb', title: '已保存的聊天', created_at: '2026-09-22T00:00:00Z' }] }));
    expect(root.querySelector('.history-title')?.textContent).toBe('已保存的聊天');
    expect(root.querySelector('main [role=alert]')?.textContent).toContain('业务数据库暂不可用');
    button(root, '我的知识库')?.click();
    expect(root.querySelector('main [role=alert]')?.textContent).toContain('业务数据库暂不可用');
    expect(root.querySelector('tbody')).toBeNull();
  });

  it('reloads the selected chat after document maintenance and hides old answers', async () => {
    let changed = false;
    let messageReads = 0;
    const chat = { id: 'chat', owner_id: 'local', kb_id: 'kb', title: '合成聊天', created_at: '2026-09-22T00:00:00Z' };
    const message = { message_id: 'message', attempt_id: 'attempt', client_message_id: 'client',
      question: '旧问题', text: '旧知识回答', mode: 'semantic', status: 'answered', kb_revision: 1,
      error_code: null, created_at: '2026-09-22T00:00:00Z', saved: true,
      citations: [{ evidence_id: 'e1', document_id: 'doc', filename: 'old.txt',
        excerpt: '旧证据', locator: { kind: 'lines', line_start: 1, line_end: 1 } }] };
    const root = await page(async (input) => {
      const path = String(input);
      if (path === '/api/knowledge-bases') return json({ items: [{ id: 'kb', name: '合成库', status: 'ready' }] });
      if (path.startsWith('/api/conversations/chat/messages')) {
        messageReads += 1;
        return json({ items: [{ ...message, text: changed ? '' : message.text,
          citations: changed ? [] : message.citations, hidden: changed, stale: changed }] });
      }
      return json({ items: [chat] });
    });
    button(root, '合成聊天')?.click();
    await vi.waitFor(() => expect(root.textContent).toContain('旧知识回答'));
    button(root, '我的知识库')?.click();
    changed = true;
    button(root, '返回工作台')?.click();
    expect(root.textContent).not.toContain('旧知识回答');
    await vi.waitFor(() => expect(root.textContent).toContain('旧知识回答与证据暂不显示'));
    expect(root.textContent).not.toContain('旧证据');
    expect(messageReads).toBe(2);
  });

  it('renames and deletes the selected chat without retaining its transcript', async () => {
    let title = '合成聊天';
    let deleted = false;
    const prompt = vi.spyOn(window, 'prompt').mockReturnValue(' 新名称 ');
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const root = await page(async (input, init) => {
      const path = String(input);
      if (path === '/api/knowledge-bases') return json({ items: [{ id: 'kb', name: '合成库', status: 'ready' }] });
      if (path === '/api/conversations/chat/messages') return json({ items: [] });
      if (path === '/api/conversations/chat' && init?.method === 'PATCH') {
        title = String(JSON.parse(String(init.body)).title);
        return json({ id: 'chat', owner_id: 'local', kb_id: 'kb', title, created_at: '2026-09-22T00:00:00Z' });
      }
      if (path === '/api/conversations/chat' && init?.method === 'DELETE') {
        deleted = true; return json({ deleted: true });
      }
      return json({ items: deleted ? [] : [{ id: 'chat', owner_id: 'local', kb_id: 'kb', title,
        created_at: '2026-09-22T00:00:00Z' }] });
    });
    button(root, '合成聊天')?.click();
    await vi.waitFor(() => expect(button(root, '聊天改名')).toBeDefined());
    button(root, '聊天改名')?.click();
    await vi.waitFor(() => expect(root.textContent).toContain('新名称'));
    button(root, '删除聊天')?.click();
    await vi.waitFor(() => expect(root.querySelector('.chat-transcript')).toBeNull());
    expect(root.textContent).not.toContain('新名称');
    prompt.mockRestore(); confirm.mockRestore();
  });

  it('retries a failed answer as a new attempt on the same saved message', async () => {
    let calls = 0;
    const failed = { message_id: 'message', attempt_id: 'old', client_message_id: 'client',
      question: '合成问题', text: '', mode: 'semantic', status: 'failed', kb_revision: 1,
      error_code: 'retrieval_failed', created_at: '2026-09-22T00:00:00Z', saved: true,
      citations: [], hidden: false, stale: false };
    const root = await page(async (input, init) => {
      const path = String(input);
      if (path === '/api/knowledge-bases') return json({ items: [{ id: 'kb', name: '合成库', status: 'ready' }] });
      if (path === '/api/conversations/chat/messages') return json({ items: [failed] });
      if (path.endsWith('/retry') && init?.method === 'POST') {
        calls += 1;
        return json({ ...failed, attempt_id: JSON.parse(String(init.body)).attempt_id,
          status: 'answered', text: '合成回答', error_code: null });
      }
      return json({ items: [{ id: 'chat', owner_id: 'local', kb_id: 'kb', title: '合成聊天',
        created_at: '2026-09-22T00:00:00Z' }] });
    });
    button(root, '合成聊天')?.click();
    await vi.waitFor(() => expect(button(root, '重试回答')).toBeDefined());
    button(root, '重试回答')?.click();
    await vi.waitFor(() => expect(root.textContent).toContain('合成回答'));
    expect(root.querySelectorAll('.chat-message')).toHaveLength(1);
    expect(button(root, '重试回答')).toBeUndefined();
    expect(calls).toBe(1);
  });

  it('labels a partial source span as incomplete and offers retry without citations', async () => {
    let retryCalls = 0;
    const partial = { message_id: 'message', attempt_id: 'old', client_message_id: 'client',
      question: '合成问题', text: '温度上限是 ', mode: 'semantic', status: 'partial', kb_revision: 1,
      error_code: 'answer_unavailable', created_at: '2026-09-22T00:00:00Z', saved: true,
      citations: [], hidden: false, stale: false };
    const root = await page(async (input, init) => {
      const path = String(input);
      if (path === '/api/knowledge-bases') return json({ items: [{ id: 'kb', name: '合成库', status: 'ready' }] });
      if (path === '/api/conversations/chat/messages') return json({ items: [partial] });
      if (path.endsWith('/retry') && init?.method === 'POST') {
        retryCalls++;
        return json({ ...partial, attempt_id: JSON.parse(String(init.body)).attempt_id,
          status: 'answered', text: '合成回答', error_code: null });
      }
      return json({ items: [{ id: 'chat', owner_id: 'local', kb_id: 'kb', title: '合成聊天',
        created_at: '2026-09-22T00:00:00Z' }] });
    });
    button(root, '合成聊天')?.click();
    await vi.waitFor(() => expect(root.textContent).toContain('上方片段不是完整回答'));
    expect(root.textContent).toContain('温度上限是 ');
    expect(button(root, '重试回答')).toBeDefined();
    expect(root.querySelector('.chat-citations')).toBeNull();
    button(root, '重试回答')?.click();
    await vi.waitFor(() => expect(retryCalls).toBe(1));
    await vi.waitFor(() => expect(root.textContent).toContain('合成回答'));
    expect(button(root, '重试回答')).toBeUndefined();
  });

  it('pages older chats without dropping the current page', async () => {
    const chat = (index: number) => ({ id: `chat-${index}`, owner_id: 'local', kb_id: 'kb',
      title: `会话 ${index}`, created_at: '2026-09-22T00:00:00Z' });
    const calls: string[] = [];
    const root = await page(async (input) => {
      const path = String(input);
      calls.push(path);
      if (path === '/api/knowledge-bases') return json({ items: [{ id: 'kb', name: '合成库', status: 'ready' }] });
      return json({ items: path.includes('offset=20') ? [chat(20)] : Array.from({ length: 20 }, (_, i) => chat(i)) });
    });
    expect(root.querySelectorAll('.history-entry')).toHaveLength(20);
    button(root, '加载更多聊天')?.click();
    await vi.waitFor(() => expect(root.querySelectorAll('.history-entry')).toHaveLength(21));
    expect(root.textContent).toContain('会话 20');
    expect(button(root, '加载更多聊天')).toBeUndefined();
    expect(calls).toContain('/api/conversations?limit=20&offset=20');
  });

  it('restores a selected chat beyond the first page without shifting pagination', async () => {
    sessionStorage.setItem('citerag.workbench.selection', JSON.stringify({ kbId: 'kb', chatId: 'older' }));
    const calls: string[] = [];
    const chat = (index: number) => ({ id: `chat-${index}`, owner_id: 'local', kb_id: 'kb',
      title: `会话 ${index}`, created_at: '2026-09-22T00:00:00Z' });
    const root = await page(async (input) => {
      const path = String(input);
      calls.push(path);
      if (path === '/api/knowledge-bases') return json({ items: [{ id: 'kb', name: '合成库', status: 'ready' }] });
      if (path === '/api/conversations/older') return json({ ...chat(21), id: 'older', title: '已选旧聊天' });
      if (path === '/api/conversations/older/messages') return json({ items: [] });
      return json({ items: path.includes('offset=20') ? [chat(20)] : Array.from({ length: 20 }, (_, i) => chat(i)) });
    });
    expect(root.querySelector('h1')?.textContent).toBe('已选旧聊天');
    expect(button(root, '聊天改名')).toBeDefined();
    button(root, '加载更多聊天')?.click();
    await vi.waitFor(() => expect(root.textContent).toContain('会话 20'));
    expect(calls).toContain('/api/conversations?limit=20&offset=20');
  });

  it('keeps pagination aligned after creating a chat at the front', async () => {
    sessionStorage.setItem('citerag.workbench.selection', JSON.stringify({ kbId: 'kb', chatId: null }));
    const chat = (index: number) => ({ id: `chat-${index}`, owner_id: 'local', kb_id: 'kb',
      title: `会话 ${index}`, created_at: '2026-09-22T00:00:00Z' });
    const all = Array.from({ length: 21 }, (_, i) => chat(i));
    const calls: string[] = [];
    const root = await page(async (input, init) => {
      const path = String(input); calls.push(path);
      if (path === '/api/knowledge-bases') return json({ items: [{ id: 'kb', name: '合成库', status: 'ready' }] });
      if (path === '/api/conversations' && init?.method === 'POST') {
        const created = { ...chat(99), id: 'created', title: '新聊天' };
        all.unshift(created); return json(created);
      }
      if (path.startsWith('/api/conversations?')) {
        const offset = Number(new URL(path, 'http://localhost').searchParams.get('offset'));
        return json({ items: all.slice(offset, offset + 20) });
      }
      return json({ items: [] });
    });
    button(root, '新建聊天')?.click();
    await vi.waitFor(() => expect(root.textContent).toContain('新聊天'));
    button(root, '加载更多聊天')?.click();
    await vi.waitFor(() => expect(root.textContent).toContain('会话 20'));
    expect(calls).toContain('/api/conversations?limit=20&offset=21');
  });

  it('keeps pagination aligned after deleting a listed chat', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const chat = (index: number) => ({ id: `chat-${index}`, owner_id: 'local', kb_id: 'kb',
      title: `会话 ${index}`, created_at: '2026-09-22T00:00:00Z' });
    const all = Array.from({ length: 21 }, (_, i) => chat(i));
    const calls: string[] = [];
    const root = await page(async (input, init) => {
      const path = String(input); calls.push(path);
      if (path === '/api/knowledge-bases') return json({ items: [{ id: 'kb', name: '合成库', status: 'ready' }] });
      if (path === '/api/conversations/chat-0/messages') return json({ items: [] });
      if (path === '/api/conversations/chat-0' && init?.method === 'DELETE') {
        all.shift(); return json({ deleted: true });
      }
      if (path.startsWith('/api/conversations?')) {
        const offset = Number(new URL(path, 'http://localhost').searchParams.get('offset'));
        return json({ items: all.slice(offset, offset + 20) });
      }
      return json({ items: [] });
    });
    button(root, '会话 0')?.click();
    await vi.waitFor(() => expect(button(root, '删除聊天')).toBeDefined());
    button(root, '删除聊天')?.click();
    await vi.waitFor(() => expect(button(root, '会话 0')).toBeUndefined());
    button(root, '加载更多聊天')?.click();
    await vi.waitFor(() => expect(root.textContent).toContain('会话 20'));
    expect(calls).toContain('/api/conversations?limit=20&offset=19');
    confirm.mockRestore();
  });

  it('uses one question input and sends automatic routing without manual filters', async () => {
    let request: Record<string, unknown> | null = null;
    const root = await page(async (input, init) => {
      if (input === '/api/knowledge-bases') return json({ items: [{ id: 'kb', name: '合成库', status: 'ready' }] });
      if (input === '/api/conversations/chat/messages') return json({ items: [] });
      if (input === '/api/conversations/chat/messages/stream' && init?.method === 'POST') {
        request = JSON.parse(String(init.body));
        return failure(503, 'answer_disabled');
      }
      return json({ items: [{ id: 'chat', owner_id: 'local', kb_id: 'kb', title: '合成聊天',
        created_at: '2026-09-22T00:00:00Z' }] });
    });
    button(root, '合成聊天')?.click();
    await vi.waitFor(() => expect(root.querySelector<HTMLTextAreaElement>('textarea')?.disabled).toBe(false));
    expect(root.querySelector('#chat-mode-select')).toBeNull();
    expect(root.querySelector('.exact-filters')).toBeNull();
    const input = root.querySelector<HTMLTextAreaElement>('textarea')!;
    input.value = 'SYN-01 的安全温度是多少？';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    button(root, '发送问题')?.click();
    await vi.waitFor(() => expect(request).not.toBeNull());
    expect(request).toMatchObject({ text: 'SYN-01 的安全温度是多少？', mode: 'auto' });
    expect(request).not.toHaveProperty('exact');
  });
});
