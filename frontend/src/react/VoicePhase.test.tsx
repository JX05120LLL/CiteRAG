import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import type { VoiceActions } from '../pages/voice';
import type { VoiceState } from '../features/voice/controller';
import { Voice } from './Voice';

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const context = { chatId: 'synthetic-chat', chatTitle: '合成语音状态', kbName: '', kbReady: true, chatPending: false };
function actions(assistantPhase: VoiceState['assistantPhase']): VoiceActions {
  return { capability: { transport: 'configured', assistant: 'configured', purpose: 'voice_assistant' },
    checking: false, capabilityError: false,
    media: { phase: 'connected', assistantPhase, muted: false, outputMuted: false,
      busy: false, remoteAudio: 0, playbackRequired: false, meterUnavailable: false, error: null, answers: [] },
    refresh: vi.fn(async () => {}), connect: vi.fn(), hangup: vi.fn(), microphone: vi.fn(), output: vi.fn(),
    stop: vi.fn(), back: vi.fn(), status: vi.fn() };
}

it.each([
  ['recognizing', '助手正在识别', '正在识别'],
  ['generating', '助手正在生成', '正在生成并核验'],
  ['speaking', '播报音频送出中', '正在发送播报音频'],
  ['waiting_input', '等待补充信息', '任务等待补充，请在页面填写'],
  ['waiting_approval', '等待审批', '任务等待审批，请核对操作后点击'],
] as const)('keeps %s distinct from assistant readiness', (phase, badge, status) => {
  render(<Voice context={context} actions={actions(phase)} />);
  expect(screen.getByText(badge)).toBeTruthy();
  expect(screen.getByRole('status').textContent).toBe(status);
  expect(screen.queryByText('助手就绪')).toBeNull();
  expect(screen.queryByText('助手待就绪')).toBeNull();
});

it('keeps disabled playback separate from microphone input and does not claim speech was heard', () => {
  const control = actions('speaking');
  control.media.outputMuted = true;
  control.media.speechText = '合成回答';
  const view = render(<Voice context={context} actions={control} levels={[0.5]} />);
  expect(screen.getByText('播放已关闭')).toBeTruthy();
  expect(screen.getByText('回响环随麦克风输入变化')).toBeTruthy();
  expect(screen.getByText('已生成文字：')).toBeTruthy();
  expect(view.container.querySelector('.voice-echo')?.getAttribute('data-input')).toBe('measuring');
  expect(screen.queryByText('正在播报：')).toBeNull();
});
