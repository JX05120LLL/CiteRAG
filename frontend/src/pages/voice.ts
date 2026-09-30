import brandMark from '../../../assets/brand/mark.svg';
import type { VoiceCapability } from '../api/client';
import type { VoiceState } from '../features/voice/controller';
import { action, el, heading, icon } from '../shared/dom';
import '../features/voice/voice.css';

// Native DOM port of LiveKit welcome-view and agent-session-view-01.
// Upstream c5d78a6c381a0ac80b081cf6aeb8ac454d00ca78; vendor/livekit/README.md.
export interface VoiceContext {
  chatId: string | null;
  chatTitle: string;
  kbName: string;
  kbReady: boolean;
  chatPending: boolean;
  error?: string | null;
}
export interface VoiceActions {
  capability: VoiceCapability | null;
  checking: boolean;
  capabilityError: boolean;
  media: VoiceState;
  refresh: () => Promise<void>;
  connect: () => void;
  hangup: () => void;
  microphone: () => void;
  output: () => void;
  stop?: () => void;
  correct?: (text: string) => Promise<void>;
  originalUrl?: (id: string) => string;
  back: () => void | Promise<void>;
  status: () => void | Promise<void>;
}
const failures: Record<string, string> = {
  microphone_denied: '麦克风权限被拒绝。请在浏览器站点权限中允许，再重新连接。',
  microphone_missing: '没有找到麦克风。请连接输入设备后重试。',
  microphone_failed: '麦克风操作失败。请挂断后检查设备与权限。',
  playback_failed: '浏览器未允许播放。请再次点击开启声音，或检查输出设备。',
  disconnected: '音频连接已断开，麦克风已停止。请检查本地 LiveKit 服务后重新连接。',
  connection_failed: '本地音频连接失败。请检查 LiveKit 端口、服务、凭证与浏览器音频权限。',
  voice_transport_disabled: '后端尚未启用本地音频连接测试。',
  voice_transport_not_configured: '后端 LiveKit 配置或 voice 可选依赖未准备好。',
  kb_not_ready: '知识库未就绪，请等待维护完成后再连接。',
  answer_running: '当前回答正在处理中，请结束后再连接。',
};

