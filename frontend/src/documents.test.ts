import { afterEach, describe, expect, it, vi } from 'vitest';
import { createApi } from './api/client';
import { mountApp } from './app';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const base = { id: 'kb-docs', name: '合成说明资料', status: 'ready' };
const job = { id: 'job-docs', kb_id: base.id, operation: 'upload', status: 'queued', stage: 'accepted',
  error_code: null, message: null, document_ids: ['doc-one'], created_at: '2026-09-24T00:00:00Z', engine_mutated: false, can_retry: false };
const doc = { id: 'doc-one', filename: 'synthetic.txt', size: 18, status: 'pending', error_code: null, created_at: '2026-09-24T00:00:00Z' };
const button = (root: HTMLElement, text: string) => [...root.querySelectorAll('button')].find((item) => item.textContent === text)!;
const settle = async () => { await vi.waitFor(() => {
  expect(document.querySelector('#document-files')).not.toBeNull();
  expect(button(document.body, '刷新资料与任务')?.disabled).toBe(false);
}); };
const choose = (root: HTMLElement, files: File[]) => {
  const input = root.querySelector<HTMLInputElement>('#document-files')!;
  Object.defineProperty(input, 'files', { configurable: true, value: files });
  input.dispatchEvent(new Event('change', { bubbles: true }));
};
async function page(fetcher: typeof fetch) {
  const root = document.createElement('div'); document.body.append(root);
  await mountApp(root, createApi(async (url, init) => {
    if (url === '/api/knowledge-bases') return json({ items: [base] });
    if (String(url).startsWith('/api/conversations')) return json({ items: [] });
    return fetcher(url, init);
  }));
  button(root, '我的知识库').click();
  button(root, '资料与任务').click();
  await settle();
  return root;
}
afterEach(() => { document.body.replaceChildren(); sessionStorage.clear(); vi.restoreAllMocks(); });

