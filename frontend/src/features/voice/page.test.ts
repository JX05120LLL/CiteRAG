import { afterEach, describe, expect, it, vi } from 'vitest';
import { createApi } from '../../api/client';
import { mountVoicePage } from './page';
import type { MediaRoom, RoomEvents } from './controller';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const chat = { id: 'chat', owner_id: 'synthetic-local', kb_id: 'kb', title: '合成独立通话', created_at: '2026-09-26T00:00:00Z' };
const capability = { transport: 'configured', assistant: 'not_configured', purpose: 'media_test' };
const find = (root: HTMLElement, text: string) => [...root.querySelectorAll('button')].find((b) => b.textContent?.includes(text))!;
const cleanups: Array<() => Promise<void>> = [];
afterEach(async () => { for (const cleanup of cleanups.splice(0)) await cleanup(); document.body.replaceChildren(); });

async function fixture(options: { id?: string | null; ready?: boolean; unowned?: boolean } = {}) {
  const root = document.createElement('div'); document.body.append(root);
  const calls: string[] = [];
  let events!: RoomEvents;
  const room: MediaRoom = { connect: vi.fn(async () => {}), microphone: vi.fn(async () => {}),
    output: vi.fn(async () => {}), disconnect: vi.fn(async () => {}) };
  const navigate = vi.fn();
  const api = createApi(async (url) => {
    const path = String(url); calls.push(path);
    if (path === '/api/voice/status') return json(capability);
    if (path === '/api/conversations/chat') return options.unowned ? json({ detail: { code: 'conversation_not_found' } }, 404) : json(chat);
    if (path === '/api/knowledge-bases') return json({ items: [{ id: 'kb', name: '合成库', status: options.ready === false ? 'blocked' : 'ready' }] });
    if (path.endsWith('/voice/token')) return json({ server_url: 'ws://127.0.0.1:7880', token: 'synthetic', room: 'synthetic',
      conversation_id: 'chat', assistant: 'not_configured', purpose: 'media_test' });
    throw new Error('Unexpected synthetic request');
  });
  const page = await mountVoicePage(root, api, options.id === undefined ? 'chat' : options.id,
    async (callbacks) => { events = callbacks; return room; }, navigate);
  cleanups.push(page.dispose);
  return { root, room, calls, navigate, event: () => events };
}

describe('standalone native LiveKit voice page', () => {
  it('loads the owned chat and ready knowledge base without capturing or issuing a token', async () => {
    const { root, calls, room } = await fixture();
    expect(root.textContent).toContain('合成独立通话');
    expect(root.textContent).toContain('固定知识库：合成库');
    expect(find(root, '测试本地音频连接').disabled).toBe(false);
    expect(find(root, '字幕').disabled).toBe(true);
    expect(calls).toEqual(expect.arrayContaining(['/api/voice/status', '/api/conversations/chat', '/api/knowledge-bases']));
    expect(calls.some((path) => path.endsWith('/token'))).toBe(false);
    expect(room.microphone).not.toHaveBeenCalled();
  });

  it.each([{ id: null }, { ready: false }, { unowned: true }])('keeps unusable chat context disabled: %j', async (options) => {
    const { root, calls } = await fixture(options);
    expect(find(root, '测试本地音频连接').disabled).toBe(true);
    expect(calls.some((path) => path.endsWith('/token'))).toBe(false);
    expect(root.textContent).not.toContain('private infrastructure');
  });

  it('updates real input levels without recreating the controls or losing focus', async () => {
    const { root, event } = await fixture();
    find(root, '测试本地音频连接').click();
    await vi.waitFor(() => expect(root.textContent).toContain('媒体已连接'));
    const microphone = find(root, '静音'); microphone.focus();
    event().audioLevels?.([0.2, 0.4, 0.6, 0.8, 1]);
    expect(document.activeElement).toBe(microphone);
    expect(root.querySelectorAll('.voice-level-bar')).toHaveLength(5);
    expect(root.querySelectorAll<HTMLElement>('.voice-level-bar')[4].style.getPropertyValue('--level')).toBe('1');
    microphone.click();
    await vi.waitFor(() => expect(find(root, '取消静音')).toBe(microphone));
    expect([...root.querySelectorAll<HTMLElement>('.voice-level-bar')].every((bar) => bar.style.getPropertyValue('--level') === '0')).toBe(true);
  });

  it('waits for actual media cleanup before navigating back', async () => {
    const { root, room, navigate } = await fixture();
    find(root, '测试本地音频连接').click();
    await vi.waitFor(() => expect(root.textContent).toContain('媒体已连接'));
    let release!: () => void;
    vi.mocked(room.disconnect).mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
    find(root, '挂断并返回聊天').click();
    await vi.waitFor(() => expect(room.disconnect).toHaveBeenCalled());
    expect(navigate).not.toHaveBeenCalled();
    release();
    await vi.waitFor(() => expect(navigate).toHaveBeenCalledWith('workbench', chat));
  });

  it('shows a safe permission failure and cleans up a connected room', async () => {
    const { root, room } = await fixture();
    vi.mocked(room.microphone).mockRejectedValue(new DOMException('private infrastructure', 'NotAllowedError'));
    find(root, '测试本地音频连接').click();
    await vi.waitFor(() => expect(root.querySelector('[role=alert]')?.textContent).toContain('麦克风权限被拒绝'));
    expect(root.textContent).not.toContain('private infrastructure');
    expect(room.disconnect).toHaveBeenCalled();
  });

  it('stops media on pagehide and revalidates context when the browser restores the page', async () => {
    const { root, room, calls } = await fixture();
    find(root, '测试本地音频连接').click();
    await vi.waitFor(() => expect(root.textContent).toContain('媒体已连接'));
    window.dispatchEvent(new Event('pagehide'));
    await vi.waitFor(() => expect(room.disconnect).toHaveBeenCalled());
    window.dispatchEvent(new Event('pageshow'));
    await vi.waitFor(() => expect(find(root, '测试本地音频连接').disabled).toBe(false));
    expect(calls.filter((path) => path === '/api/conversations/chat')).toHaveLength(2);
    find(root, '测试本地音频连接').click();
    await vi.waitFor(() => expect(room.connect).toHaveBeenCalledTimes(2));
  });
});
