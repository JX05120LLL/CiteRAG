import { ApiError } from './api/client';
import type { ApiClient, KnowledgeBase, VoiceCapability } from './api/client';
import { renderKnowledge } from './pages/knowledge';
import { createDocumentsPanel } from './pages/documents';
import { renderStatus } from './pages/status';
import { renderWorkbench } from './pages/workbench';
import { renderSources } from './pages/sources';
import { createVoiceView } from './pages/voice';
import { VoiceController } from './features/voice/controller';
import type { RoomFactory } from './features/voice/controller';
import { createMediaRoom } from './features/voice/livekit';
import { action, el } from './shared/dom';
import { renderHeader, renderManagementSidebar, renderSidebar } from './shared/shell';
import type { AppState, Page } from './state';
import { isKnowledgePending, knowledgeLimit } from './state';
import { clearPendingCreation, normalizeKnowledgeName, restorePendingCreation, savePendingCreation } from './knowledge-draft';
import type { VoiceActions, VoiceContext } from './pages/voice';

export interface AppView {
  state: AppState; hasMoreChats: boolean; documents: ReturnType<typeof createDocumentsPanel>;
  voice: { context: VoiceContext; actions: VoiceActions; levels: readonly number[] };
  actions: {
    navigate: (page: Page) => void; refresh: () => Promise<void>; loadHealth: () => Promise<void>;
    selectChat: (id: string) => Promise<void>; createChat: () => Promise<void>;
    renameChat: () => Promise<void>; deleteChat: () => Promise<void>; loadMoreChats: () => Promise<void>;
    sendChat: () => Promise<void>; retryChat: (id: string) => Promise<void>; setDraft: (text: string) => void;
    selectKb: (id: string) => void; selectCitation: (messageId: string, evidenceId: string) => void; closeCitation: () => void;
    createKnowledgeBase: () => Promise<void>; beginRename: (id: string) => void;
    renameKnowledgeBase: () => Promise<void>; cancelRename: () => void; setKnowledgeName: (name: string, rename?: boolean) => void;
    openDocuments: (base: KnowledgeBase, tasks?: boolean) => void; closeDocuments: () => void;
  };
}
export interface AppOptions {
  render?: (view: AppView) => void; onDispose?: (dispose: () => void) => void;
  confirm?: (text: string) => Promise<boolean>; prompt?: (title: string, value: string) => Promise<string | null>;
}