export function createVoiceView(initialContext: VoiceContext, initialActions: VoiceActions) {
  let actions = initialActions;
  const element = el('section', 'livekit-voice');
  const header = el('header', 'lk-voice-header');
  const brand = el('div', 'lk-voice-brand');
  const logo = el('img'); logo.src = brandMark; logo.alt = ''; logo.width = 28; logo.height = 28;
  brand.append(logo, el('strong', '', 'CiteRAG'), el('span', 'lk-voice-product', '语音通话'));
  const back = action('', 'lk-voice-back', () => { void actions.back(); });
  back.append(icon('back')); const backLabel = el('span'); back.append(backLabel);
  header.append(brand, back);
  const main = el('main', 'lk-voice-main'); main.id = 'main-content';
  const context = el('div', 'lk-voice-context');
  const chat = el('span', 'lk-voice-chat'); const base = el('span', 'lk-voice-base'); base.prepend(icon('book'));
  const baseLabel = el('span'); base.append(baseLabel); context.append(chat, base);
  const stage = el('section', 'lk-voice-stage');
  const welcome = el('div', 'lk-voice-welcome-icon'); welcome.setAttribute('aria-hidden', 'true');
  // Heights follow the five geometric bars in the official WelcomeImage.
  for (const height of [22, 54, 38, 22, 30]) {
    const bar = el('span'); bar.style.height = `${height}px`; welcome.append(bar);
  }
  const visualizer = el('div', 'lk-voice-visualizer'); visualizer.setAttribute('aria-hidden', 'true');
  const bars = Array.from({ length: 5 }, () => {
    const bar = el('span', 'voice-level-bar'); bar.style.setProperty('--level', '0'); visualizer.append(bar); return bar;
  });
  const title = heading('语音通话');
  const status = el('p', 'capability-status'); status.setAttribute('role', 'status');
  const description = el('p', 'lk-voice-description', '测试本地音频连接，语音识别、知识库回答和播报暂不可用。');
  description.id = 'voice-media-purpose';
  const setup = el('p', 'lk-voice-setup');
  const connect = action('测试本地音频连接', 'lk-voice-start', () => actions.connect());
  connect.setAttribute('aria-describedby', 'voice-media-purpose');
  const error = el('p', 'voice-error');
  const levelLabel = el('p', 'lk-voice-level-label');
  stage.append(welcome, visualizer, title, status, description, setup, connect, error, levelLabel);
  const footer = el('footer', 'lk-voice-footer');
  const controls = el('div', 'lk-voice-controls'); controls.setAttribute('aria-label', '通话控制');
  function control(label: string, symbol: Parameters<typeof icon>[0], callback?: () => void) {
    const button = action('', 'lk-voice-control', callback); button.append(icon(symbol));
    const text = el('span', '', label); button.append(text); return { button, text };
  }
  const mic = control('静音', 'mic', () => actions.microphone());
  const output = control('关闭声音', 'stop', () => actions.output());
  const subtitles = control('字幕', 'file'); subtitles.button.disabled = true;
  subtitles.button.title = '语音识别尚未接入，没有可显示的转写';
  subtitles.button.setAttribute('aria-describedby', 'voice-media-purpose');
  const hangup = control('挂断', 'phone', () => actions.hangup()); hangup.button.classList.add('lk-voice-end');
  controls.append(mic.button, output.button, subtitles.button, hangup.button);
  const tracks = el('p', 'lk-voice-track-count');
  const links = el('div', 'lk-voice-links');
  const refresh = action('刷新语音配置', 'lk-voice-link', () => { void actions.refresh(); });
  const system = action('查看系统状态', 'lk-voice-link', () => { void actions.status(); });
  links.append(refresh, system);
  footer.append(controls, tracks, links, el('p', 'lk-voice-privacy', '返回聊天或离开此页会停止麦克风；原始音频不保存。'));
  main.append(context, stage, footer); element.append(header, main);

  function levels(values: readonly number[]) {
    const enabled = actions.media.phase === 'connected' && !actions.media.muted;
    bars.forEach((bar, i) => bar.style.setProperty('--level', String(enabled && Number.isFinite(values[i])
      ? Math.round(Math.min(1, Math.max(0, values[i])) * 1000) / 1000 : 0)));
  }
  function update(current: VoiceContext, next: VoiceActions) {
    actions = next;
    const media = actions.media;
    const active = ['connecting', 'connected', 'reconnecting', 'ending'].includes(media.phase);
    const connected = media.phase === 'connected';
    element.dataset.phase = media.phase;
    backLabel.textContent = active ? '挂断并返回聊天' : '返回聊天'; back.disabled = media.phase === 'ending';
    chat.textContent = current.chatTitle || '尚未选择聊天';
    baseLabel.textContent = current.chatId ? current.kbName ? `固定知识库：${current.kbName}`
      : '普通聊天 · 不检索知识库' : '尚未选择聊天';
    const phases = { idle: '未连接', connecting: '正在连接并请求麦克风权限…', connected: '媒体已连接',
      reconnecting: '媒体重连中…', ending: '正在挂断…', failed: '媒体连接未完成' };
    status.textContent = `${phases[media.phase]} · 语音助手尚未接入`;
    setup.textContent = current.error || (actions.checking ? '正在读取本地通话配置…'
      : actions.capabilityError ? '当前 API 未提供可用的语音配置。请核对后端版本与服务状态。'
      : !current.chatId ? '先返回工作台，创建或打开聊天。'
      : !current.kbReady ? '知识库尚未就绪，请等待维护完成。'
      : actions.capability?.transport === 'configured' ? (active ? '' : '点击连接后才会请求麦克风权限。')
      : actions.capability?.transport === 'not_configured' ? '本地 LiveKit 配置或依赖尚未准备。'
      : '本地音频连接测试默认关闭。');
    connect.hidden = active;
    connect.disabled = active || actions.checking || actions.capability?.transport !== 'configured' ||
      !current.chatId || !current.kbReady || current.chatPending || Boolean(current.error);
    const reason = media.error ? (Object.hasOwn(failures, media.error) ? failures[media.error] : failures.connection_failed) : '';
    error.textContent = reason; error.hidden = !reason;
    if (reason) error.setAttribute('role', 'alert'); else error.removeAttribute('role');
    welcome.hidden = active; visualizer.hidden = !active;
    levelLabel.textContent = connected ? (media.muted ? '麦克风已静音'
      : media.meterUnavailable ? '此浏览器无法显示输入音量，音频连接仍可使用。' : '麦克风输入音量') : '';
    mic.text.textContent = media.muted ? '取消静音' : '静音';
    mic.button.disabled = !connected || media.busy; mic.button.setAttribute('aria-pressed', String(media.muted));
    output.text.textContent = media.outputMuted || media.playbackRequired ? '开启声音' : '关闭声音';
    output.button.disabled = !connected || media.busy; output.button.setAttribute('aria-pressed', String(media.outputMuted));
    hangup.button.disabled = !active || media.phase === 'ending'; controls.hidden = !active;
    tracks.textContent = connected ? `已收到 ${media.remoteAudio} 路远端音频；语音助手尚未接入。` : '';
    if (media.playbackRequired) tracks.textContent += ' 请点击“开启声音”允许播放。';
    refresh.disabled = actions.checking || active; system.disabled = media.phase === 'ending';
    if (!connected || media.muted) levels([]);
  }
  update(initialContext, initialActions);
  return { element, update, levels };
}
