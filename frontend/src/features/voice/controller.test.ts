import { describe, expect, it, vi } from 'vitest';
import { VoiceController } from './controller';
import type { MediaRoom, RoomEvents } from './controller';

const details = { server_url: 'ws://127.0.0.1:7880', token: 'synthetic-token', room: 'test-room',
  conversation_id: 'chat', assistant: 'not_configured' as const, purpose: 'media_test' as const };

function fixture() {
  let events: RoomEvents;
  const room: MediaRoom = {
    connect: vi.fn(async () => {}), microphone: vi.fn(async () => {}),
    disconnect: vi.fn(async () => {}), output: vi.fn(async () => {}),
  };
  const api = { voiceToken: vi.fn(async () => details) };
  const controller = new VoiceController(api, vi.fn(), async (callbacks) => {
    events = callbacks; return room;
  });
  return { controller, room, api, event: () => events! };
}

describe('LiveKit media lifecycle', () => {
  it('keeps audio connected when only the input visualizer is unavailable', async () => {
    const { controller, event } = fixture();
    await controller.connect('chat');
    event().meterUnavailable?.();
    expect(controller.state.phase).toBe('connected');
    expect(controller.state.meterUnavailable).toBe(true);
  });
  it('keeps repeated hangups in ending state until actual cleanup finishes', async () => {
    const { controller, room } = fixture();
    await controller.connect('chat');
    let release!: () => void;
    vi.mocked(room.disconnect).mockImplementation(() => new Promise((resolve) => { release = resolve; }));
    const first = controller.hangup();
    const second = controller.hangup();
    await Promise.resolve();
    const phaseDuringCleanup = controller.state.phase;
    release(); await Promise.all([first, second]);
    expect(phaseDuringCleanup).toBe('ending');
    expect(room.disconnect).toHaveBeenCalledTimes(1);
  });

  it('connects only after explicit action and enables just the microphone', async () => {
    const { controller, room, api } = fixture();
    expect(api.voiceToken).not.toHaveBeenCalled();
    await controller.connect('chat');
    expect(room.connect).toHaveBeenCalledWith(details.server_url, details.token);
    expect(room.microphone).toHaveBeenCalledWith(true);
    expect(controller.state.phase).toBe('connected');
    expect(controller.state.remoteAudio).toBe(0);
  });

  it('suppresses repeated clicks while a token request is in flight', async () => {
    const { controller, api } = fixture();
    let release!: (value: typeof details) => void;
    api.voiceToken.mockImplementation(() => new Promise((resolve) => { release = resolve; }));
    const pending = controller.connect('chat');
    await controller.connect('chat');
    expect(api.voiceToken).toHaveBeenCalledTimes(1);
    release(details); await pending;
  });

  it('ignores a late token after hangup without opening a room', async () => {
    const { controller, room, api } = fixture();
    let release!: (value: typeof details) => void;
    api.voiceToken.mockImplementation(() => new Promise((resolve) => { release = resolve; }));
    const pending = controller.connect('chat');
    await controller.hangup(); release(details); await pending;
    expect(room.connect).not.toHaveBeenCalled();
    expect(controller.state.phase).toBe('idle');
  });

  it('cleans up a connection if microphone permission is rejected', async () => {
    const { controller, room } = fixture();
    vi.mocked(room.microphone).mockRejectedValue(new DOMException('private device data', 'NotAllowedError'));
    await controller.connect('chat');
    expect(room.disconnect).toHaveBeenCalled();
    expect(controller.state.phase).toBe('failed');
    expect(controller.state.error).toBe('microphone_denied');
  });

  it('stops media when hangup races a late microphone permission response', async () => {
    const { controller, room } = fixture();
    let release!: () => void;
    vi.mocked(room.microphone).mockImplementation(() => new Promise((resolve) => { release = resolve; }));
    const pending = controller.connect('chat');
    await vi.waitFor(() => expect(room.microphone).toHaveBeenCalled());
    await controller.hangup(); release(); await pending;
    expect(room.disconnect).toHaveBeenCalled();
    expect(controller.state.phase).toBe('idle');
  });

  it('mutes actual local capture and separately mutes output', async () => {
    const { controller, room } = fixture();
    await controller.connect('chat');
    await controller.toggleMicrophone();
    expect(room.microphone).toHaveBeenLastCalledWith(false);
    expect(controller.state.muted).toBe(true);
    await controller.toggleOutput();
    expect(room.output).toHaveBeenLastCalledWith(false);
    expect(controller.state.outputMuted).toBe(true);
  });

  it('tracks reconnects and real remote audio without claiming an assistant exists', async () => {
    const { controller, event } = fixture();
    await controller.connect('chat');
    event().remoteAudio(1); expect(controller.state.remoteAudio).toBe(1);
    event().reconnecting(); expect(controller.state.phase).toBe('reconnecting');
    event().reconnected(); expect(controller.state.phase).toBe('connected');
    event().disconnected();
    await vi.waitFor(() => expect(controller.state.phase).toBe('failed'));
    expect(controller.state.error).toBe('disconnected');
    event().remoteAudio(9); expect(controller.state.remoteAudio).toBe(0);
  });

  it('never exposes a raw infrastructure failure', async () => {
    const { controller, room } = fixture();
    vi.mocked(room.connect).mockRejectedValue(new Error('wss://private:secret@host/token'));
    await controller.connect('chat');
    expect(controller.state.error).toBe('connection_failed');
    expect(room.disconnect).toHaveBeenCalled();
  });
});
