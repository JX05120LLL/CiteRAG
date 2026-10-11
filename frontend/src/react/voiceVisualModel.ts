import type { VoiceState } from '../features/voice/controller';

export type EchoTone = 'quiet' | 'listening' | 'working' | 'sending' | 'attention';
export interface EchoState {
  tone: EchoTone;
  levels: readonly number[];
  input: 'inactive' | 'muted' | 'unavailable' | 'measuring';
  output: 'inactive' | 'blocked' | 'enabled';
}

// These five bands come from LiveKit's local microphone analyser, never TTS audio.
export function voiceVisualState(media: VoiceState, levels: readonly number[]): EchoState {
  const connected = media.phase === 'connected';
  const input = !connected ? 'inactive' : media.muted ? 'muted'
    : media.meterUnavailable ? 'unavailable' : 'measuring';
  const output = !connected ? 'inactive' : media.outputMuted || media.playbackRequired ? 'blocked' : 'enabled';
  const tone: EchoTone = media.error || media.phase === 'failed' ? 'attention' : !connected ? 'quiet'
    : media.assistantPhase === 'recognizing' || media.assistantPhase === 'generating' ? 'working'
      : media.assistantPhase === 'speaking' ? 'sending'
        : media.assistantPhase === 'waiting_input' || media.assistantPhase === 'waiting_approval' ? 'attention'
          : media.assistantPhase === 'listening' ? 'listening' : 'quiet';
  return { tone, input, output, levels: Array.from({ length: 5 }, (_, index) => {
    const value = levels[index];
    return input === 'measuring' && Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0;
  }) };
}
