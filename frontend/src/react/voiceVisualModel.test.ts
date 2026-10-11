import { describe, expect, it } from 'vitest';
import type { VoiceState } from '../features/voice/controller';
import { voiceVisualState } from './voiceVisualModel';

const media: VoiceState = { phase: 'connected', assistantPhase: 'listening', muted: false,
  outputMuted: false, busy: false, remoteAudio: 1, playbackRequired: false, meterUnavailable: false, error: null };

describe('microphone visual truth', () => {
  it('uses finite local input bands and clamps invalid values', () => {
    expect(voiceVisualState(media, [0.3, -1, 4, Number.NaN, Number.POSITIVE_INFINITY]).levels)
      .toEqual([0.3, 0, 1, 0, 0]);
  });
  it.each(['idle', 'connecting', 'reconnecting', 'ending', 'failed'] as const)('zeros stale input during %s', (phase) => {
    expect(voiceVisualState({ ...media, phase }, [1, 1, 1, 1, 1]).levels).toEqual([0, 0, 0, 0, 0]);
  });
  it('zeros input on microphone mute or meter failure', () => {
    expect(voiceVisualState({ ...media, muted: true }, [1, 1]).input).toBe('muted');
    expect(voiceVisualState({ ...media, muted: true }, [1, 1]).levels).toEqual([0, 0, 0, 0, 0]);
    expect(voiceVisualState({ ...media, meterUnavailable: true }, [1, 1]).levels).toEqual([0, 0, 0, 0, 0]);
  });
  it('keeps input independent of disabled playback and browser playback permission', () => {
    for (const state of [{ ...media, outputMuted: true }, { ...media, playbackRequired: true }]) {
      const echo = voiceVisualState(state, [0.5]);
      expect(echo.output).toBe('blocked');
      expect(echo.input).toBe('measuring');
      expect(echo.levels).toEqual([0.5, 0, 0, 0, 0]);
    }
  });
  it('changes generation color without inventing microphone or playback volume', () => {
    const echo = voiceVisualState({ ...media, assistantPhase: 'speaking', remoteAudio: 2 }, []);
    expect(echo.tone).toBe('sending');
    expect(echo.levels).toEqual([0, 0, 0, 0, 0]);
    expect(voiceVisualState({ ...media, assistantPhase: 'generating' }, []).tone).toBe('working');
  });
});
