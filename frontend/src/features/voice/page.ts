import type { ApiClient, Conversation, VoiceCapability } from '../../api/client';
import { createVoiceView } from '../../pages/voice';
import type { VoiceContext } from '../../pages/voice';
import { VoiceController } from './controller';
import type { RoomFactory } from './controller';
import { createMediaRoom } from './livekit';

type Navigation = (page: 'workbench' | 'status', chat?: Conversation) => void;

export async function mountVoicePage(root: HTMLElement, api: ApiClient, conversationId: string | null,
    factory: RoomFactory = createMediaRoom, navigate: Navigation = (page, chat) => {
      if (chat) {
        try { sessionStorage.setItem('citerag.workbench.selection', JSON.stringify({ kbId: chat.kb_id, chatId: chat.id })); }
        catch { /* Navigation works without convenience storage. */ }
      }
      window.location.assign(page === 'status' ? './index.html?view=status' : './index.html');
    }) {
  let disposed = false;
  let hidden = false;
  let leaving = false;
  let generation = 0;
  let checking = false;
  let capabilityError = false;
  let capability: VoiceCapability | null = null;
  let ownedChat: Conversation | undefined;
  let context: VoiceContext = { chatId: null, chatTitle: '', kbName: '', kbReady: false, chatPending: false };
  const voice = new VoiceController(api, () => { if (!disposed && !hidden) update(); }, factory,
    (levels) => { if (!disposed && !hidden) view.levels(levels); });
  async function leave(page: 'workbench' | 'status') {
    if (disposed || hidden || leaving) return;
    leaving = true;
    await voice.hangup();
    if (!disposed) navigate(page, ownedChat);
  }
  const actions = () => ({ capability, checking, capabilityError, media: voice.state, refresh: load,
    connect: () => { if (!disposed && !hidden && !leaving && !checking && context.chatId && context.kbReady &&
      !context.error && capability?.transport === 'configured') void voice.connect(context.chatId); },
    hangup: () => { void voice.hangup(); }, microphone: () => { void voice.toggleMicrophone(); },
    output: () => { void voice.toggleOutput(); }, back: () => leave('workbench'), status: () => leave('status') });
  const view = createVoiceView(context, actions()); root.replaceChildren(view.element);
  function update() { view.update(context, actions()); }
  async function load() {
    if (disposed || hidden || leaving || !['idle', 'failed'].includes(voice.state.phase)) return;
    const current = ++generation;
    checking = true; capabilityError = false; update();
    const [config, chat, bases] = await Promise.allSettled([
      api.voiceStatus(), conversationId ? api.conversation(conversationId) : Promise.resolve(null),
      conversationId ? api.knowledgeBases() : Promise.resolve([]),
    ]);
    if (disposed || hidden || current !== generation) return;
    capability = config.status === 'fulfilled' ? config.value : null;
    capabilityError = config.status === 'rejected';
    ownedChat = chat.status === 'fulfilled' && chat.value?.id === conversationId ? chat.value : undefined;
    const base = bases.status === 'fulfilled' ? bases.value.find((item) => item.id === ownedChat?.kb_id) : undefined;
    context = { chatId: ownedChat?.id ?? null, chatTitle: ownedChat?.title ?? '', kbName: base?.name ?? '',
      kbReady: base?.status === 'ready', chatPending: false,
      error: conversationId && !ownedChat ? '当前聊天不可访问，请返回工作台重新选择。'
        : ownedChat && !base ? '无法读取此聊天的知识库，请返回工作台核对。' : null };
    checking = false; update();
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
  await load();
  return { dispose };
}
