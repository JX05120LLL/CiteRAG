import { StrictMode } from 'react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import VoiceThreeEcho from './VoiceThreeEcho';
import type { EchoState } from './voiceVisualModel';

const loop = vi.hoisted(() => ({ callbacks: new Set<() => void>(), calls: 0, canvas: null as HTMLCanvasElement | null }));
vi.mock('@react-three/fiber', async () => {
  const React = await import('react');
  const renderer = { get domElement() { return loop.canvas!; }, info: { render: { get calls() { return loop.calls; } } } };
  return {
    // Run the real guard's hooks without attempting to render Three nodes in jsdom.
    Canvas: ({ children }: { children: ReactNode }) => <>{React.Children.toArray(children).slice(0, 1)}</>,
    useThree: (select: (state: { gl: typeof renderer }) => unknown) => select({ gl: renderer }),
    addAfterEffect: (callback: () => void) => { loop.callbacks.add(callback); return () => loop.callbacks.delete(callback); },
  };
});
beforeEach(() => { loop.calls = 0; loop.callbacks.clear(); loop.canvas = document.createElement('canvas'); });
afterEach(() => { cleanup(); loop.callbacks.clear(); });
const echo: EchoState = { tone: 'quiet', levels: [0, 0, 0, 0, 0], input: 'inactive', output: 'inactive' };

it('reports readiness only after this renderer has drawn and unsubscribes after the first frame', () => {
  const ready = vi.fn();
  render(<VoiceThreeEcho echo={echo} onUnavailable={vi.fn()} onFirstFrame={ready} />);
  expect(ready).not.toHaveBeenCalled();
  loop.callbacks.forEach((callback) => callback());
  expect(ready).not.toHaveBeenCalled();
  loop.calls = 9;
  loop.callbacks.forEach((callback) => callback());
  expect(ready).toHaveBeenCalledTimes(1);
  expect(loop.callbacks.size).toBe(0);
});

it('cleans frame and context listeners under StrictMode and ignores a late frame after removal', () => {
  const ready = vi.fn();
  const unavailable = vi.fn();
  const view = render(<StrictMode><VoiceThreeEcho echo={echo} onUnavailable={unavailable} onFirstFrame={ready} /></StrictMode>);
  expect(loop.callbacks.size).toBe(1);
  const lateFrame = [...loop.callbacks][0];
  view.unmount();
  expect(loop.callbacks.size).toBe(0);
  loop.calls = 9;
  lateFrame();
  loop.canvas!.dispatchEvent(new Event('webglcontextlost'));
  expect(ready).not.toHaveBeenCalled();
  expect(unavailable).not.toHaveBeenCalled();
});

it('keeps renderer failure and first-frame readiness as separate signals', () => {
  const ready = vi.fn();
  const unavailable = vi.fn();
  const view = render(<VoiceThreeEcho echo={echo} onUnavailable={unavailable} onFirstFrame={ready} />);
  loop.canvas!.dispatchEvent(new Event('webglcontextlost'));
  expect(unavailable).toHaveBeenCalledTimes(1);
  expect(ready).not.toHaveBeenCalled();
  view.unmount();
  expect(loop.callbacks.size).toBe(0);
});