describe('managed ingestion', () => {
  it('keeps a blocked library out of the currently usable count and shows small file sizes in bytes', async () => {
    const root = document.createElement('div'); document.body.append(root);
    await mountApp(root, createApi(async (url) => {
      if (url === '/api/knowledge-bases') return json({ items: [{ ...base, status: 'blocked' }] });
      if (String(url).startsWith('/api/conversations')) return json({ items: [] });
      return json({ items: String(url).endsWith('/documents') ? [{ ...doc, status: 'ready' }] : [] });
    }));
    button(root, '我的知识库').click(); button(root, '资料与任务').click(); await settle();
    expect([...root.querySelectorAll('.stage-step')].map((item) => item.querySelector('strong')?.textContent))
      .toEqual(['1', '1', '0']);
    expect(root.textContent).toContain('18 B');
  });

  it('reads current task detail from its dedicated endpoint without claiming completion', async () => {
    const calls: string[] = [];
    const root = await page(async (url) => {
      const path = String(url);
      calls.push(path);
      if (path === '/api/jobs/job-docs') return json({ ...job, status: 'failed', stage: 'indexing',
        error_code: 'index_failed', engine_mutated: true, can_retry: true });
      return json({ items: path.endsWith('/documents') ? [doc] : [job] });
    });
    button(root, '查看任务详情').click();
    await vi.waitFor(() => expect(root.querySelector('.job-details')?.textContent).toContain('索引写入失败'));
    expect(root.querySelector('.job-details')?.textContent).toContain('处理失败');
    expect(root.querySelector('.job-details')?.textContent).not.toContain('核验与清理通过');
    expect(calls).toContain('/api/jobs/job-docs');
  });

  it('sends multipart without declaring a JSON content type and rejects false completed responses', async () => {
    const captured: RequestInit[] = [];
    const api = createApi(async (_url, init) => { captured.push(init!); return json(job, 202); });
    const result = await api.uploadDocuments(base.id, [new File(['synthetic content'], 'synthetic.txt')], 'same-request');
    expect(result.status).toBe('queued');
    expect(captured[0].body).toBeInstanceOf(FormData);
    expect((captured[0].body as FormData).get('client_request_id')).toBe('same-request');
    expect((captured[0].body as FormData).getAll('files')).toHaveLength(1);
    expect(new Headers(captured[0].headers).has('Content-Type')).toBe(false);
    expect(captured[0].credentials).toBe('omit');
    const broken = createApi(async () => json({ ...job, status: 'succeeded', stage: 'parsing' }));
    await expect(broken.uploadDocuments(base.id, [], 'same-request')).rejects.toMatchObject({ kind: 'invalid-response' });
  });

  it('shows acceptance separately from parsed documents and verified completion', async () => {
    let currentJob = job;
    let currentDoc = doc;
    const root = await page(async (url, init) => {
      if (init?.method === 'POST') return json(currentJob, 202);
      return json({ items: String(url).endsWith('/documents') ? [currentDoc] : [currentJob] });
    });
    await vi.waitFor(() => expect(root.textContent).toContain('已受理，等待处理'));
    expect(root.textContent).not.toContain('入库核验通过');
    currentJob = { ...job, status: 'running', stage: 'parsed' };
    currentDoc = { ...doc, status: 'parsed' };
    button(root, '刷新资料与任务').click();
    await vi.waitFor(() => expect(root.textContent).toContain('解析完成，尚未入库'));
    currentJob = { ...job, status: 'succeeded', stage: 'complete' };
    currentDoc = { ...doc, status: 'ready' };
    button(root, '刷新资料与任务').click();
    await vi.waitFor(() => expect(root.textContent).toContain('入库核验通过'));
    expect(button(root, '新建聊天')).toBeUndefined();
  });

  it('keeps delete and replacement as accepted tasks until verification, using their dedicated endpoints', async () => {
    const ready = { ...doc, status: 'ready', doc_code: 'MAN-120', model_code: 'AX-120', edition: 'V2' };
    const complete = { ...job, status: 'succeeded', stage: 'complete' };
    const operations: string[] = [];
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const fetcher: typeof fetch = async (url, init) => {
      const path = String(url);
      if (init?.method === 'POST') {
        operations.push(path);
        expect(path).toMatch(/^\/api\/documents\/doc-one\/(delete|replacement)$/);
        if (path.endsWith('/replacement')) {
          expect(init.body).toBeInstanceOf(FormData);
          expect((init.body as FormData).get('file')).toBeInstanceOf(File);
        }
        return json({ ...job, id: `job-${operations.length}`, operation: path.endsWith('/delete') ? 'delete' : 'replace' }, 202);
      }
      return json({ items: path.endsWith('/documents') ? [ready] : [complete] });
    };
    const deleting = await page(fetcher);
    button(deleting, '删除资料').click();
    await vi.waitFor(() => expect(deleting.textContent).toContain('删除任务已受理'));
    expect(operations).toContain('/api/documents/doc-one/delete');
    expect(deleting.textContent).toContain('入库核验通过');
    deleting.remove();

    const replacing = await page(fetcher);
    const replacement = new File(['synthetic replacement'], 'replacement.txt', { type: 'text/plain' });
    const input = replacing.querySelector<HTMLInputElement>('.replace-file')!;
    Object.defineProperty(input, 'files', { configurable: true, value: [replacement] });
    input.dispatchEvent(new Event('change', { bubbles: true }));
    await vi.waitFor(() => expect(replacing.textContent).toContain('替换任务已受理'));
    expect(operations).toContain('/api/documents/doc-one/replacement');
    expect(replacing.textContent).toContain('入库核验通过');
  });

  it('retains one upload request key after response loss and remount without storing file content or automatically resending', async () => {
    const keys: string[] = [];
    let accepted = false;
    const fetcher: typeof fetch = async (_url, init) => {
      if (init?.method === 'POST') {
        const body = init.body as FormData; keys.push(String(body.get('client_request_id')));
        if (!accepted) throw new TypeError('response lost');
        return json(job, 202);
      }
      return json({ items: [] });
    };
    const root = await page(fetcher);
    choose(root, [new File(['do not persist source content'], 'synthetic.txt')]);
    button(root, '上传并处理').click();
    await vi.waitFor(() => expect(root.textContent).toContain('受理结果尚未确认'));
    const stored = JSON.stringify(sessionStorage);
    expect(stored).not.toContain('do not persist source content');
    root.remove();
    const reopened = await page(fetcher);
    expect(keys).toHaveLength(1);
    expect(reopened.textContent).toContain('重新选择原文件');
    expect(button(reopened, '同键重试上传').disabled).toBe(true);
    accepted = true;
    choose(reopened, [new File(['do not persist source content'], 'synthetic.txt')]);
    button(reopened, '同键重试上传').click();
    await vi.waitFor(() => expect(keys).toHaveLength(2));
    expect(keys[0]).toBe(keys[1]);
    await vi.waitFor(() => expect(reopened.textContent).not.toContain('受理结果尚未确认'));
  });

  it('keeps unsafe server details out of task failures and presents retry only when permitted', async () => {
    const root = await page(async (url) => json({ items: String(url).endsWith('/documents') ? [] : [
      { ...job, status: 'interrupted', stage: 'indexing', engine_mutated: true, can_retry: true,
        error_code: 'engine_interrupted', message: 'private workspace and response details' },
    ] }));
    await vi.waitFor(() => expect(root.textContent).toContain('处理已中断'));
    expect(root.textContent).toContain('此任务曾修改引擎后失败，当前库状态见上方');
    expect(root.textContent).not.toContain('此库保持待修复');
    expect(root.textContent).not.toContain('private workspace');
    expect(button(root, '重试任务').disabled).toBe(false);
  });

  it('rejects unsupported, empty, oversized and excessive files before any request', async () => {
    let posts = 0;
    const root = await page(async (_url, init) => { if (init?.method === 'POST') posts++; return json({ items: [] }); });
    for (const files of [[new File(['content'], 'unsafe.html')], [new File([], 'empty.txt')],
      Array.from({ length: 6 }, (_, i) => new File(['content'], `sample-${i}.txt`))]) {
      choose(root, files);
      expect(button(root, '上传并处理').disabled).toBe(true);
      expect(root.querySelector('[role="alert"]')).not.toBeNull();
    }
    const large = new File(['content'], 'large.txt');
    Object.defineProperty(large, 'size', { value: 20 * 1024 * 1024 + 1 });
    choose(root, [large]);
    expect(root.textContent).toContain('不超过 20 MiB');
    expect(posts).toBe(0);
  });

  it('does not send when recovery storage is unavailable', async () => {
    let posts = 0;
    const root = await page(async (_url, init) => { if (init?.method === 'POST') posts++; return json({ items: [] }); });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('denied'); });
    choose(root, [new File(['content'], 'synthetic.txt')]);
    button(root, '上传并处理').click();
    await vi.waitFor(() => expect(root.textContent).toContain('无法可靠保存或读取'));
    expect(posts).toBe(0);
  });

  it('preserves an uncertain request and prevents selection of different files from reusing the key', async () => {
    let posts = 0;
    const root = await page(async (_url, init) => {
      if (init?.method === 'POST') { posts++; throw new TypeError('lost'); }
      return json({ items: [] });
    });
    choose(root, [new File(['content'], 'original.txt')]);
    button(root, '上传并处理').click();
    await vi.waitFor(() => expect(root.textContent).toContain('受理结果尚未确认'));
    choose(root, [new File(['different'], 'other.txt')]);
    expect(button(root, '同键重试上传').disabled).toBe(true);
    expect(root.textContent).toContain('所选文件与原请求不一致');
    expect(posts).toBe(1);
  });

  it('renders only supplied file locations and treats extracted text as plain text', async () => {
    const root = await page(async (url) => {
      if (String(url).endsWith('/blocks')) return json({ items: [
        { ordinal: 0, text: '<img src=x onerror=alert(1)>', locator: { kind: 'lines', line_start: 2, line_end: 3 }, start: 0, end: 26 },
        { ordinal: 1, text: 'source without a reliable page', locator: {}, start: 27, end: 57 },
      ] });
      return json({ items: String(url).endsWith('/documents') ? [{ ...doc, status: 'parsed' }] : [] });
    });
    button(root, '查看解析位置').click();
    await vi.waitFor(() => expect(root.textContent).toContain('第 2–3 行'));
    expect(root.textContent).toContain('<img src=x onerror=alert(1)>');
    expect(root.querySelector('.parsed-preview img')).toBeNull();
    expect(root.textContent).toContain('来源片段');
    expect(root.querySelector('.parsed-preview')?.textContent).not.toContain('第 1 页');
  });

  it('shows a failed list as unavailable and prevents writes instead of claiming an empty library', async () => {
    const root = await page(async (url) => String(url).endsWith('/documents')
      ? json({ detail: { code: 'database_unavailable' } }, 503) : json({ items: [] }));
    choose(root, [new File(['content'], 'synthetic.txt')]);
    expect(button(root, '上传并处理').disabled).toBe(true);
    expect(root.textContent).toContain('业务数据库暂不可用');
    expect(root.textContent).not.toContain('尚无受管资料');
  });

  it('retries the original task endpoint and never creates another upload', async () => {
    const posts: string[] = [];
    const failedJob = { ...job, status: 'failed', stage: 'indexing', engine_mutated: true, can_retry: true, error_code: 'index_failed' };
    const root = await page(async (url, init) => {
      if (init?.method === 'POST') { posts.push(String(url)); return json(job, 202); }
      return json({ items: String(url).endsWith('/documents') ? [doc] : [failedJob] });
    });
    button(root, '重试任务').click();
    await vi.waitFor(() => expect(root.textContent).toContain('原任务已受理重试'));
    expect(posts).toEqual(['/api/jobs/job-docs/retry']);
  });

  it('requires acknowledging rebuild impact and restores the same rebuild key after a lost response', async () => {
    const keys: string[] = [];
    let accepted = false;
    const fetcher: typeof fetch = async (url, init) => {
      if (init?.method === 'POST') {
        expect(String(url)).toBe('/api/knowledge-bases/kb-docs/rebuild');
        keys.push(JSON.parse(String(init.body)).client_request_id);
        if (!accepted) throw new TypeError('response lost');
        return json({ ...job, operation: 'rebuild' }, 202);
      }
      return json({ items: String(url).endsWith('/documents') ? [{ ...doc, status: 'ready' }] : [] });
    };
    const root = await page(fetcher);
    expect(button(root, '重建知识库').disabled).toBe(true);
    expect(root.textContent).toContain('遮蔽此前知识回答与证据');
    root.querySelector<HTMLInputElement>('.rebuild-confirmation input')!.click();
    button(root, '重建知识库').click();
    await vi.waitFor(() => expect(root.textContent).toContain('重建受理结果尚未确认'));
    root.remove();
    const reopened = await page(fetcher);
    expect(keys).toHaveLength(1);
    accepted = true;
    button(reopened, '同键重试重建').click();
    await vi.waitFor(() => expect(reopened.textContent).toContain('任务已受理'));
    expect(keys).toHaveLength(2);
    expect(keys[1]).toBe(keys[0]);
  });

  it('respects blocked library visibility and does not create new uploads while a task is active', async () => {
    const root = document.createElement('div'); document.body.append(root);
    await mountApp(root, createApi(async (url) => {
      if (url === '/api/knowledge-bases') return json({ items: [{ ...base, status: 'blocked' }] });
      if (String(url).startsWith('/api/conversations')) return json({ items: [] });
      return json({ items: String(url).endsWith('/documents') ? [{ ...doc, status: 'ready' }] : [job] });
    }));
    button(root, '我的知识库').click(); button(root, '资料与任务').click(); await settle();
    expect(root.querySelector('a[download]')).toBeNull();
    expect(button(root, '查看解析位置').disabled).toBe(true);
    choose(root, [new File(['content'], 'synthetic.txt')]);
    expect(button(root, '上传并处理').disabled).toBe(true);
    root.querySelector<HTMLInputElement>('.rebuild-confirmation input')!.click();
    expect(button(root, '重建知识库').disabled).toBe(true);
    expect(root.textContent).toContain('此库已有待处理任务');
  });

  it('does not present an obsolete failed task as the current library state or offer its retry', async () => {
    const root = await page(async (url) => json({ items: String(url).endsWith('/documents') ? [{ ...doc, status: 'ready' }] : [
      { ...job, operation: 'rebuild', status: 'failed', stage: 'indexing', engine_mutated: true, can_retry: false, error_code: 'stale_job' },
      { ...job, id: 'job-new', operation: 'rebuild', status: 'succeeded', stage: 'complete', engine_mutated: true, can_retry: false },
    ] }));
    expect(root.textContent).toContain('资料就绪');
    expect(root.textContent).toContain('此历史任务已被后续重建替代');
    expect(root.textContent).toContain('此任务曾修改引擎后失败，当前库状态见上方');
    expect(root.textContent).not.toContain('此库保持待修复');
    expect(button(root, '重试任务')).toBeUndefined();
  });

  it('keeps the repair warning for a library that is still blocked', async () => {
    const root = document.createElement('div'); document.body.append(root);
    await mountApp(root, createApi(async (url) => {
      if (url === '/api/knowledge-bases') return json({ items: [{ ...base, status: 'blocked' }] });
      if (String(url).startsWith('/api/conversations')) return json({ items: [] });
      return json({ items: String(url).endsWith('/documents') ? [doc] : [
        { ...job, status: 'interrupted', stage: 'indexing', engine_mutated: true, can_retry: true, error_code: 'interrupted' },
      ] });
    }));
    button(root, '我的知识库').click(); button(root, '资料与任务').click(); await settle();
    expect(root.textContent).toContain('此库保持待修复，问答暂停');
    expect(button(root, '重试任务').disabled).toBe(false);
  });

  it('explains a stale-task rejection without exposing backend response details', async () => {
    const root = await page(async (url, init) => {
      if (init?.method === 'POST') return json({ detail: { code: 'stale_job', message: 'private backend details' } }, 409);
      return json({ items: String(url).endsWith('/documents') ? [doc] : [
        { ...job, status: 'failed', stage: 'indexing', engine_mutated: true, can_retry: true, error_code: 'index_failed' },
      ] });
    });
    button(root, '重试任务').click();
    await vi.waitFor(() => expect(root.textContent).toContain('此历史任务已被后续重建替代'));
    expect(root.textContent).not.toContain('private backend details');
  });
});
