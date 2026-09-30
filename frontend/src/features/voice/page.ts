import type { ApiClient, Conversation, VoiceCapability } from '../../api/client';
import { createVoiceView } from '../../pages/voice';
import type { VoiceContext, VoiceActions } from '../../pages/voice';
import { VoiceController } from './controller';
import type { RoomFactory } from './controller';
import { createMediaRoom } from './livekit';

type Navigation = (page: 'workbench' | 'status', chat?: Conversation) => void;
export interface VoicePageOptions {
  render: (view: { context: VoiceContext; actions: VoiceActions; levels: readonly number[] }) => void;
  onDispose: (dispose: () => void) => void;
}

export async function mountVoicePage(root: HTMLElement, api: ApiClient, conversationId: string | null,
    factory: RoomFactory = createMediaRoom, navigate: Navigation = (page, chat) => {
      if (chat) {
        try { sessionStorage.setItem('citerag.workbench.selection', JSON.stringify({ kbId: chat.kb_id, chatId: chat.id })); }
        catch { /* Navigation works without convenience storage. */ }
      }
      window.location.assign(page === 'status' ? './index.html?view=status' : './index.html');
    }, options?: VoicePageOptions) {
  let disposed = false;
  let hidden = false;
  let leaving = false;
  let generation = 0;
  let checking = false;
  let capabilityError = false;
  let capability: VoiceCapability | null = null;
  let ownedChat: Conversation | undefined;
  let levels: readonly number[] = [];
  let context: VoiceContext = { chatId: null, chatTitle: '', kbName: '', kbReady: false, chatPending: false };
  const voice = new VoiceController(api, () => { if (!disposed && !hidden) update(); }, factory,
    (value) => { if (!disposed && !hidden) { levels = value; if (options) update(); else view?.levels(value); } });
  async function leave(page: 'workbench' | 'status') {
    if (disposed || hidden || leaving) return;
    leaving = true;
    await voice.hangup();
    if (voice.state.error === 'voice_cleanup_failed') { leaving = false; update(); return; }
    if (!disposed) navigate(page, ownedChat);
  }
  const actions = () => ({ capability, checking, capabilityError, media: voice.state, refresh: load,
    connect: () => { if (!disposed && !hidden && !leaving && !checking && context.chatId && context.kbReady &&
      !context.error && capability?.transport === 'configured') void voice.connect(context.chatId, capability.purpose === 'voice_assistant'); },
    hangup: () => { void voice.hangup(); }, microphone: () => { void voice.toggleMicrophone(); },
    output: () => { void voice.toggleOutput(); }, stop: () => { void voice.stopAnswer(); },
    correct: (text: string) => voice.correctTranscript(text),
    originalUrl: api.originalUrl,
    back: () => leave('workbench'), status: () => leave('status') });
  const view = options ? null : createVoiceView(context, actions());
  if (view) root.replaceChildren(view.element);
  function update() {
    if (disposed || hidden) return;
    if (options) options.render({ context: { ...context }, actions: { ...actions(), media: { ...voice.state } }, levels });
    else view?.update(context, actions());
  }
  async function load() {
    if (disposed || hidden || leaving || !['idle', 'failed'].includes(voice.state.phase)) return;
    const current = ++generation;
    checking = true; capabilityError = false; update();
    const [config, chat] = await Promise.allSettled([
      api.voiceStatus(), conversationId ? api.conversation(conversationId) : Promise.resolve(null),
    ]);
    if (disposed || hidden || current !== generation) return;
    capability = config.status === 'fulfilled' ? config.value : null;
    capabilityError = config.status === 'rejected';
    ownedChat = chat.status === 'fulfilled' && chat.value?.id === conversationId ? chat.value : undefined;
    const bases = ownedChat?.kb_id ? await Promise.allSettled([api.knowledgeBases()]) : [];
    if (disposed || hidden || current !== generation) return;
    const base = bases[0]?.status === 'fulfilled' ? bases[0].value.find((item) => item.id === ownedChat?.kb_id) : undefined;
    context = { chatId: ownedChat?.id ?? null, chatTitle: ownedChat?.title ?? '', kbName: base?.name ?? '',
      kbReady: ownedChat?.kb_id === null || base?.status === 'ready', chatPending: false,
      error: conversationId && !ownedChat ? '当前聊天不可访问，请返回工作台重新选择。'
        : ownedChat && ownedChat.kb_id !== null && !base ? '无法读取此聊天的知识库，请返回工作台核对。' : null };
    checking = false; update();
    if (ownedChat) void voice.readHistory(ownedChat.id);
  }
  const dispose = async () => {
    if (disposed) return;
    disposed = true; ++generation;
    window.removeEventListener('pagehide', onPageHide);
    window.removeEventListener('pageshow', onPageShow);
    await voice.hangup();
  };
  const onPageHide = () => {
    hidden = true; ++generation; checking = false;
    void voice.hangup();
  };
  const onPageShow = () => {
    if (disposed || !hidden) return;
    hidden = false; leaving = false;
    void voice.hangup().then(() => { if (!disposed && !hidden) void load(); });
  };
  window.addEventListener('pagehide', onPageHide);
  window.addEventListener('pageshow', onPageShow);
  options?.onDispose(() => { void dispose(); });
  await load();
  return { dispose };
}
