import { ApiError } from '../../api/client';
import type { VoiceConnection } from '../../api/client';

export interface MediaRoom {
  connect: (url: string, token: string) => Promise<void>;
  microphone: (enabled: boolean) => Promise<void>;
  output: (enabled: boolean) => Promise<void>;
  disconnect: () => Promise<void>;
}
export interface RoomEvents {
  disconnected: () => void;
  reconnecting: () => void;
  reconnected: () => void;
  remoteAudio: (count: number) => void;
  playbackRequired: () => void;
  audioLevels?: (levels: readonly number[]) => void;
  meterUnavailable?: () => void;
}
export type RoomFactory = (events: RoomEvents) => Promise<MediaRoom>;
export interface VoiceState {
  phase: 'idle' | 'connecting' | 'connected' | 'reconnecting' | 'ending' | 'failed';
  muted: boolean;
  outputMuted: boolean;
  busy: boolean;
  remoteAudio: number;
  playbackRequired: boolean;
  meterUnavailable: boolean;
  error: string | null;
}

export class VoiceController {
  readonly state: VoiceState = { phase: 'idle', muted: false, outputMuted: false,
    busy: false, remoteAudio: 0, playbackRequired: false, meterUnavailable: false, error: null };
  private generation = 0;
  private room: MediaRoom | null = null;
  private closing: Promise<void> | null = null;
  constructor(private readonly api: { voiceToken: (id: string) => Promise<VoiceConnection> },
              private readonly changed: () => void, private readonly factory: RoomFactory,
              private readonly levelsChanged: (levels: readonly number[]) => void = () => {}) {}

  async connect(conversation: string): Promise<void> {
    if (!conversation || this.closing || !['idle', 'failed'].includes(this.state.phase)) return;
    const generation = ++this.generation;
    const current = () => generation === this.generation;
    this.state.phase = 'connecting'; this.state.error = null; this.state.meterUnavailable = false; this.changed();
    let room: MediaRoom | null = null;
    try {
      const details = await this.api.voiceToken(conversation);
      if (!current()) return;
      room = await this.factory({
        disconnected: () => { if (current()) void this.fail('disconnected'); },
        reconnecting: () => { if (current()) { this.state.phase = 'reconnecting'; this.changed(); } },
        reconnected: () => { if (current()) { this.state.phase = 'connected'; this.changed(); } },
        remoteAudio: (count) => { if (current()) { this.state.remoteAudio = count; this.changed(); } },
        playbackRequired: () => { if (current()) { this.state.playbackRequired = true; this.changed(); } },
        audioLevels: (levels) => { if (current()) this.levelsChanged(levels); },
        meterUnavailable: () => { if (current()) { this.state.meterUnavailable = true; this.changed(); } },
      });
      if (!current()) { await room.disconnect(); return; }
      this.room = room;
      await room.connect(details.server_url, details.token);
      if (!current()) { await room.disconnect(); return; }
      await room.microphone(true);
      if (!current()) { await room.disconnect(); return; }
      this.state.phase = 'connected'; this.state.muted = false; this.state.outputMuted = false;
      this.changed();
    } catch (error) {
      if (current()) {
        const code = error instanceof ApiError ? error.code || 'connection_failed'
          : error instanceof DOMException && error.name === 'NotAllowedError' ? 'microphone_denied'
          : error instanceof DOMException && error.name === 'NotFoundError' ? 'microphone_missing'
          : 'connection_failed';
        await this.fail(code);
      } else if (room) await room.disconnect().catch(() => {});
    }
  }

  private async fail(code: string): Promise<void> {
    const generation = this.generation + 1;
    await this.hangup();
    if (generation === this.generation) {
      this.state.phase = 'failed'; this.state.error = code; this.changed();
    }
  }

  async hangup(): Promise<void> {
    if (this.closing) return this.closing;
    ++this.generation;
    const room = this.room; this.room = null;
    this.state.phase = 'ending'; this.state.busy = true; this.changed();
    const closing = (async () => {
      try { await room?.disconnect(); }
      catch { /* Local capture cleanup is also enforced by the SDK adapter. */ }
      finally {
        this.state.phase = 'idle'; this.state.busy = false; this.state.remoteAudio = 0;
        this.state.muted = false; this.state.outputMuted = false; this.state.playbackRequired = false;
        this.state.meterUnavailable = false;
        this.changed();
      }
    })();
    this.closing = closing;
    try { await closing; }
    finally { if (this.closing === closing) this.closing = null; }
  }

  async toggleMicrophone(): Promise<void> {
    if (this.state.phase !== 'connected' || this.state.busy || !this.room) return;
    const generation = this.generation;
    this.state.busy = true; this.changed();
    try {
      const muted = !this.state.muted;
      await this.room.microphone(!muted);
      if (generation === this.generation) this.state.muted = muted;
    } catch { if (generation === this.generation) this.state.error = 'microphone_failed'; }
    finally { if (generation === this.generation) { this.state.busy = false; this.changed(); } }
  }

  async toggleOutput(): Promise<void> {
    if (this.state.phase !== 'connected' || this.state.busy || !this.room) return;
    const generation = this.generation;
    this.state.busy = true; this.changed();
    try {
      const muted = this.state.playbackRequired ? false : !this.state.outputMuted;
      await this.room.output(!muted);
      if (generation === this.generation) {
        this.state.outputMuted = muted; this.state.playbackRequired = false;
      }
    } catch { if (generation === this.generation) this.state.error = 'playback_failed'; }
    finally { if (generation === this.generation) { this.state.busy = false; this.changed(); } }
  }
}
