import { describe, expect, it, vi } from 'vitest';
import { VoiceController } from './controller';
import type { MediaRoom, RoomEvents } from './controller';
import { ApiError } from '../../api/client';
import type { ChatMessage } from '../../api/client';

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
  it('rejects old events while correction is pending and accepts its new generation', async () => {
    const { controller, room, api } = fixture();
    let push!: (event: import('../../api/client').VoiceEvent) => void;
    let release!: (value: { generation: number }) => void;
    room.discardOutput = vi.fn();
    Object.assign(api, {
      voiceStart: async () => ({ ...details, session_id: 's', control_token: 'synthetic-control-1234567890',
        assistant_identity: 'assistant', assistant: 'starting', purpose: 'voice_assistant', lease_seconds: 40, generation: 0 }),
      voiceEvents: async (_id: string, _control: string, signal: AbortSignal, callback: typeof push) => {
        push = callback; await new Promise<void>((resolve) => signal.addEventListener('abort', () => resolve()));
      },
      voiceCorrection: () => new Promise<{ generation: number }>((resolve) => { release = resolve; }),
      voiceEnd: async () => ({ status: 'ended' }),
    });
    await controller.connect('chat', true);
    push({ type: 'transcript', seq: 1, session_id: 's', generation: 1, text: 'original', final: true, utterance: 1, revision: 1 });
    const pending = controller.correctTranscript('corrected');
    push({ type: 'transcript', seq: 2, session_id: 's', generation: 1, text: 'late old', final: true, utterance: 1, revision: 1 });
    expect(controller.state.finalTranscript).toBe('original');
    push({ type: 'transcript', seq: 3, session_id: 's', generation: 2, text: 'corrected', final: true, utterance: 1, revision: 2 });
    release({ generation: 2 }); await pending;
    expect(controller.state.finalTranscript).toBe('corrected');
    expect(room.discardOutput).toHaveBeenLastCalledWith(2);
    expect(room.discardOutput).toHaveBeenCalledTimes(3); // Do not discard new audio on HTTP acknowledgement.
    await controller.hangup();
  });
  it('clears the previous chat history before starting a different call', async () => {
    const { controller } = fixture();
    controller.state.answers = [{ message_id: 'previous-chat' } as ChatMessage];
    await controller.connect('different-chat');
    expect(controller.state.answers).toEqual([]);
    await controller.hangup();
  });
  it('allows an explicit new call after API restart invalidates the old lease', async () => {
    const { controller, api } = fixture();
    const start = vi.fn(async () => ({ ...details, session_id: 's',
      control_token: 'synthetic-control-1234567890', assistant_identity: 'assistant',
      assistant: 'starting', purpose: 'voice_assistant', lease_seconds: 40, generation: 0 }));
    Object.assign(api, { voiceStart: start, voiceEnd: async () => { throw new ApiError('http', 'voice_session_missing'); } });
    await controller.connect('chat', true);
    await controller.hangup();
    expect(controller.state.error).toBe('voice_session_ended');
    await controller.connect('chat', true);
    expect(start).toHaveBeenCalledTimes(2);
    await controller.hangup();
  });
  it('revokes the server call even if local disconnect fails', async () => {
    const { controller, room, api } = fixture();
    const end = vi.fn(async () => ({ status: 'ended' }));
    Object.assign(api, { voiceStart: async () => ({ ...details, session_id: 's',
      control_token: 'synthetic-control-1234567890', assistant_identity: 'assistant',
      assistant: 'starting', purpose: 'voice_assistant', lease_seconds: 40, generation: 0 }),
      voiceEnd: end });
    await controller.connect('chat', true);
    vi.mocked(room.disconnect).mockRejectedValue(new Error('synthetic disconnect failure'));
    await controller.hangup();
    expect(end).toHaveBeenCalledWith('s', 'synthetic-control-1234567890');
  });
  it('keeps media connection separate from assistant readiness and stops stale output', async () => {
    let push!: (event: import('../../api/client').VoiceEvent) => void;
    let events!: RoomEvents;
    const history = vi.fn(async () => []);
    const room = { connect: async () => {}, microphone: async () => {}, output: async () => {},
      disconnect: async () => {}, discardOutput: vi.fn() };
    const api = { voiceToken: async () => details,
      voiceStart: async () => ({ ...details, session_id: 's', control_token: 'synthetic-control-1234567890',
        assistant_identity: 'assistant', assistant: 'starting' as const, purpose: 'voice_assistant' as const,
        lease_seconds: 40, generation: 0 }),
      voiceEvents: async (_id: string, _control: string, signal: AbortSignal, callback: typeof push) => {
        push = callback; await new Promise<void>((resolve) => signal.addEventListener('abort', () => resolve()));
      }, voiceRenew: async () => ({ generation: 0 }), voiceStop: async () => ({ generation: 1 }),
      voiceEnd: async () => ({ status: 'ended' }), conversationMessages: history };
    const controller = new VoiceController(api, vi.fn(), async (callbacks) => { events = callbacks; return room; });
    await controller.connect('chat', true);
    expect(controller.state.phase).toBe('connected');
    expect(controller.state.assistantPhase).toBe('starting');
    push({ type: 'ready', phase: 'listening', seq: 1, generation: 0, session_id: 's' });
    expect(controller.state.assistantPhase).toBe('listening');
    events.reconnecting(); events.reconnected();
    await vi.waitFor(() => expect(history).toHaveBeenCalledTimes(2));
    expect(history).toHaveBeenLastCalledWith('chat');
    await controller.stopAnswer();
    expect(room.discardOutput).toHaveBeenCalled();
    push({ type: 'phase', phase: 'speaking', seq: 2, generation: 0, session_id: 's' });
    expect(controller.state.assistantPhase).not.toBe('speaking');
    await controller.hangup();
    push({ type: 'ready', phase: 'listening', seq: 3, generation: 2, session_id: 's' });
    expect(controller.state.phase).toBe('idle');
  });
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
