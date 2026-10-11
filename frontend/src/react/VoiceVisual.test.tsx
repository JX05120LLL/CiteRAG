import { StrictMode } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import type { VoiceState } from '../features/voice/controller';
import { VoiceVisual } from './VoiceVisual';

const scene = vi.hoisted(() => ({ requested: false, release: () => {}, mounts: 0, releases: 0, fail: false,
  unavailable: () => {}, firstFrame: () => {} }));
vi.mock('./VoiceThreeEcho', async () => {
  scene.requested = true;
  await new Promise<void>((resolve) => { scene.release = resolve; });
  const React = await import('react');
  return { default: ({ echo, onUnavailable, onFirstFrame }: { echo: { levels: readonly number[] }; onUnavailable: () => void; onFirstFrame: () => void }) => {
    React.useEffect(() => { scene.mounts++; return () => { scene.releases++; }; }, []);
    scene.unavailable = onUnavailable;
    scene.firstFrame = onFirstFrame;
    if (scene.fail) throw new Error('synthetic GPU error');
    return <div data-testid="three-echo" data-levels={JSON.stringify(echo.levels)} />;
  } };
});

const media: VoiceState = { phase: 'connected', assistantPhase: 'listening', muted: false,
  outputMuted: false, busy: false, remoteAudio: 0, playbackRequired: false, meterUnavailable: false, error: null };
let hidden = false;
let reduced = false;
let preference: (() => void) | undefined;
const removePreference = vi.fn();

