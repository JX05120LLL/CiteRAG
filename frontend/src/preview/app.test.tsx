import { StrictMode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react';
import { PreviewApp } from './App';
import type { PreviewDesign, PreviewLocation } from './model';
import { usePreviewResource } from './useData';
import { Knowledge } from './Management';
import { sampleBases, sampleChats, sampleDocuments, sampleMessages } from './samples';
import { Workbench } from './Workbench';

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
// jsdom does not implement layout / media queries. Browser coverage checks the real implementations.
beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => window.setTimeout(() => callback(performance.now()), 16));
  vi.stubGlobal('cancelAnimationFrame', (id: number) => window.clearTimeout(id));
  vi.stubGlobal('matchMedia', (query: string) => ({ matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; } }));
  const computedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, 'getComputedStyle').mockImplementation((element) => computedStyle(element));
});

describe('request lifetimes and real pagination adapter', () => {
  it('never labels an uncommitted answer as saved or publishes its text and sources', async () => {
    const message = { ...sampleMessages['design-chat-1'][0], saved: false };
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ items: [message] })));
    render(<Workbench location={{ ...location('b'), data: 'live' }} kb={sampleBases[0]} chat={sampleChats[0]} refresh={0} navigate={() => {}} />);
    await screen.findByText('等待回答核验与保存，尚未发布正文和引用。');
    expect(screen.queryByText('回答已保存')).toBeNull();
    expect(screen.queryByText(message.text)).toBeNull();
    expect(screen.queryByRole('button', { name: /查看来源：/ })).toBeNull();
  });

  it('removes an already opened source when refreshed history becomes hidden', async () => {
    const message = sampleMessages['design-chat-1'][0];
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({ items: [message] })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ items: [{ ...message, hidden: true }] })));
    const props = { location: { ...location('b'), data: 'live' as const }, kb: sampleBases[0], chat: sampleChats[0], refresh: 0, navigate: () => {} };
    const { rerender } = render(<Workbench {...props} />);
    fireEvent.click(await screen.findByRole('button', { name: /查看来源：合成产品手册/ }, { timeout: 5000 }));
    expect(screen.getAllByText(message.citations[0].excerpt).length).toBeGreaterThan(0);
    rerender(<Workbench {...props} refresh={1} />);
    await screen.findByText('资料已变化，此回答暂不展示。请核对当前知识库后重新提问。');
    expect(screen.queryAllByText(message.citations[0].excerpt)).toHaveLength(0);
  });

  it('does not relabel samples as live data or accept a late result after changing origin', async () => {
    const pending: ((value: Response) => void)[] = [];
    const signals: AbortSignal[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation((_input, init) => {
      signals.push(init?.signal as AbortSignal);
      return new Promise<Response>((resolve) => pending.push(resolve));
    });
    const { result, rerender, unmount } = renderHook(({ mode }: { mode: 'live' | 'sample' }) =>
      usePreviewResource(mode, 'bases', (api) => api.knowledgeBases()), { initialProps: { mode: 'live' } });
    rerender({ mode: 'sample' });
    expect(signals[0].aborted).toBe(true);
    await waitFor(() => expect(result.current.data?.[0].name).toBe('产品说明与故障查询'));
    await act(async () => pending[0](new Response(JSON.stringify({ items: [{ id: 'late-base', name: '迟到的合成 API 响应', status: 'ready' }] }))));
    expect(result.current.data?.[0].name).toBe('产品说明与故障查询');
    rerender({ mode: 'live' });
    expect(result.current.data).toBeNull();
    unmount(); expect(signals.at(-1)?.aborted).toBe(true);
  });

  it('requests page two and deleted scope through GET, without locally slicing the first page', async () => {
    const fetcher = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = new URL(String(input), 'http://127.0.0.1');
      const deleted = url.searchParams.get('scope') === 'deleted';
      const offset = Number(url.searchParams.get('offset'));
      return new Response(JSON.stringify({ items: deleted ? [] : sampleDocuments.slice(offset, offset + 10),
        total: deleted ? 0 : 17, counts: deleted ? {} : { ready: 16, failed: 1 } }));
    });
    render(<Knowledge location={{ ...location('b', 'knowledge'), data: 'live' }} kb={sampleBases[0]} chat={undefined} refresh={0} navigate={() => {}} />);
    await screen.findAllByText('合成产品手册.md');
    expect(screen.queryByText('合成操作说明_17.md')).toBeNull();
    fireEvent.click(screen.getByTitle('2'));
    await screen.findAllByText('合成操作说明_17.md');
    expect(fetcher.mock.calls.some(([path]) => String(path).endsWith('scope=current&limit=10&offset=10'))).toBe(true);
    fireEvent.click(screen.getByText('已删除记录 · 展开查看与分页'));
    await waitFor(() => expect(fetcher.mock.calls.some(([path]) => String(path).endsWith('scope=deleted&limit=10&offset=0'))).toBe(true));
    expect(fetcher.mock.calls.every(([, init]) => init?.method === 'GET')).toBe(true);
  });
});

