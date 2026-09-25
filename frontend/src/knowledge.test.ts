import { afterEach, describe, expect, it, vi } from 'vitest';
import { mountApp } from './app';
import { createApi } from './api/client';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const base = { id: 'kb-1', name: '已有资料', status: 'empty' };
const health = { status: 'partial', mode: 'local_single_user', database: 'available', rag: 'unverified', models: 'unverified' };
const recoveryKey = 'citerag.pending-knowledge-create.v1';
const button = (root: HTMLElement, label: string) => Array.from(root.querySelectorAll('button')).find((item) => item.textContent === label)!;
const input = (root: HTMLElement, id = 'kb-create-name') => root.querySelector<HTMLInputElement>(`#${id}`)!;
const enter = (root: HTMLElement, value: string, id?: string) => {
  input(root, id).value = value;
  input(root, id).dispatchEvent(new Event('input', { bubbles: true }));
};
const submit = (root: HTMLElement, id = 'kb-create-name') => input(root, id).form!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
async function page(write: typeof fetch, items: unknown[] = []) {
  const root = document.createElement('div');
  document.body.append(root);
  await mountApp(root, createApi(async (url, init) => {
    if (init?.method === 'GET') return json(url === '/api/status' ? health : { items: url === '/api/knowledge-bases' ? items : [] });
    return write(url, init);
  }));
  button(root, '我的知识库').click();
  return root;
}
afterEach(() => { document.body.replaceChildren(); vi.restoreAllMocks(); sessionStorage.clear(); });