export async function mountApp(root: HTMLElement, api: ApiClient, roomFactory: RoomFactory = createMediaRoom,
    initialPage: 'workbench' | 'status' = 'workbench', options: AppOptions = {}): Promise<void> {
  let disposed = false;
  let levels: readonly number[] = [];
  const selectionKey = 'citerag.workbench.selection';
  let restoredSelection: { kbId: string | null; chatId: string | null } = { kbId: null, chatId: null };
  try {
    const stored = window.sessionStorage.getItem(selectionKey);
    if (stored) {
      const value: unknown = JSON.parse(stored);
      if (value && typeof value === 'object' && 'kbId' in value && 'chatId' in value &&
          (value.kbId === null || typeof value.kbId === 'string') &&
          (value.chatId === null || typeof value.chatId === 'string'))
        restoredSelection = value as typeof restoredSelection;
    }
  } catch { /* Selection is convenience state; storage failure must not block the app. */ }
  const state: AppState = {
    bases: null, chats: null, page: initialPage, loading: true,
    basesError: null, chatsError: null, health: null, healthError: null, healthLoading: false,
    healthCheckedAt: undefined,
    createDraft: { name: '', pending: false, error: null, requestId: null, requestName: null, uncertain: false },
    renameDraft: null, knowledgeNotice: null,
    selectedKbId: restoredSelection.kbId, selectedChatId: restoredSelection.chatId, chatMessages: [], chatDraft: '',
    chatStreamText: '', chatStreamSaved: false, chatStreamAttemptId: null,
    chatPending: false, chatError: null, chatRequestKey: null, chatRequestText: null,
  };
  let generation = 0;
  let healthGeneration = 0;
  let chatGeneration = 0;
  let hasMoreChats = false;
  let chatOffset = 0;
  let listedChatIds = new Set<string>();
  const retryKeys = new Map<string, string>();
  let voiceCapability: VoiceCapability | null = null;
  let voiceChecking = false;
  let voiceCapabilityError = false;
  let voiceGeneration = 0;
  let voiceView: ReturnType<typeof createVoiceView> | null = null;
  const voice = new VoiceController(api, () => { if (state.page === 'voice') render(); }, roomFactory,
    (value) => { if (!disposed && state.page === 'voice') { levels = value; if (options.render) render(); else voiceView?.levels(value); } });
  const onPageHide = () => { ++voiceGeneration; voiceChecking = false; void voice.hangup(); };
  window.addEventListener('pagehide', onPageHide);
  options.onDispose?.(() => {
    disposed = true; ++generation; ++chatGeneration; ++healthGeneration; ++voiceGeneration;
    window.removeEventListener('pagehide', onPageHide); documentsPanel.dispose(); void voice.hangup();
  });
  async function loadVoiceCapability() {
    const generation = ++voiceGeneration;
    voiceChecking = true; voiceCapabilityError = false; render();
    try {
      const capability = await api.voiceStatus();
      if (generation === voiceGeneration) voiceCapability = capability;
    } catch {
      if (generation === voiceGeneration) { voiceCapability = null; voiceCapabilityError = true; }
    } finally {
      if (generation === voiceGeneration) { voiceChecking = false; if (state.page === 'voice') render(); }
    }
  }
  function openDocuments(base: KnowledgeBase, focusTasks = false) {
    if (disposed || documentsPanel.snapshot.busy) return;
    void documentsPanel.open(base).then(() => {
      if (state.page === 'knowledge' && documentsPanel.isOpen) {
        const target = root.querySelector<HTMLElement>(focusTasks ? '#persistent-jobs' : 'h1');
        target?.focus();
        if (focusTasks) target?.scrollIntoView?.({ block: 'start' });
      }
    });
  }
  function rememberSelection() {
    try { window.sessionStorage.setItem(selectionKey, JSON.stringify({ kbId: state.selectedKbId, chatId: state.selectedChatId })); }
    catch { /* No persisted selection; backend remains the source of truth. */ }
  }
  const documentsPanel = createDocumentsPanel(api, render, (bases) => { state.bases = bases; }, options.confirm);
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
    chatOffset = state.chats?.length ?? 0;
    listedChatIds = new Set(state.chats?.map((chat) => chat.id) ?? []);
    state.basesError = results[0].status === 'rejected'
      ? results[0].reason instanceof ApiError ? results[0].reason : new ApiError('http') : null;
    state.chatsError = results[1].status === 'rejected'
      ? results[1].reason instanceof ApiError ? results[1].reason : new ApiError('http') : null;
    state.loading = false;
    if (state.selectedKbId && !state.bases?.some((base) => base.id === state.selectedKbId)) {
      state.selectedKbId = null;
      state.selectedChatId = null;
      state.chatMessages = [];
      rememberSelection();
    }
    if (state.selectedChatId && state.chats && !hasMoreChats &&
        !state.chats.some((chat) => chat.id === state.selectedChatId)) {
      state.selectedChatId = null;
      rememberSelection();
    }
    let restoredChat = state.chats?.find((chat) => chat.id === state.selectedChatId);
    const requestedChatId = state.selectedChatId;
    if (!restoredChat && requestedChatId && hasMoreChats) {
      try {
        const older = await api.conversation(requestedChatId);
        if (currentGeneration !== generation || currentChatGeneration !== chatGeneration ||
            state.selectedChatId !== requestedChatId) return;
        state.chats?.unshift(older);
        restoredChat = older;
      } catch (error) {
        if (currentGeneration !== generation || currentChatGeneration !== chatGeneration ||
            state.selectedChatId !== requestedChatId) return;
        if (error instanceof ApiError && error.kind === 'not-found') {
          state.selectedChatId = null;
          rememberSelection();
        } else state.chatError = error instanceof ApiError ? error : new ApiError('http');
      }
    }
    if (restoredChat && state.selectedKbId !== restoredChat.kb_id) {
      state.selectedKbId = restoredChat.kb_id;
      rememberSelection();
    }
    render();
    const selectedChatId = state.selectedChatId;
    if (selectedChatId && state.selectedKbId) {
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
    state.navigationOpen = false;
    state.selectedKbId = chat.kb_id;
    state.selectedChatId = chat.id;
    rememberSelection();
    state.chatDraft = '';
    state.chatRequestKey = state.chatRequestText = null;
    state.chatError = null;
    state.chatMessages = [];
    state.selectedCitation = null;
    render();
    try {
      const messages = await api.conversationMessages(id);
      if (currentChatGeneration === chatGeneration && state.selectedChatId === id) state.chatMessages = messages;
    } catch (error) {
      if (currentChatGeneration === chatGeneration && state.selectedChatId === id)
        state.chatError = error instanceof ApiError ? error : new ApiError('http');
    }
    if (currentChatGeneration === chatGeneration) { render(); root.querySelector<HTMLElement>('h1')?.focus(); }
  }

  async function loadMoreChats() {
    if (!hasMoreChats || !state.chats || state.chatPending) return;
    const currentGeneration = generation;
    const offset = chatOffset;
    state.chatPending = true;
    render();
    try {
      const page = await api.conversations(20, offset);
      if (currentGeneration !== generation) return;
      const known = new Set(state.chats.map((item) => item.id));
      state.chats = [...state.chats, ...page.filter((item) => !known.has(item.id))];
      chatOffset += page.length;
      for (const chat of page) listedChatIds.add(chat.id);
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
      listedChatIds.add(chat.id);
      chatOffset += 1;
      state.selectedChatId = chat.id;
      rememberSelection();
      state.chatMessages = [];
      state.chatDraft = '';
      state.chatRequestKey = state.chatRequestText = null;
    } catch (error) {
      state.chatError = error instanceof ApiError ? error : new ApiError('http');
    } finally {
      state.chatPending = false;
      render();
      if (!state.chatError) root.querySelector<HTMLTextAreaElement>('textarea')?.focus();
    }
  }

  async function renameChat() {
    const id = state.selectedChatId;
    const chat = state.chats?.find((item) => item.id === id);
    if (!id || !chat || state.chatPending) return;
    const requested = options.prompt ? await options.prompt('聊天名称', chat.title) : window.prompt('聊天名称', chat.title);
    if (disposed || state.selectedChatId !== id || state.chatPending) return;
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
    if (!id || state.chatPending) return;
    const warning = '删除此聊天及其消息和摘要？此操作不可撤销。';
    if (!(options.confirm ? await options.confirm(warning) : window.confirm(warning)) ||
        disposed || state.selectedChatId !== id || state.chatPending) return;
    state.chatPending = true;
    try {
      await api.deleteConversation(id);
      state.chats = (state.chats ?? []).filter((item) => item.id !== id);
      if (listedChatIds.delete(id)) chatOffset = Math.max(0, chatOffset - 1);
      state.selectedChatId = null;
      rememberSelection();
      state.chatMessages = [];
      state.chatError = null;
      state.chatDraft = '';
    } catch (error) {
      state.chatError = error instanceof ApiError ? error : new ApiError('http');
    } finally { state.chatPending = false; render(); }
  }

  async function sendChat() {
    if (!state.selectedChatId || state.chatPending ||
        state.chatMessages.some((item) => item.status === 'running')) return;
    const question = state.chatDraft.trim();
    if (!question || question.length > 1000) return;
    if (!state.chatRequestKey || state.chatRequestText !== question) {
      state.chatRequestKey = crypto.randomUUID();
      state.chatRequestText = question;
    }
    state.chatPending = true;
    state.chatError = null;
    render();
    try {
      const message = await api.askMessageStream(state.selectedChatId, question, state.chatRequestKey,
        'auto', undefined, (progress) => {
          if (progress.type === 'accepted') {
            state.chatMessages = [...state.chatMessages.filter((item) =>
              item.message_id !== progress.message.message_id), progress.message];
            state.chatStreamAttemptId = progress.message.attempt_id;
            state.chatStreamText = '';
            state.chatStreamSaved = false;
          } else if (state.chatStreamAttemptId) {
            state.chatStreamText = (state.chatStreamText ?? '') + progress.text;
            state.chatStreamSaved = progress.saved;
          }
          render();
        });
      state.chatMessages = [...state.chatMessages.filter((item) => item.message_id !== message.message_id), message];
      state.chatDraft = '';
      state.chatRequestKey = null;
      state.chatRequestText = null;
    } catch (error) {
      state.chatError = error instanceof ApiError ? error : new ApiError('http');
      try { state.chatMessages = await api.conversationMessages(state.selectedChatId); }
      catch { /* Keep the original network error visible; an uncertain write may have committed. */ }
      const savedInput = state.chatMessages.find((item) =>
        item.client_message_id === state.chatRequestKey);
      if (savedInput) {
        if (savedInput.status === 'running')
          void followSavedAnswer(state.selectedChatId, savedInput.message_id, chatGeneration);
        state.chatDraft = '';
        state.chatRequestKey = null;
        state.chatRequestText = null;
      }
    } finally {
      state.chatStreamText = '';
      state.chatStreamSaved = false;
      state.chatStreamAttemptId = null;
      state.chatPending = false;
      render();
    }
  }

  async function followSavedAnswer(chatId: string, messageId: string, expectedGeneration: number) {
    for (let attempt = 0; attempt < 30; attempt++) {
      await new Promise<void>((resolve) => { window.setTimeout(resolve, 1000); });
      if (expectedGeneration !== chatGeneration || state.selectedChatId !== chatId) return;
      try {
        const messages = await api.conversationMessages(chatId);
        if (expectedGeneration !== chatGeneration || state.selectedChatId !== chatId) return;
        state.chatMessages = messages;
        if (messages.some((item) => item.message_id === messageId && item.status !== 'running')) {
          state.chatError = null;
          render();
          return;
        }
        render();
      } catch { /* Keep the saved question visible and retry a bounded read. */ }
    }
  }

  async function retryChat(messageId: string) {
    const chatId = state.selectedChatId;
    if (!chatId || state.chatPending ||
        state.chatMessages.some((item) => item.status === 'running') ||
        !state.chatMessages.some((item) => item.message_id === messageId &&
          !item.hidden && !item.stale && ['failed', 'interrupted', 'partial'].includes(item.status))) return;
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
      if (currentGeneration === healthGeneration) {
        state.health = health;
        state.healthCheckedAt = Date.now();
      }
    } catch (reason) {
      if (currentGeneration === healthGeneration) state.healthError = reason instanceof ApiError ? reason : new ApiError('http');
    } finally {
      if (currentGeneration === healthGeneration) { state.healthLoading = false; render(); }
    }
  }

  function navigate(next: Page) {
    if (disposed) return;
    const previous = state.page;
    ++healthGeneration;
    state.page = next;
    if (previous === 'voice' && next !== 'voice') {
      ++voiceGeneration; voiceChecking = false; voiceView = null; void voice.hangup();
    }
    state.navigationOpen = false;
    state.selectedCitation = null;
    render();
    if (next === 'status') void loadHealth();
    if (next === 'voice') void loadVoiceCapability();
    if (next === 'workbench' && previous !== 'workbench' && !options.render) void refresh();
    root.querySelector<HTMLElement>('h1')?.focus();
  }

  function render() {
    if (disposed) return;
    if (options.render) {
      const base = state.bases?.find((item) => item.id === state.selectedKbId);
      options.render({ state: { ...state, createDraft: { ...state.createDraft }, renameDraft: state.renameDraft ? { ...state.renameDraft } : null },
        hasMoreChats, documents: documentsPanel,
        voice: { context: { chatId: state.selectedChatId,
          chatTitle: state.chats?.find((item) => item.id === state.selectedChatId)?.title ?? '',
          kbName: base?.name ?? '', kbReady: base?.status === 'ready', chatPending: state.chatPending || state.chatMessages.some((item) => item.status === 'running') },
          levels, actions: { capability: voiceCapability, checking: voiceChecking, capabilityError: voiceCapabilityError,
            media: { ...voice.state }, refresh: loadVoiceCapability,
            connect: () => { if (!disposed && state.page === 'voice' && !voiceChecking && state.selectedChatId && base?.status === 'ready' && !state.chatPending &&
              !state.chatMessages.some((item) => item.status === 'running') && voiceCapability?.transport === 'configured') void voice.connect(state.selectedChatId); },
            hangup: () => { void voice.hangup(); }, microphone: () => { void voice.toggleMicrophone(); }, output: () => { void voice.toggleOutput(); },
            back: async () => { await voice.hangup(); navigate('workbench'); }, status: async () => { await voice.hangup(); navigate('status'); } } },
        actions: { navigate, refresh, loadHealth, selectChat, createChat, renameChat, deleteChat, loadMoreChats, sendChat, retryChat,
          selectKb: (id) => { if (disposed || state.chatPending || documentsPanel.snapshot.busy || !state.bases?.some((item) => item.id === id)) return;
            ++chatGeneration; state.selectedKbId = id; state.selectedChatId = null; rememberSelection();
            state.chatMessages = []; state.chatError = null; state.chatDraft = ''; state.selectedCitation = null; state.chatRequestKey = state.chatRequestText = null; render(); },
          setDraft: (text) => { state.chatDraft = text; if (text.trim() !== state.chatRequestText) state.chatRequestKey = state.chatRequestText = null; render(); },
          selectCitation: (messageId, evidenceId) => { state.selectedCitation = { messageId, evidenceId }; render(); },
          closeCitation: () => { state.selectedCitation = null; render(); }, createKnowledgeBase,
          beginRename: (id) => { const base = state.bases?.find((item) => item.id === id); if (base) beginRename(base); }, renameKnowledgeBase, cancelRename,
          setKnowledgeName: (name, rename = false) => { const draft = rename ? state.renameDraft : state.createDraft; if (draft && !draft.pending) { draft.name = name; render(); } },
          openDocuments, closeDocuments: () => { documentsPanel.close(); render(); } } });
      return;
    }
    if (state.page === 'voice') {
      const base = state.bases?.find((item) => item.id === state.selectedKbId);
      const context = { chatId: state.selectedChatId,
        chatTitle: state.chats?.find((item) => item.id === state.selectedChatId)?.title ?? '',
        kbName: base?.name ?? '', kbReady: base?.status === 'ready', chatPending: state.chatPending };
      const actions = { capability: voiceCapability, checking: voiceChecking, capabilityError: voiceCapabilityError,
        media: voice.state, refresh: loadVoiceCapability,
        connect: () => { if (context.chatId && !context.chatPending && context.kbReady &&
          voiceCapability?.transport === 'configured') void voice.connect(context.chatId); },
        hangup: () => { void voice.hangup(); }, microphone: () => { void voice.toggleMicrophone(); },
        output: () => { void voice.toggleOutput(); },
        back: async () => { await voice.hangup(); navigate('workbench'); },
        status: async () => { await voice.hangup(); navigate('status'); } };
      voiceView ??= createVoiceView(context, actions);
      voiceView.update(context, actions);
      if (root.firstChild !== voiceView.element) root.replaceChildren(voiceView.element);
      return;
    }
    const skip = el('a', 'skip-link', '跳到主要内容');
    skip.href = '#main-content';
    const isManagement = state.page === 'knowledge' || state.page === 'status';
    const shell = el('div', `app-shell${isManagement ? ' app-shell-management' : ''}${state.navigationOpen ? ' navigation-open' : ''}`);
    const main = el('main', `main ${isManagement ? 'management' : 'workbench'}`);
    main.id = 'main-content';
    main.tabIndex = -1;
    if (state.page === 'knowledge' && documentsPanel.isOpen) main.append(documentsPanel.render());
    else if (state.page === 'knowledge') renderKnowledge(main, state, { refresh, createKnowledgeBase, beginRename, renameKnowledgeBase, cancelRename, openDocuments });
    else if (state.page === 'status') renderStatus(main, state, loadHealth);
    else renderWorkbench(main, state, navigate, {
      refresh, selectChat, createChat, renameChat, deleteChat, retryChat, sendChat,
      selectKb: (id: string) => { state.selectedKbId = id; state.selectedChatId = null;
        rememberSelection();
        state.chatMessages = []; state.chatError = null; state.chatDraft = '';
        state.chatRequestKey = state.chatRequestText = null; render(); },
      setDraft: (text: string) => { state.chatDraft = text; if (text.trim() !== state.chatRequestText) {
        state.chatRequestKey = null; state.chatRequestText = null;
      } },
      selectCitation: (messageId, evidenceId) => {
        state.selectedCitation = { messageId, evidenceId }; render();
        root.querySelector<HTMLElement>('.source-inspector button')?.focus();
      },
    });
    if (state.page === 'workbench' && state.selectedCitation) {
      const message = state.chatMessages.find((item) => item.message_id === state.selectedCitation?.messageId &&
        item.status === 'answered' && !item.hidden);
      const citation = message?.citations.find((item) => item.evidence_id === state.selectedCitation?.evidenceId);
      if (message && citation && state.bases?.find((base) => base.id === state.selectedKbId)?.status === 'ready') {
        main.classList.add('has-inspector');
        main.append(renderSources(message.citations, citation, api.originalUrl, (next) => {
          state.selectedCitation = { messageId: message.message_id, evidenceId: next.evidence_id }; render();
          root.querySelector<HTMLElement>('.source-inspector button')?.focus();
        }, () => { state.selectedCitation = null; render(); root.querySelector<HTMLElement>('.citation-trigger')?.focus(); }));
      } else state.selectedCitation = null;
    }
    const toggleNavigation = () => { state.navigationOpen = !state.navigationOpen; render(); };
    shell.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && state.navigationOpen) {
        state.navigationOpen = false; render(); root.querySelector<HTMLElement>('.navigation-toggle')?.focus();
      }
    });
    shell.append(renderHeader(state, navigate, toggleNavigation));
    if (state.navigationOpen) {
      const dismiss = action('', 'navigation-backdrop', toggleNavigation); dismiss.setAttribute('aria-label', '关闭导航');
      shell.append(dismiss);
    }
    if (isManagement) {
      const frame = el('div', 'management-frame');
      frame.append(renderManagementSidebar(state, navigate, openCreate, openDocuments, documentsPanel.currentBaseId), main);
      shell.append(frame);
    } else shell.append(renderSidebar(state, navigate, refresh, createChat, selectChat,
      hasMoreChats, loadMoreChats, openDocuments), main);
    root.replaceChildren(skip, shell);
  }

  await refresh();
  if (initialPage === 'status') await loadHealth();
}