function location(design: PreviewDesign, page: PreviewLocation['page'] = 'workbench'): PreviewLocation {
  return { design, page, data: 'sample', phase: 'connected', source: true };
}

describe('complete read-only candidate UI', () => {
  it.each(['b'] as const)('keeps %s preview writes disabled under StrictMode', async (design) => {
    const fetcher = vi.spyOn(globalThis, 'fetch');
    render(<StrictMode><PreviewApp initialLocation={location(design)} /></StrictMode>);
    expect(screen.getByRole<HTMLButtonElement>('button', { name: '发送问题' }).disabled).toBe(true);
    expect(await screen.findByText('合成设计样例，不是实际业务或通话结果。')).toBeTruthy();
    fireEvent.click(screen.getByRole('menuitem', { name: /语音通话/ }));
    expect((await screen.findByRole<HTMLButtonElement>('button', { name: '测试本地音频连接' })).disabled).toBe(true);
    expect(screen.getByRole<HTMLButtonElement>('button', { name: '挂断' }).disabled).toBe(true);
    expect(fetcher).not.toHaveBeenCalled();
  }, 20000); // Full Ant Design trees and jsdom visibility queries exceed Vitest's 5s default.

  it('opens and closes source and impact details without triggering writes or fake downloads', async () => {
    render(<PreviewApp initialLocation={{ ...location('b'), source: false }} />);
    const source = await screen.findByRole('button', { name: /查看来源：合成产品手册/ }, { timeout: 10000 });
    fireEvent.click(source);
    const sourceDialog = await screen.findByRole('dialog');
    expect(within(sourceDialog).getByRole('heading', { name: '原文来源' })).toBeTruthy();
    expect(screen.queryByRole('link', { name: '下载原文' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '关闭来源' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('button', { name: /查看来源：合成产品手册/ })));
    fireEvent.click(screen.getByRole('menuitem', { name: /知识库/ }));
    fireEvent.click(await screen.findByRole('button', { name: '查看重建影响' }));
    expect(within(await screen.findByRole('dialog')).getByText('重建影响说明')).toBeTruthy();
    expect(screen.getByRole<HTMLButtonElement>('button', { name: '确认重建' }).disabled).toBe(true);
  }, 20000);

  it('keeps an actual API failure visible until the user explicitly selects design samples', async () => {
    const fetcher = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('private raw diagnostic'));
    render(<StrictMode><PreviewApp initialLocation={{ ...location('b'), data: 'live' }} /></StrictMode>);
    expect(await screen.findByText(/无法读取现有 API/)).toBeTruthy();
    expect(screen.queryByText('private raw diagnostic')).toBeNull();
    expect(screen.queryByText('合成设计样例，不是实际业务或通话结果。')).toBeNull();
    expect(fetcher.mock.calls.every(([, init]) => init?.method === 'GET')).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '查看设计样例' }));
    expect(await screen.findByText('合成设计样例，不是实际业务或通话结果。')).toBeTruthy();
  });

  it('keeps deleted records and ended tasks folded until requested', async () => {
    render(<PreviewApp initialLocation={location('b', 'knowledge')} />);
    expect(await screen.findByText('当前资料')).toBeTruthy();
    expect(screen.queryByText('合成旧版手册.md')).toBeNull();
    fireEvent.click(screen.getByText(/已删除记录/));
    expect((await screen.findAllByText('合成旧版手册.md')).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole('menuitem', { name: /处理任务/ }));
    expect(await screen.findByText('进行中的任务')).toBeTruthy();
    expect(screen.queryByText('文件包含当前解析器不支持的结构。请转换为普通段落或简单表格后重新上传。')).toBeNull();
    fireEvent.click(screen.getByText(/已结束任务 ·/));
    expect(await screen.findByText('文件包含当前解析器不支持的结构。请转换为普通段落或简单表格后重新上传。')).toBeTruthy();
  });
});