describe('M1 knowledge mutations', () => {
  it('records the pending request before sending and replays it after a page remount', async () => {
    let finish!: (response: Response) => void;
    let originalBody = '';
    const first = await page(async (_url, init) => {
      originalBody = String(init?.body);
      const body = JSON.parse(originalBody);
      expect(JSON.parse(sessionStorage.getItem(recoveryKey)!)).toEqual({ name: body.name, key: body.client_request_id });
      return new Promise((resolve) => { finish = resolve; });
    });
    enter(first, '  刷新恢复  ');
    submit(first);
    first.remove();
    const replay = vi.fn<typeof fetch>(async (_url, init) => {
      expect(String(init?.body)).toBe(originalBody);
      return json({ ...base, name: '刷新恢复' }, 201);
    });
    const second = await page(replay, [{ ...base, name: '刷新恢复' }]);
    expect(input(second).value).toBe('刷新恢复');
    expect(input(second).readOnly).toBe(true);
    expect(second.textContent).toContain('创建结果尚未确认');
    expect(replay).not.toHaveBeenCalled();
    submit(second);
    await vi.waitFor(() => expect(input(second).value).toBe(''));
    expect(replay).toHaveBeenCalledTimes(1);
    expect(second.querySelectorAll('tbody tr')).toHaveLength(1);
    expect(sessionStorage.getItem(recoveryKey)).toBeNull();
    finish(json({ ...base, name: '刷新恢复' }, 201));
  });

  it('restores a network-unknown request after remount, even when the committed fifth library fills capacity', async () => {
    let originalBody = '';
    const first = await page(async (_url, init) => { originalBody = String(init?.body); throw new TypeError('lost response'); });
    enter(first, '第五个库');
    submit(first);
    await vi.waitFor(() => expect(first.textContent).toContain('创建结果尚未确认'));
    first.remove();
    const second = await page(async (_url, init) => {
      expect(String(init?.body)).toBe(originalBody);
      return json({ ...base, id: 'kb-4', name: '第五个库' }, 201);
    }, Array.from({length: 5}, (_, index) => ({ ...base, id: `kb-${index}` })));
    expect(button(second, '重试确认创建').disabled).toBe(false);
    submit(second);
    await vi.waitFor(() => expect(input(second).value).toBe(''));
    expect(second.querySelectorAll('tbody tr')).toHaveLength(5);
    expect(sessionStorage.getItem(recoveryKey)).toBeNull();
  });

  it.each([403, 404, 409, 422])('preserves an earlier unknown outcome through a refused HTTP %s retry and another reload', async (status) => {
    const bodies: string[] = [];
    const first = await page(async (_url, init) => {
      bodies.push(String(init?.body));
      throw new TypeError('committed but response lost');
    });
    enter(first, '不能重复创建');
    submit(first);
    await vi.waitFor(() => expect(first.textContent).toContain('创建结果尚未确认'));
    const originalRecord = sessionStorage.getItem(recoveryKey);
    first.remove();

    const second = await page(async (_url, init) => {
      bodies.push(String(init?.body));
      return json({ detail: { code: 'retry_rejected' } }, status);
    });
    submit(second);
    await vi.waitFor(() => expect(second.querySelector('[role=alert]')).not.toBeNull());
    expect(sessionStorage.getItem(recoveryKey)).toBe(originalRecord);
    expect(input(second).readOnly).toBe(true);
    expect(input(second).value).toBe('不能重复创建');
    expect(second.textContent).toContain('创建结果尚未确认');
    second.remove();

    const third = await page(async (_url, init) => {
      bodies.push(String(init?.body));
      return json({ ...base, name: '不能重复创建' }, 201);
    });
    expect(input(third).readOnly).toBe(true);
    submit(third);
    await vi.waitFor(() => expect(input(third).value).toBe(''));
    expect(bodies).toHaveLength(3);
    expect(new Set(bodies).size).toBe(1);
    expect(sessionStorage.getItem(recoveryKey)).toBeNull();
  });

  it.each([
    'not-json', 'null', '[]',
    JSON.stringify({ name: '<img src=x onerror=alert(1)>', key: 'bad-key' }),
    JSON.stringify({ name: '资料', key: 'c0d85213-68bb-4a4a-a08c-30ef86eac651', extra: 'unexpected' }),
    JSON.stringify({ name: '资料\u200b', key: 'c0d85213-68bb-4a4a-a08c-30ef86eac651' }),
    JSON.stringify({ name: ' '.repeat(2000), key: 'c0d85213-68bb-4a4a-a08c-30ef86eac651' }),
  ])('discards malformed recovery data safely: %s', async (stored) => {
    sessionStorage.setItem(recoveryKey, stored);
    const write = vi.fn<typeof fetch>();
    const root = await page(write);
    expect(sessionStorage.getItem(recoveryKey)).toBeNull();
    expect(input(root).value).toBe('');
    expect(root.querySelector('img[onerror]')).toBeNull();
    expect(write).not.toHaveBeenCalled();
  });

  it('renders a valid recovered markup-looking name as inert input text', async () => {
    const name = '<img src=x onerror=alert(1)>';
    sessionStorage.setItem(recoveryKey, JSON.stringify({ name, key: 'c0d85213-68bb-4a4a-a08c-30ef86eac651' }));
    const root = await page(vi.fn<typeof fetch>());
    expect(input(root).value).toBe(name);
    expect(root.querySelector('img[onerror]')).toBeNull();
  });

  it('blocks creating when recovery cannot be stored while leaving rename available', async () => {
    const write = vi.fn<typeof fetch>(async () => json({ ...base, name: '改名可用' }));
    const root = await page(write, [base]);
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('unavailable', 'QuotaExceededError'); });
    enter(root, '不可冒进');
    submit(root);
    await vi.waitFor(() => expect(root.textContent).toContain('恢复记录无法保存'));
    expect(write).not.toHaveBeenCalled();
    expect(input(root).value).toBe('不可冒进');
    button(root, '改名').click();
    enter(root, '改名可用', 'kb-rename-name');
    submit(root, 'kb-rename-name');
    await vi.waitFor(() => expect(root.querySelector('tbody')?.textContent).toContain('改名可用'));
    expect(write).toHaveBeenCalledTimes(1);
    expect(write.mock.calls[0][1]?.method).toBe('PATCH');
  });

  it('blocks creating after an unreadable recovery record without overwriting it', async () => {
    const write = vi.fn<typeof fetch>();
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new DOMException('blocked', 'SecurityError'); });
    const root = await page(write);
    expect(root.textContent).toContain('恢复记录无法读取');
    expect(button(root, '创建知识库').disabled).toBe(true);
    enter(root, '不能覆盖旧请求');
    submit(root);
    expect(write).not.toHaveBeenCalled();
  });

  it.each([403, 404, 409, 422])('clears recovery after a confirmed HTTP %s rejection', async (status) => {
    const root = await page(async () => {
      expect(sessionStorage.getItem(recoveryKey)).not.toBeNull();
      return json({ detail: { code: 'rejected' } }, status);
    });
    enter(root, '确认失败');
    submit(root);
    await vi.waitFor(() => expect(root.querySelector('[role=alert]')).not.toBeNull());
    expect(sessionStorage.getItem(recoveryKey)).toBeNull();
    expect(input(root).value).toBe('确认失败');
    expect(input(root).readOnly).toBe(false);
  });

  it('keeps the same request recoverable if cleanup fails after server confirmation', async () => {
    const bodies: string[] = [];
    const root = await page(async (_url, init) => { bodies.push(String(init?.body)); return json(base, 201); });
    const remove = vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new DOMException('blocked', 'SecurityError'); });
    enter(root, '已有资料');
    submit(root);
    await vi.waitFor(() => expect(root.textContent).toContain('恢复记录未能清除'));
    expect(input(root).readOnly).toBe(true);
    expect(sessionStorage.getItem(recoveryKey)).not.toBeNull();
    expect(root.querySelector('tbody')?.textContent).toContain('已有资料');
    remove.mockRestore();
    submit(root);
    await vi.waitFor(() => expect(input(root).value).toBe(''));
    expect(bodies[0]).toBe(bodies[1]);
    expect(sessionStorage.getItem(recoveryKey)).toBeNull();
  });

  it('does not let a late old response erase a newer recovery record', async () => {
    let finishOld!: (response: Response) => void;
    const first = await page(() => new Promise((resolve) => { finishOld = resolve; }));
    enter(first, '旧请求');
    submit(first);
    first.remove();
    const second = await page(async () => json({ ...base, name: '旧请求' }, 201));
    submit(second);
    await vi.waitFor(() => expect(input(second).value).toBe(''));
    const newRecord = JSON.stringify({ name: '新请求', key: 'c0d85213-68bb-4a4a-a08c-30ef86eac651' });
    sessionStorage.setItem(recoveryKey, newRecord);
    finishOld(json({ ...base, name: '旧请求' }, 201));
    await vi.waitFor(() => expect(first.textContent).toContain('知识库已创建'));
    expect(sessionStorage.getItem(recoveryKey)).toBe(newRecord);
  });

  it('creates only after server confirmation, clears the draft and keeps empty libraries unavailable for questions', async () => {
    let finish!: (response: Response) => void;
    const write = vi.fn<typeof fetch>(() => new Promise((resolve) => { finish = resolve; }));
    const root = await page(write);
    enter(root, '  使用手册  ');
    submit(root);
    expect(write).toHaveBeenCalledTimes(1);
    const payload = JSON.parse(String(write.mock.calls[0][1]?.body));
    expect(payload.name).toBe('使用手册');
    expect(payload.client_request_id).toMatch(/^[0-9a-f-]{36}$/);
    expect(input(root).disabled).toBe(true);
    submit(root);
    expect(write).toHaveBeenCalledTimes(1);
    expect(root.querySelector('tbody')).toBeNull();
    finish(json({ ...base, name: '使用手册' }, 201));
    await vi.waitFor(() => expect(root.querySelector('tbody')?.textContent).toContain('使用手册'));
    expect(input(root).value).toBe('');
    expect(root.textContent).toContain('知识库已创建');
    expect(root.textContent).toContain('暂无资料，不可问答');
    expect(button(root, '资料与任务').disabled).toBe(false);
    expect(button(root, '删除')).toBeUndefined();
    button(root, '返回工作台').click();
    expect(root.querySelector('textarea')?.disabled).toBe(true);
  });

  it('retries an unknown create result with the same name and UUID across navigation and starts a new key only after confirmation', async () => {
    let attempt = 0;
    const bodies: Array<{name: string; client_request_id: string}> = [];
    const root = await page(async (_url, init) => {
      const body = JSON.parse(String(init?.body));
      bodies.push(body);
      if (++attempt === 1) throw new TypeError('response lost');
      return json({ ...base, id: `kb-${attempt}`, name: body.name }, 201);
    });
    enter(root, '确认资料');
    submit(root);
    await vi.waitFor(() => expect(root.textContent).toContain('创建结果尚未确认'));
    expect(input(root).value).toBe('确认资料');
    expect(input(root).readOnly).toBe(true);
    button(root, '系统状态').click();
    await vi.waitFor(() => expect(root.querySelector('.health-list')).not.toBeNull());
    button(root, '我的知识库').click();
    submit(root);
    await vi.waitFor(() => expect(input(root).value).toBe(''));
    expect(bodies[0]).toEqual(bodies[1]);
    enter(root, '新的资料');
    submit(root);
    await vi.waitFor(() => expect(bodies).toHaveLength(3));
    expect(bodies[2].client_request_id).not.toBe(bodies[1].client_request_id);
  });

  it.each(['   ', 'a'.repeat(121), '资料\u0001', '资料\u200b', '资料\ue000', '资料\ud800', '😀'.repeat(121)])('rejects invalid local names without issuing a write', async (name) => {
    const write = vi.fn<typeof fetch>();
    const root = await page(write);
    enter(root, name);
    submit(root);
    expect(write).not.toHaveBeenCalled();
    expect(root.querySelector('[role=alert]')?.textContent).toContain('名称须为 1–120 个字符');
    expect(input(root).getAttribute('aria-invalid')).toBe('true');
  });

  it('accepts 120 Unicode code points and renders returned names as text', async () => {
    const name = '😀'.repeat(120);
    const markup = '<img src=x onerror=alert(1)>';
    const write = vi.fn<typeof fetch>(async () => json({ ...base, name: markup }, 201));
    const root = await page(write);
    enter(root, name);
    submit(root);
    await vi.waitFor(() => expect(root.querySelector('tbody')?.textContent).toContain(markup));
    expect(JSON.parse(String(write.mock.calls[0][1]?.body)).name).toBe(name);
    expect(root.querySelector('img[onerror]')).toBeNull();
  });

  it('shows server capacity failure safely, preserving the draft for correction', async () => {
    const root = await page(async () => json({ detail: { code: 'capacity_exceeded', message: 'private-host' } }, 409));
    enter(root, '保留名称');
    submit(root);
    await vi.waitFor(() => expect(root.textContent).toContain('最多可创建 5 个知识库'));
    expect(input(root).value).toBe('保留名称');
    expect(input(root).readOnly).toBe(false);
    expect(root.textContent).not.toContain('private-host');
    expect(root.querySelector('tbody')).toBeNull();
  });

  it('retains the same create request after an incomplete success response', async () => {
    const bodies: string[] = [];
    const root = await page(async (_url, init) => {
      bodies.push(String(init?.body));
      return json(bodies.length === 1 ? { id: 'kb-1', name: '不完整' } : base, 201);
    });
    enter(root, '已有资料');
    submit(root);
    await vi.waitFor(() => expect(root.textContent).toContain('创建结果尚未确认'));
    expect(root.querySelector('tbody')).toBeNull();
    submit(root);
    await vi.waitFor(() => expect(root.querySelector('tbody')?.textContent).toContain('已有资料'));
    expect(bodies[0]).toBe(bodies[1]);
  });

  it('uses a new request key when a confirmed rejection is corrected with another name', async () => {
    const bodies: Array<{name: string; client_request_id: string}> = [];
    const root = await page(async (_url, init) => {
      bodies.push(JSON.parse(String(init?.body)));
      return json({ detail: { code: 'invalid_name' } }, 422);
    });
    enter(root, '原名称');
    submit(root);
    await vi.waitFor(() => expect(input(root).getAttribute('aria-invalid')).toBe('true'));
    enter(root, '新名称');
    submit(root);
    await vi.waitFor(() => expect(bodies).toHaveLength(2));
    expect(bodies[0].client_request_id).not.toBe(bodies[1].client_request_id);
    expect(bodies[1].name).toBe('新名称');
  });

  it('explains the known five-library limit before issuing another request', async () => {
    const write = vi.fn<typeof fetch>();
    const root = await page(write, Array.from({length: 5}, (_, index) => ({ ...base, id: `kb-${index}` })));
    expect(button(root, '创建知识库').disabled).toBe(true);
    expect(root.textContent).toContain('已达到 5 个知识库上限');
    submit(root);
    expect(write).not.toHaveBeenCalled();
  });

  it('renames inline after confirmation and retains the original row on failure', async () => {
    let succeeded = false;
    const calls: Array<[string, string]> = [];
    const root = await page(async (url, init) => {
      calls.push([String(url), String(init?.body)]);
      return succeeded ? json({ ...base, name: '新资料' }) : json({ detail: { code: 'kb_not_found' } }, 404);
    }, [base]);
    button(root, '改名').click();
    enter(root, '  新资料 ', 'kb-rename-name');
    submit(root, 'kb-rename-name');
    await vi.waitFor(() => expect(root.textContent).toContain('知识库已不存在或不属于当前本地安装'));
    expect(input(root, 'kb-rename-name').value).toBe('  新资料 ');
    expect(root.querySelector('aside')?.textContent).toContain('已有资料');
    succeeded = true;
    submit(root, 'kb-rename-name');
    await vi.waitFor(() => expect(root.querySelector('tbody')?.textContent).toContain('新资料'));
    expect(root.querySelector('#kb-rename-name')).toBeNull();
    expect(calls[1]).toEqual(['/api/knowledge-bases/kb-1', JSON.stringify({ name: '新资料' })]);
    expect(root.querySelector('aside')?.textContent).toContain('新资料');
  });

  it('does not let a delayed mutation result replace the current status page', async () => {
    let finish!: (response: Response) => void;
    const root = await page(() => new Promise((resolve) => { finish = resolve; }));
    enter(root, '慢请求');
    submit(root);
    button(root, '系统状态').click();
    await vi.waitFor(() => expect(root.querySelector('.health-list')).not.toBeNull());
    finish(json({ ...base, name: '慢请求' }, 201));
    await vi.waitFor(() => expect(root.querySelector('aside')?.textContent).toContain('慢请求'));
    expect(root.querySelector('h1')?.textContent).toBe('系统状态');
    expect(root.querySelector('#kb-create-name')).toBeNull();
    button(root, '我的知识库').click();
    expect(root.querySelector('tbody')?.textContent).toContain('慢请求');
  });

  it('blocks duplicate rename submissions and keeps the current workbench when a rename finishes late', async () => {
    let finish!: (response: Response) => void;
    const write = vi.fn<typeof fetch>(() => new Promise((resolve) => { finish = resolve; }));
    const root = await page(write, [base]);
    button(root, '改名').click();
    enter(root, '最终名称', 'kb-rename-name');
    submit(root, 'kb-rename-name');
    submit(root, 'kb-rename-name');
    expect(write).toHaveBeenCalledTimes(1);
    expect(button(root, '刷新列表').disabled).toBe(true);
    expect(button(root, '创建知识库').disabled).toBe(true);
    expect(button(root, '取消').disabled).toBe(true);
    button(root, '返回工作台').click();
    finish(json({ ...base, name: '最终名称' }));
    await vi.waitFor(() => expect(root.querySelector('.knowledge-list')?.textContent).toContain('最终名称'));
    expect(root.querySelector('h1')?.textContent).toBe('开始文字问答');
    expect(root.querySelector('.management-content')).toBeNull();
  });
});
