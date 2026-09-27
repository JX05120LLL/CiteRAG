import { ApiError } from '../../api/client';
import type { ApiClient, ChatMessage, VoiceConnection, VoiceEvent, VoiceSessionConnection } from '../../api/client';

export interface MediaRoom {
  connect: (url: string, token: string) => Promise<void>;
  microphone: (enabled: boolean) => Promise<void>;
  output: (enabled: boolean) => Promise<void>;
  disconnect: () => Promise<void>;
  discardOutput?: (minimumGeneration: number) => void;
}
export interface RoomEvents {
  disconnected: () => void;
  reconnecting: () => void;
  reconnected: () => void;
  remoteAudio: (count: number) => void;
  playbackRequired: () => void;
  audioLevels?: (levels: readonly number[]) => void;
  meterUnavailable?: () => void;
  assistantIdentity?: string;
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
  assistantPhase?: 'not_configured' | 'starting' | 'listening' | 'recognizing' | 'generating' | 'speaking' | 'ended';
  subtitle?: string;
  finalTranscript?: string;
  utterance?: number;
  transcriptRevision?: number;
  answers?: ChatMessage[];
}

type VoiceApi = { voiceToken: (id: string) => Promise<VoiceConnection> } & Partial<Pick<ApiClient,
  'voiceStart' | 'voiceRenew' | 'voiceStop' | 'voiceEnd' | 'voiceEvents' | 'voiceCorrection' | 'conversationMessages'>>;

export class VoiceController {
  readonly state: VoiceState = { phase: 'idle', muted: false, outputMuted: false,
    busy: false, remoteAudio: 0, playbackRequired: false, meterUnavailable: false, error: null,
    assistantPhase: 'not_configured', subtitle: '', answers: [] };
  private generation = 0;
  private room: MediaRoom | null = null;
  private closing: Promise<void> | null = null;
  private session: VoiceSessionConnection | null = null;
  private heartbeat: ReturnType<typeof setInterval> | null = null;
  private events: AbortController | null = null;
  private serverGeneration = 0;
  private minimumGeneration = 0;
  constructor(private readonly api: VoiceApi,
              private readonly changed: () => void, private readonly factory: RoomFactory,
              private readonly levelsChanged: (levels: readonly number[]) => void = () => {}) {}

