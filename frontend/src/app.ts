import { ApiError } from './api/client';
import type { ApiClient, KnowledgeBase } from './api/client';
import { renderKnowledge } from './pages/knowledge';
import { createDocumentsPanel } from './pages/documents';
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
    selectedKbId: null, selectedChatId: null, chatMessages: [], chatDraft: '',
    chatPending: false, chatError: null, chatRequestKey: null, chatRequestText: null,
    chatMode: 'semantic', exactFilter: {},
  };
  let generation = 0;
  let healthGeneration = 0;
  let chatGeneration = 0;
  let hasMoreChats = false;
  const retryKeys = new Map<string, string>();
  const documentsPanel = createDocumentsPanel(api, render, (bases) => { state.bases = bases; });
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
    const currentChatGeneration = ++chatGeneration;
    Object.assign(state, { loading: true, basesError: null, chatsError: null, bases: null, chats: null });
    state.chatMessages = [];
    render();
    const results = await Promise.allSettled([api.knowledgeBases(), api.conversations()]);
    if (currentGeneration !== generation) return;
    state.bases = results[0].status === 'fulfilled' ? results[0].value : null;
    state.chats = results[1].status === 'fulfilled' ? results[1].value : null;
    hasMoreChats = state.chats?.length === 20;
    state.basesError = results[0].status === 'rejected'
      ? results[0].reason instanceof ApiError ? results[0].reason : new ApiError('http') : null;
    state.chatsError = results[1].status === 'rejected'
      ? results[1].reason instanceof ApiError ? results[1].reason : new ApiError('http') : null;
    state.loading = false;
    if (state.selectedKbId && !state.bases?.some((base) => base.id === state.selectedKbId)) {
      state.selectedKbId = null;
      state.selectedChatId = null;
      state.chatMessages = [];
    }
    render();
    const selectedChatId = state.selectedChatId;
    if (selectedChatId && state.chats?.some((chat) => chat.id === selectedChatId)) {
      try {
        const messages = await api.conversationMessages(selectedChatId);
        if (currentGeneration === generation && currentChatGeneration === chatGeneration &&
            state.selectedChatId === selectedChatId) state.chatMessages = messages;
      } catch (error) {
        if (currentGeneration === generation && currentChatGeneration === chatGeneration &&
            state.selectedChatId === selectedChatId)
          state.chatError = error instanceof ApiError ? error : new ApiError('http');
      }
      if (currentGeneration === generation && currentChatGeneration === chatGeneration) render();
    }
  }

  async function selectChat(id: string) {
    const chat = state.chats?.find((item) => item.id === id);
    if (!chat || state.chatPending) return;
    const currentChatGeneration = ++chatGeneration;
    state.selectedKbId = chat.kb_id;
    state.selectedChatId = chat.id;
    state.chatDraft = '';
    state.chatRequestKey = state.chatRequestText = null;
    state.chatError = null;
    state.chatMessages = [];
    render();
    try {
      const messages = await api.conversationMessages(id);
      if (currentChatGeneration === chatGeneration && state.selectedChatId === id) state.chatMessages = messages;
    } catch (error) {
      if (currentChatGeneration === chatGeneration && state.selectedChatId === id)
        state.chatError = error instanceof ApiError ? error : new ApiError('http');
    }
    if (currentChatGeneration === chatGeneration) render();
  }

  async function loadMoreChats() {
    if (!hasMoreChats || !state.chats || state.chatPending) return;
    const currentGeneration = generation;
    const offset = state.chats.length;
    state.chatPending = true;
    render();
    try {
      const page = await api.conversations(20, offset);
      if (currentGeneration !== generation) return;
      const known = new Set(state.chats.map((item) => item.id));
      state.chats = [...state.chats, ...page.filter((item) => !known.has(item.id))];
      hasMoreChats = page.length === 20;
      state.chatsError = null;
    } catch (error) {
      state.chatsError = error instanceof ApiError ? error : new ApiError('http');
    } finally { state.chatPending = false; render(); }
  }

  async function createChat() {
    if (!state.selectedKbId || state.chatPending) return;
    state.chatPending = true;
    state.chatError = null;
    render();
    try {
      const chat = await api.createConversation(state.selectedKbId);
      state.chats = [chat, ...(state.chats ?? [])];
      state.selectedChatId = chat.id;
      state.chatMessages = [];
      state.chatDraft = '';
      state.chatRequestKey = state.chatRequestText = null;
    } catch (error) {
      state.chatError = error instanceof ApiError ? error : new ApiError('http');
    } finally { state.chatPending = false; render(); }
  }

  async function renameChat() {
    const id = state.selectedChatId;
    const chat = state.chats?.find((item) => item.id === id);
    if (!id || !chat || state.chatPending) return;
    const requested = window.prompt('聊天名称', chat.title);
    if (requested === null) return;
    const title = requested.trim();
    if (!title || title.length > 120) {
      state.chatError = new ApiError('validation'); render(); return;
    }
    state.chatPending = true;
    try {
      const updated = await api.renameConversation(id, title);
      state.chats = (state.chats ?? []).map((item) => item.id === id ? updated : item);
      state.chatError = null;
    } catch (error) {
      state.chatError = error instanceof ApiError ? error : new ApiError('http');
    } finally { state.chatPending = false; render(); }
  }

  async function deleteChat() {
    const id = state.selectedChatId;
    if (!id || state.chatPending || !window.confirm('删除此聊天及其消息和摘要？此操作不可撤销。')) return;
    state.chatPending = true;
    try {
      await api.deleteConversation(id);
      state.chats = (state.chats ?? []).filter((item) => item.id !== id);
      state.selectedChatId = null;
      state.chatMessages = [];
      state.chatError = null;
      state.chatDraft = '';
    } catch (error) {
      state.chatError = error instanceof ApiError ? error : new ApiError('http');
    } finally { state.chatPending = false; render(); }
  }

  async function sendChat() {
    if (!state.selectedChatId || state.chatPending) return;
    const question = state.chatDraft.trim();
    if (!question || question.length > 1000) return;
    const exact = Object.fromEntries(Object.entries(state.exactFilter)
      .map(([key, value]) => [key, value.trim()]).filter(([, value]) => Boolean(value)));
    if (state.chatMode === 'exact' && !['doc_code', 'model_code', 'edition'].some((key) => key in exact)) {
      state.chatError = new ApiError('validation', 'exact_filter'); render(); return;
    }
    if (!state.chatRequestKey || state.chatRequestText !== question) {
      state.chatRequestKey = crypto.randomUUID();
      state.chatRequestText = question;
    }
    state.chatPending = true;
    state.chatError = null;
    render();
    try {
      const message = await api.askMessage(state.selectedChatId, question, state.chatRequestKey,
        state.chatMode, state.chatMode === 'exact' ? exact : undefined);
      state.chatMessages = [...state.chatMessages.filter((item) => item.message_id !== message.message_id), message];
      state.chatDraft = '';
      state.chatRequestKey = null;
      state.chatRequestText = null;
    } catch (error) {
      state.chatError = error instanceof ApiError ? error : new ApiError('http');
      try { state.chatMessages = await api.conversationMessages(state.selectedChatId); }
      catch { /* Keep the original network error visible; an uncertain write may have committed. */ }
      if (state.chatMessages.some((item) => item.client_message_id === state.chatRequestKey)) {
        state.chatDraft = '';
        state.chatRequestKey = null;
        state.chatRequestText = null;
      }
    } finally { state.chatPending = false; render(); }
  }

  async function retryChat(messageId: string) {
    const chatId = state.selectedChatId;
    if (!chatId || state.chatPending ||
        !state.chatMessages.some((item) => item.message_id === messageId &&
          !item.hidden && !item.stale && ['failed', 'interrupted'].includes(item.status))) return;
    const key = retryKeys.get(messageId) ?? crypto.randomUUID();
    retryKeys.set(messageId, key);
    state.chatPending = true;
    state.chatError = null;
    render();
    try {
      const message = await api.retryAnswer(chatId, messageId, key);
      state.chatMessages = state.chatMessages.map((item) => item.message_id === messageId ? message : item);
      retryKeys.delete(messageId);
    } catch (error) {
      state.chatError = error instanceof ApiError ? error : new ApiError('http');
      try {
        state.chatMessages = await api.conversationMessages(chatId);
        if (state.chatMessages.some((item) => item.attempt_id === key)) retryKeys.delete(messageId);
      } catch { /* Retain the key until the committed state is known. */ }
    } finally { state.chatPending = false; render(); }
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
    documentsPanel.close();
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
    const previous = state.page;
    ++healthGeneration;
    state.page = next;
    render();
    if (next === 'status') void loadHealth();
    if (next === 'workbench' && previous !== 'workbench') void refresh();
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
    if (state.page === 'knowledge' && documentsPanel.isOpen) main.append(documentsPanel.render());
    else if (state.page === 'knowledge') renderKnowledge(main, state, { refresh, createKnowledgeBase, beginRename, renameKnowledgeBase, cancelRename, openDocuments: documentsPanel.open });
    else if (state.page === 'status') renderStatus(main, state, loadHealth);
    else renderWorkbench(main, state, navigate, {
      refresh, selectChat, createChat, renameChat, deleteChat, retryChat, sendChat,
      selectKb: (id: string) => { state.selectedKbId = id; state.selectedChatId = null;
        state.chatMessages = []; state.chatError = null; state.chatDraft = '';
        state.chatRequestKey = state.chatRequestText = null; render(); },
      setDraft: (text: string) => { state.chatDraft = text; if (text.trim() !== state.chatRequestText) {
        state.chatRequestKey = null; state.chatRequestText = null;
      } },
      setMode: (mode: 'semantic' | 'exact') => { state.chatMode = mode;
        state.chatRequestKey = state.chatRequestText = null; render(); },
      setExact: (key: 'doc_code' | 'model_code' | 'edition' | 'phrase', value: string) => {
        state.exactFilter[key] = value; state.chatRequestKey = state.chatRequestText = null;
      },
    });
    shell.append(renderHeader(state, navigate));
    if (isManagement) {
      const frame = el('div', 'management-frame');
      frame.append(renderManagementSidebar(state, navigate, openCreate), main);
      shell.append(frame);
    } else shell.append(renderSidebar(state, navigate, refresh, createChat, selectChat,
      hasMoreChats, loadMoreChats), main);
    root.replaceChildren(skip, shell);
  }

  await refresh();
}
