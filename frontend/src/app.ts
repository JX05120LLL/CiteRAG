import { ApiError } from './api/client';
import type { ApiClient } from './api/client';
import { renderKnowledge } from './pages/knowledge';
import { renderStatus } from './pages/status';
import { renderWorkbench } from './pages/workbench';
import { el } from './shared/dom';
import { renderHeader, renderManagementSidebar, renderSidebar } from './shared/shell';
import type { AppState, Page } from './state';

export async function mountApp(root: HTMLElement, api: ApiClient): Promise<void> {
  const state: AppState = {
    bases: null, chats: null, page: 'workbench', loading: true,
    basesError: null, chatsError: null, health: null, healthError: null, healthLoading: false,
  };
  let generation = 0;
  let healthGeneration = 0;

  async function refresh() {
    const currentGeneration = ++generation;
    Object.assign(state, { loading: true, basesError: null, chatsError: null, bases: null, chats: null });
    render();
    const results = await Promise.allSettled([api.knowledgeBases(), api.conversations()]);
    if (currentGeneration !== generation) return;
    state.bases = results[0].status === 'fulfilled' ? results[0].value : null;
    state.chats = results[1].status === 'fulfilled' ? results[1].value : null;
    state.basesError = results[0].status === 'rejected'
      ? results[0].reason instanceof ApiError ? results[0].reason : new ApiError('http') : null;
    state.chatsError = results[1].status === 'rejected'
      ? results[1].reason instanceof ApiError ? results[1].reason : new ApiError('http') : null;
    state.loading = false;
    render();
  }

  async function loadHealth() {
    const currentGeneration = ++healthGeneration;
    Object.assign(state, { healthLoading: true, healthError: null, health: null });
    render();
    try {
      const health = await api.health();
      if (currentGeneration === healthGeneration) state.health = health;
    } catch (reason) {
      if (currentGeneration === healthGeneration) state.healthError = reason instanceof ApiError ? reason : new ApiError('http');
    } finally {
      if (currentGeneration === healthGeneration) { state.healthLoading = false; render(); }
    }
  }

  function navigate(next: Page) {
    ++healthGeneration;
    state.page = next;
    render();
    if (next === 'status') void loadHealth();
    root.querySelector<HTMLElement>('h1')?.focus();
  }

  function render() {
    const skip = el('a', 'skip-link', '跳到主要内容');
    skip.href = '#main-content';
    const isManagement = state.page !== 'workbench';
    const shell = el('div', `app-shell${isManagement ? ' app-shell-management' : ''}`);
    const main = el('main', `main ${isManagement ? 'management' : 'workbench'}`);
    main.id = 'main-content';
    main.tabIndex = -1;
    if (state.page === 'knowledge') renderKnowledge(main, state, refresh);
    else if (state.page === 'status') renderStatus(main, state, loadHealth);
    else renderWorkbench(main, state, navigate, refresh);
    shell.append(renderHeader(state, navigate));
    if (isManagement) {
      const frame = el('div', 'management-frame');
      frame.append(renderManagementSidebar(state, navigate), main);
      shell.append(frame);
    } else shell.append(renderSidebar(state, navigate, refresh), main);
    root.replaceChildren(skip, shell);
  }

  await refresh();
}
