import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import type { EchoState } from './voiceVisualModel';
import VoiceThreeEcho from './VoiceThreeEcho';

const renderer = vi.hoisted(() => vi.fn(() => null));
vi.mock('@react-three/fiber', () => ({ Canvas: renderer, useThree: vi.fn() }));
afterEach(() => { cleanup(); renderer.mockClear(); });

const echo: EchoState = { tone: 'listening', levels: [0, 0, 0, 0, 0], input: 'measuring', output: 'enabled' };

it('keeps repeated microphone samples out of the renderer while responding to new audio and phase', () => {
  const onUnavailable = vi.fn();
  const onFirstFrame = vi.fn();
  const view = render(<VoiceThreeEcho echo={echo} onUnavailable={onUnavailable} onFirstFrame={onFirstFrame} />);
  expect(renderer).toHaveBeenCalledTimes(1);
  // Each frame creates new state/arrays, but silence is still the same scene.
  for (let frame = 0; frame < 12; frame++) {
    view.rerender(<VoiceThreeEcho echo={{ ...echo, levels: [0, 0, 0, 0, 0] }} onUnavailable={onUnavailable} onFirstFrame={onFirstFrame} />);
  }
  expect(renderer).toHaveBeenCalledTimes(1);
  // Output settings do not change the microphone geometry.
  view.rerender(<VoiceThreeEcho echo={{ ...echo, output: 'blocked', levels: [0, 0, 0, 0, 0] }} onUnavailable={onUnavailable} onFirstFrame={onFirstFrame} />);
  expect(renderer).toHaveBeenCalledTimes(1);
  view.rerender(<VoiceThreeEcho echo={{ ...echo, levels: [0.4, 0, 0, 0, 0] }} onUnavailable={onUnavailable} onFirstFrame={onFirstFrame} />);
  expect(renderer).toHaveBeenCalledTimes(2);
  view.rerender(<VoiceThreeEcho echo={{ ...echo, tone: 'working', levels: [0.4, 0, 0, 0, 0] }} onUnavailable={onUnavailable} onFirstFrame={onFirstFrame} />);
  expect(renderer).toHaveBeenCalledTimes(3);
});

it('updates the renderer when its context recovery callback changes', () => {
  const onFirstFrame = vi.fn();
  const view = render(<VoiceThreeEcho echo={echo} onUnavailable={vi.fn()} onFirstFrame={onFirstFrame} />);
  view.rerender(<VoiceThreeEcho echo={echo} onUnavailable={vi.fn()} onFirstFrame={onFirstFrame} />);
  expect(renderer).toHaveBeenCalledTimes(2);
});