beforeEach(() => {
  hidden = reduced = false;
  scene.mounts = scene.releases = 0;
  scene.fail = false;
  vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => hidden ? 'hidden' : 'visible');
  vi.stubGlobal('matchMedia', () => ({ get matches() { return reduced; },
    addEventListener: (_type: string, callback: () => void) => { preference = callback; },
    removeEventListener: removePreference }));
  vi.stubGlobal('WebGL2RenderingContext', undefined);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('does not load Three without WebGL and uses truthful microphone bands in SVG', () => {
  const view = render(<VoiceVisual media={media} levels={[0.5, 0.2]} />);
  expect(view.container.querySelector('svg')).toBeTruthy();
  expect(view.container.querySelector('.voice-echo')?.getAttribute('data-renderer')).toBe('static');
  expect(scene.requested).toBe(false);
  view.rerender(<VoiceVisual media={{ ...media, muted: true }} levels={[1, 1]} />);
  expect(view.container.querySelector('.voice-echo')?.getAttribute('data-input')).toBe('muted');
  expect(Array.from(view.container.querySelectorAll('rect')).every((rect) => rect.getAttribute('height') === '10')).toBe(true);
});

it('respects reduced motion without importing or mounting the Three scene', () => {
  vi.stubGlobal('WebGL2RenderingContext', class {});
  reduced = true;
  render(<VoiceVisual media={media} levels={[]} />);
  expect(screen.queryByTestId('three-echo')).toBeNull();
  expect(scene.requested).toBe(false);
});

it('ignores a late lazy scene load while hidden, then releases the visible scene on hide', async () => {
  vi.stubGlobal('WebGL2RenderingContext', class {});
  const view = render(<VoiceVisual media={media} levels={[0.6]} />);
  await waitFor(() => expect(scene.requested).toBe(true));
  hidden = true;
  document.dispatchEvent(new Event('visibilitychange'));
  scene.release();
  await waitFor(() => expect(view.container.querySelector('.voice-echo')?.getAttribute('data-renderer')).toBe('static'));
  expect(scene.mounts).toBe(0);
  hidden = false;
  document.dispatchEvent(new Event('visibilitychange'));
  await screen.findByTestId('three-echo');
  await waitFor(() => expect(scene.mounts).toBe(1));
  hidden = true;
  document.dispatchEvent(new Event('visibilitychange'));
  await waitFor(() => {
    expect(screen.queryByTestId('three-echo')).toBeNull();
    expect(scene.releases).toBe(1);
  });
  view.unmount();
});

it('cleans listeners and scene ownership through StrictMode and changing motion preference', async () => {
  vi.stubGlobal('WebGL2RenderingContext', class {});
  const add = vi.spyOn(document, 'addEventListener');
  const remove = vi.spyOn(document, 'removeEventListener');
  const view = render(<StrictMode><VoiceVisual media={media} levels={[0.4]} /></StrictMode>);
  await screen.findByTestId('three-echo');
  await waitFor(() => expect(scene.mounts).toBeGreaterThan(0));
  reduced = true;
  preference?.();
  await waitFor(() => {
    expect(screen.queryByTestId('three-echo')).toBeNull();
    expect(scene.mounts).toBe(scene.releases);
  });
  view.unmount();
  expect(add.mock.calls.filter(([type]) => type === 'visibilitychange').length)
    .toBe(remove.mock.calls.filter(([type]) => type === 'visibilitychange').length);
  expect(removePreference).toHaveBeenCalled();
});

it('keeps SVG until a drawn frame and resets it after hiding, rejecting late callbacks', async () => {
  vi.stubGlobal('WebGL2RenderingContext', class {});
  const view = render(<StrictMode><VoiceVisual media={media} levels={[0.4]} /></StrictMode>);
  await screen.findByTestId('three-echo');
  expect(view.container.querySelector('.voice-echo-placeholder svg')).toBeTruthy();
  expect(view.container.querySelector('.voice-echo-3d')?.getAttribute('data-drawn')).toBe('false');
  scene.firstFrame();
  await waitFor(() => expect(view.container.querySelector('.voice-echo-placeholder')).toBeNull());
  expect(view.container.querySelector('.voice-echo-3d')?.getAttribute('data-drawn')).toBe('true');
  const oldCallback = scene.firstFrame;
  hidden = true;
  document.dispatchEvent(new Event('visibilitychange'));
  await waitFor(() => expect(screen.queryByTestId('three-echo')).toBeNull());
  hidden = false;
  document.dispatchEvent(new Event('visibilitychange'));
  await screen.findByTestId('three-echo');
  oldCallback();
  expect(view.container.querySelector('.voice-echo-3d')?.getAttribute('data-drawn')).toBe('false');
  expect(view.container.querySelector('.voice-echo-placeholder svg')).toBeTruthy();
  scene.firstFrame();
  await waitFor(() => expect(view.container.querySelector('.voice-echo-placeholder')).toBeNull());
});

it('falls back after a context loss notification without resurrecting the scene on media updates', async () => {
  vi.stubGlobal('WebGL2RenderingContext', class {});
  const view = render(<VoiceVisual media={media} levels={[0.4]} />);
  await screen.findByTestId('three-echo');
  scene.unavailable();
  await waitFor(() => {
    expect(screen.queryByTestId('three-echo')).toBeNull();
    expect(scene.releases).toBe(1);
  });
  const mounts = scene.mounts;
  view.rerender(<VoiceVisual media={{ ...media, assistantPhase: 'speaking' }} levels={[0.8]} />);
  expect(scene.mounts).toBe(mounts);
  expect(view.container.querySelector('svg')).toBeTruthy();
  scene.firstFrame();
  expect(view.container.querySelector('.voice-echo-3d')).toBeNull();
});

it('contains renderer errors within the visual and leaves adjacent call controls available', async () => {
  vi.stubGlobal('WebGL2RenderingContext', class {});
  vi.spyOn(console, 'error').mockImplementation(() => {});
  scene.fail = true;
  render(<><VoiceVisual media={media} levels={[]} /><button>挂断</button></>);
  await waitFor(() => expect(document.querySelector('.voice-echo')?.getAttribute('data-renderer')).toBe('static'));
  expect(document.querySelector('svg')).toBeTruthy();
  expect(screen.getByRole<HTMLButtonElement>('button', { name: '挂断' }).disabled).toBe(false);
});
