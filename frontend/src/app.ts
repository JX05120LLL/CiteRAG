import { ApiError } from './api/client';
import type { ApiClient, KnowledgeBase } from './api/client';
import { renderKnowledge } from './pages/knowledge';
import { renderStatus } from './pages/status';
import { renderWorkbench } from './pages/workbench';
import { el } from './shared/dom';
import { renderHeader, renderManagementSidebar, renderSidebar } from './shared/shell';
import type { AppState, Page } from './state';
import { isKnowledgePending, knowledgeLimit } from './state';
import { clearPendingCreation, normalizeKnowledgeName, restorePendingCreation, savePendingCreation } from './knowledge-draft';

export async function mountApp(root: HTMLElement, api: ApiClient): Promise<void> {
  const state: AppState = {
    bases: null, chats: null, page: 'workbench', loading: true,
    basesError: null, chatsError: null, health: null, healthError: null, healthLoading: false,
    createDraft: { name: '', pending: false, error: null, requestId: null, requestName: null, uncertain: false },
    renameDraft: null, knowledgeNotice: null,
  };
  let generation = 0;
  let healthGeneration = 0;
  try {
    const recovered = restorePendingCreation();
    if (recovered) Object.assign(state.createDraft, {
      name: recovered.name, requestName: recovered.name, requestId: recovered.key, uncertain: true,
    });
  } catch (reason) {
    state.createDraft.error = reason instanceof ApiError ? reason : new ApiError('local-storage');
  }

  async function refresh() {
    if (isKnowledgePending(state)) return;
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

  function acceptKnowledgeBase(base: KnowledgeBase) {
    const bases = state.bases ?? [];
    state.bases = bases.some((item) => item.id === base.id)
      ? bases.map((item) => item.id === base.id ? base : item) : [...bases, base];
  }

  function focusName(id: string) {
    if (state.page === 'knowledge') root.querySelector<HTMLInputElement>(`#${id}`)?.focus();
  }

  async function createKnowledgeBase() {
    const draft = state.createDraft;
    if (state.loading || state.basesError || isKnowledgePending(state) || draft.error?.code === 'recovery_read_failed' ||
        (!draft.uncertain && (state.bases?.length ?? 0) >= knowledgeLimit)) return;
    const wasUncertain = draft.uncertain;
    try {
      const name = normalizeKnowledgeName(draft.uncertain ? draft.requestName ?? draft.name : draft.name);
      if (!draft.requestId || draft.requestName !== name) {
        draft.requestId = crypto.randomUUID();
        draft.requestName = name;
      }
      state.knowledgeNotice = null;
      savePendingCreation({ name, key: draft.requestId });
      draft.pending = true;
      draft.error = null;
      render();
      const base = await api.createKnowledgeBase(name, draft.requestId);
      acceptKnowledgeBase(base);
      clearPendingCreation({ name, key: draft.requestId });
      Object.assign(draft, { name: '', requestId: null, requestName: null, uncertain: false });
      state.knowledgeNotice = '知识库已创建。当前尚无资料，不可问答。';
    } catch (reason) {
      draft.error = reason instanceof ApiError ? reason : new ApiError('http');
      if (draft.pending) {
        // Refusing a retry cannot prove the earlier request did not commit.
        draft.uncertain = wasUncertain || ['network', 'invalid-response', 'http', 'unavailable', 'local-storage'].includes(draft.error.kind);
        if (!draft.uncertain) {
          try { clearPendingCreation({ name: draft.requestName!, key: draft.requestId! }); }
          catch (cleanupError) {
            draft.uncertain = true;
            draft.error = cleanupError instanceof ApiError ? cleanupError : new ApiError('local-storage');
          }
        }
      }
    } finally {
      draft.pending = false;
      render();
      focusName('kb-create-name');
    }
  }

  function beginRename(base: KnowledgeBase) {
    if (state.loading || isKnowledgePending(state)) return;
    state.renameDraft = { id: base.id, name: base.name, pending: false, error: null };
    state.knowledgeNotice = null;
    render();
    focusName('kb-rename-name');
  }

  async function renameKnowledgeBase() {
    const draft = state.renameDraft;
    if (!draft || state.loading || isKnowledgePending(state)) return;
    try {
      const name = normalizeKnowledgeName(draft.name);
      draft.pending = true;
      draft.error = null;
      state.knowledgeNotice = null;
      render();
      acceptKnowledgeBase(await api.renameKnowledgeBase(draft.id, name));
      state.renameDraft = null;
      state.knowledgeNotice = '知识库名称已更新。';
    } catch (reason) {
      draft.error = reason instanceof ApiError ? reason : new ApiError('http');
    } finally {
      draft.pending = false;
      render();
      focusName('kb-rename-name');
    }
  }

  function cancelRename() {
    if (isKnowledgePending(state)) return;
    state.renameDraft = null;
    render();
  }

  function openCreate() {
    navigate('knowledge');
    focusName('kb-create-name');
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
    if (state.page === 'knowledge') renderKnowledge(main, state, { refresh, createKnowledgeBase, beginRename, renameKnowledgeBase, cancelRename });
    else if (state.page === 'status') renderStatus(main, state, loadHealth);
    else renderWorkbench(main, state, navigate, refresh);
    shell.append(renderHeader(state, navigate));
    if (isManagement) {
      const frame = el('div', 'management-frame');
      frame.append(renderManagementSidebar(state, navigate, openCreate), main);
      shell.append(frame);
    } else shell.append(renderSidebar(state, navigate, refresh), main);
    root.replaceChildren(skip, shell);
  }

  await refresh();
}
