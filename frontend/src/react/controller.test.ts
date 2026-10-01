import { afterEach, expect, it, vi } from 'vitest';
import { mountApp, type AppView } from '../app';
import { createApi } from '../api/client';
import { createDocumentsPanel } from '../pages/documents';
import { sampleBases } from '../preview/samples';

const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } });
afterEach(() => sessionStorage.clear());

it('publishes one business state to React, does not mount native UI or start media', async () => {
  const root = document.createElement('div');
  const views: AppView[] = [];
  const calls: string[] = [];
  let dispose = () => {};
  await mountApp(root, createApi(async (url) => {
    calls.push(String(url)); return json({ items: [] });
  }), undefined, 'workbench', { render: (view) => views.push(view), onDispose: (fn) => { dispose = fn; } });
  expect(root.childNodes).toHaveLength(0);
  expect(views.at(-1)?.state.loading).toBe(false);
  views.at(-1)!.actions.setDraft('待发送');
  expect(views.at(-1)?.state.chatDraft).toBe('待发送');
  expect(calls).toEqual(['/api/knowledge-bases', '/api/conversations?limit=20&offset=0', '/api/agent/capability']);
  dispose();
});

it('registers disposal before initial requests resolve and ignores their late result', async () => {
  let resolve!: (response: Response) => void;
  const late = new Promise<Response>((done) => { resolve = done; });
  const render = vi.fn();
  let dispose = () => {};
  const mounted = mountApp(document.createElement('div'), createApi(() => late), undefined, 'workbench',
    { render, onDispose: (fn) => { dispose = fn; } });
  dispose(); const count = render.mock.calls.length;
  expect(count).toBeGreaterThan(0);
  resolve(json({ items: [] })); await mounted;
  expect(render).toHaveBeenCalledTimes(count);
});

it('closing a detail does not invalidate a pending collection refresh', async () => {
  let delayed = false;
  let release!: (response: Response) => void;
  const api = createApi(async (input) => {
    const path = String(input);
    if (path === '/api/knowledge-bases') return delayed ? await new Promise<Response>((resolve) => { release = resolve; }) : json({ items: sampleBases });
    if (path.includes('/jobs?')) return json({ items: [], total: 0, active_items: [], failed_count: 0 });
    return json({ items: [], total: 0, counts: { ready: 0, failed: 0, deleted: 0 } });
  });
  const panel = createDocumentsPanel(api, () => {}, () => {});
  await panel.open(sampleBases[0]); delayed = true;
  const refreshed = panel.refresh(); expect(panel.snapshot.loading).toBe(true);
  panel.closeInspector(); release(json({ items: sampleBases })); await refreshed;
  expect(panel.snapshot.loading).toBe(false);
  expect(panel.snapshot.unavailable).toBe(false);
});
