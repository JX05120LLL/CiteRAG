import brandMark from '../../../assets/brand/mark.svg';
import type { AppState, Page } from '../state';
import { action, el, heading, icon } from '../shared/dom';

export function renderVoice(main: HTMLElement, state: AppState, navigate: (page: Page) => void) {
  main.classList.add('voice-page');
  const toolbar = el('div', 'voice-toolbar');
  const back = action('返回聊天', 'button secondary', () => navigate('workbench')); back.prepend(icon('back'));
  const base = state.bases?.find((item) => item.id === state.selectedKbId);
  toolbar.append(back, el('span', 'metadata', state.selectedChatId ? `固定知识库：${base?.name ?? '暂不可用'}` : '尚未选择聊天'));
  const stage = el('section', 'voice-stage');
  const mark = el('img', 'voice-mark'); mark.src = brandMark; mark.alt = ''; mark.width = 88; mark.height = 88;
  stage.append(mark, heading('语音通话'), el('p', 'capability-status', '语音服务尚未接入'),
    el('p', 'voice-explanation', '实时连接、语音识别与播报接口尚未提供。当前不会访问麦克风，也不会创建通话。'));
  const connect = action('连接语音', 'button primary'); connect.disabled = true;
  connect.setAttribute('aria-describedby', 'voice-unavailable');
  stage.append(connect);
  const reason = el('p', 'field-hint', '需接通 LiveKit、ASR 与 TTS 后才能使用。'); reason.id = 'voice-unavailable';
  const controls = el('div', 'voice-controls');
  for (const [name, symbol] of [['静音', 'mic'], ['停止播放', 'stop'], ['字幕', 'file'], ['挂断', 'phone']] as const) {
    const button = action(name, 'voice-control'); button.prepend(icon(symbol)); button.disabled = true;
    button.setAttribute('aria-describedby', 'voice-unavailable'); controls.append(button);
  }
  stage.append(reason, controls, el('p', 'field-hint', '通话接入后固定当前聊天与知识库；发送文字或图片前需挂断。'));
  const status = action('查看系统状态', 'text-button', () => navigate('status'));
  stage.append(status, el('p', 'field-hint', '原始音频默认不保存。'));
  main.append(toolbar, stage);
}