  async connect(conversation: string, assistant = false): Promise<void> {
    if (!conversation || this.closing || !['idle', 'failed'].includes(this.state.phase)) return;
    if (this.session) { await this.hangup(); if (this.session) return; }
    const generation = ++this.generation;
    const current = () => generation === this.generation;
    this.state.phase = 'connecting'; this.state.error = null; this.state.meterUnavailable = false;
    this.state.answers = []; this.changed();
    let room: MediaRoom | null = null;
    try {
      const details = assistant && this.api.voiceStart
        ? await this.api.voiceStart(conversation, crypto.randomUUID()) : await this.api.voiceToken(conversation);
      if (!current()) {
        if ('session_id' in details) await this.api.voiceEnd?.(details.session_id, details.control_token);
        return;
      }
      this.session = 'session_id' in details ? details : null;
      this.serverGeneration = this.minimumGeneration = this.session?.generation ?? 0;
      this.state.assistantPhase = this.session ? 'starting' : 'not_configured';
      this.state.subtitle = ''; this.state.finalTranscript = ''; this.state.utterance = 0;
      if (this.session) {
        this.events = new AbortController();
        const session = this.session;
        void this.api.voiceEvents?.(session.session_id, session.control_token, this.events.signal,
          (event) => { if (current()) this.receive(event); }).then(() => {
            if (current()) void this.fail('voice_events_failed');
          }).catch(() => { if (current()) void this.fail('voice_events_failed'); });
        this.heartbeat = setInterval(() => { void this.renew(false, generation); }, 10000);
        void this.loadHistory(conversation, generation);
      }
      room = await this.factory({
        assistantIdentity: this.session?.assistant_identity,
        disconnected: () => { if (current()) void this.fail('disconnected'); },
        reconnecting: () => { if (current()) { this.room?.discardOutput?.(Number.MAX_SAFE_INTEGER); this.state.phase = 'reconnecting'; this.changed(); } },
        reconnected: () => { if (current()) { this.state.phase = 'connected'; void this.renew(true, generation); this.changed(); } },
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

  private receive(event: VoiceEvent): void {
    if (!this.session || event.session_id !== this.session.session_id || event.generation < this.minimumGeneration) return;
    if (event.generation > this.serverGeneration) {
      this.serverGeneration = event.generation;
      this.room?.discardOutput?.(event.generation);
    }
    if (event.type === 'ended') {
      if (event.reason === 'binding_or_lease_invalid') this.state.answers = (this.state.answers ?? []).map((answer) => ({ ...answer, stale: true }));
      void this.fail(event.reason === 'hangup' ? 'disconnected' : 'voice_session_ended'); return;
    }
    if (event.type === 'interrupted') {
      this.state.assistantPhase = 'listening'; this.state.subtitle = '';
      this.room?.discardOutput?.(event.generation);
    } else if (event.type === 'ready' || event.type === 'phase') {
      if (['listening', 'recognizing', 'generating', 'speaking', 'starting'].includes(event.phase ?? ''))
        this.state.assistantPhase = event.phase as VoiceState['assistantPhase'];
    } else if (event.type === 'transcript') {
      this.state.subtitle = event.text ?? '';
      if (event.final) {
        this.state.error = null;
        this.state.finalTranscript = event.text; this.state.utterance = event.utterance;
        this.state.transcriptRevision = event.revision;
      }
    } else if (event.type === 'answer' && event.answer) {
      this.state.answers = [...(this.state.answers ?? []).filter((item) => item.message_id !== event.answer!.message_id), event.answer].slice(-50);
    } else if (event.type === 'error') {
      this.state.error = event.code ?? 'voice_turn_failed'; this.state.assistantPhase = 'listening';
    }
    this.changed();
  }

  private async renew(reconnect: boolean, generation: number): Promise<void> {
    const session = this.session;
    if (!session || !this.api.voiceRenew) return;
    try {
      const result = await this.api.voiceRenew(session.session_id, session.control_token, reconnect);
      if (generation !== this.generation) return;
      if (reconnect) {
        this.minimumGeneration = result.generation + 1;
        this.room?.discardOutput?.(this.minimumGeneration);
        this.state.assistantPhase = 'listening';
        await this.loadHistory(session.conversation_id, generation);
      }
    } catch (error) {
      if (generation === this.generation) await this.fail(error instanceof ApiError ? error.code : 'voice_lease_expired');
    }
  }

  private async loadHistory(conversation: string, generation: number): Promise<void> {
    if (!this.api.conversationMessages) return;
    const previous = new Map((this.state.answers ?? []).map((item) => [item.message_id, item]));
    try {
      const items = await this.api.conversationMessages(conversation);
      if (generation !== this.generation) return;
      const merged = new Map(items.map((item) => [item.message_id, item]));
      for (const item of this.state.answers ?? []) {
        if (previous.get(item.message_id) !== item) merged.set(item.message_id, item);
      }
      this.state.answers = [...merged.values()].sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at)).slice(-50);
      this.changed();
    } catch {
      if (generation === this.generation) { this.state.error = 'voice_history_failed'; this.changed(); }
    }
  }

  async stopAnswer(): Promise<void> {
    const session = this.session;
    if (!session || !this.api.voiceStop || this.state.busy) return;
    this.room?.discardOutput?.(Number.MAX_SAFE_INTEGER);
    this.minimumGeneration = Number.MAX_SAFE_INTEGER;
    this.state.busy = true; this.changed();
    const generation = this.generation;
    try {
      const result = await this.api.voiceStop(session.session_id, session.control_token);
      if (generation === this.generation) {
        this.minimumGeneration = result.generation + 1;
        this.room?.discardOutput?.(this.minimumGeneration);
        this.state.assistantPhase = 'listening';
      }
    } catch { if (generation === this.generation) await this.fail('voice_stop_failed'); }
    finally { if (generation === this.generation) { this.state.busy = false; this.changed(); } }
  }

  async correctTranscript(text: string): Promise<void> {
    const session = this.session;
    if (!session || !this.api.voiceCorrection || !this.state.utterance || this.state.busy || !text.trim()) return;
    this.minimumGeneration = this.serverGeneration + 1;
    this.room?.discardOutput?.(this.minimumGeneration);
    this.state.busy = true; this.changed();
    const generation = this.generation;
    try {
      const result = await this.api.voiceCorrection(session.session_id, session.control_token, this.state.utterance,
        (this.state.transcriptRevision ?? 1) + 1, text.trim());
      if (generation === this.generation) {
        this.minimumGeneration = Math.max(this.minimumGeneration, result.generation);
        if (result.generation > this.serverGeneration) {
          this.serverGeneration = result.generation;
          this.room?.discardOutput?.(this.minimumGeneration);
        }
      }
    } catch (error) { if (generation === this.generation) this.state.error = error instanceof ApiError ? error.code : 'transcript_stale'; }
    finally { if (generation === this.generation) { this.state.busy = false; this.changed(); } }
  }

  private async fail(code: string): Promise<void> {
    const generation = this.generation + 1;
    await this.hangup();
    if (generation === this.generation) {
      this.state.phase = 'failed';
      if (this.state.error !== 'voice_cleanup_failed') this.state.error = code;
      this.changed();
    }
  }

  async hangup(): Promise<void> {
    if (this.closing) return this.closing;
    ++this.generation;
    if (this.heartbeat) clearInterval(this.heartbeat);
    this.heartbeat = null;
    this.events?.abort(); this.events = null;
    const room = this.room; this.room = null;
    const session = this.session;
    this.state.phase = 'ending'; this.state.busy = true; this.changed();
    const closing = (async () => {
      try {
        room?.discardOutput?.(Number.MAX_SAFE_INTEGER);
        await room?.disconnect();
      }
      catch { this.state.error = 'voice_cleanup_failed'; }
      finally {
        if (session) {
          try { await this.api.voiceEnd?.(session.session_id, session.control_token); this.session = null; }
          catch (error) {
            if (error instanceof ApiError && error.code === 'voice_session_missing') {
              // API restart revoked this in-memory lease; a new start has its own server gates.
              this.session = null; this.state.error = 'voice_session_ended';
            } else this.state.error = 'voice_cleanup_failed';
          }
        }
        this.state.phase = 'idle'; this.state.busy = false; this.state.remoteAudio = 0;
        this.state.muted = false; this.state.outputMuted = false; this.state.playbackRequired = false;
        this.state.meterUnavailable = false;
        this.state.assistantPhase = 'ended'; this.state.subtitle = '';
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
