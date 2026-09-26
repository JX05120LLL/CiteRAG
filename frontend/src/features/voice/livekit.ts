import type { MediaRoom, RoomEvents } from './controller';

export async function createMediaRoom(events: RoomEvents): Promise<MediaRoom> {
  const { Room, RoomEvent, Track, LogLevel, setLogLevel, createAudioAnalyser } = await import('livekit-client');
  // SDK debug logs may include signaling details. Product errors use safe codes.
  setLogLevel(LogLevel.silent);
  const room = new Room({ adaptiveStream: false, dynacast: false,
    audioCaptureDefaults: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  const host = document.createElement('div'); host.hidden = true;
  host.dataset.voiceAudio = 'true'; document.body.append(host);
  const tracks = new Set<import('livekit-client').RemoteAudioTrack>();
  let outputEnabled = true;
  let disposed = false;
  let meter: ReturnType<typeof createAudioAnalyser> | null = null;
  let frame = 0;
  async function stopMeter() {
    cancelAnimationFrame(frame);
    const previous = meter; meter = null;
    await previous?.cleanup().catch(() => {});
    events.audioLevels?.([]);
  }
  function startMeter(track: import('livekit-client').LocalAudioTrack) {
    try {
      meter = createAudioAnalyser(track, { cloneTrack: false, fftSize: 512,
        smoothingTimeConstant: 0.7, minDecibels: -80, maxDecibels: -20 });
      const current = meter;
      const frequencies = new Uint8Array(current.analyser.frequencyBinCount);
      const edges = [80, 250, 600, 1500, 3000, 8000];
      const binWidth = current.analyser.context.sampleRate / current.analyser.fftSize;
      function sample() {
        if (disposed || meter !== current) return;
        current.analyser.getByteFrequencyData(frequencies);
        const levels = edges.slice(0, 5).map((low, i) => {
          const from = Math.max(1, Math.floor(low / binWidth));
          const to = Math.min(frequencies.length, Math.max(from + 1, Math.ceil(edges[i + 1] / binWidth)));
          let peak = 0;
          for (let bin = from; bin < to; bin++) peak = Math.max(peak, frequencies[bin]);
          return peak / 255;
        });
        events.audioLevels?.(levels);
        frame = requestAnimationFrame(sample);
      }
      frame = requestAnimationFrame(sample);
    } catch {
      // A missing Web Audio feature does not invalidate a working media connection.
      events.meterUnavailable?.();
    }
  }
  room.on(RoomEvent.TrackSubscribed, (track) => {
    if (track.kind !== Track.Kind.Audio) return;
    const audio = track as import('livekit-client').RemoteAudioTrack;
    tracks.add(audio);
    const element = audio.attach(); element.muted = !outputEnabled; host.append(element);
    events.remoteAudio(tracks.size);
  });
  room.on(RoomEvent.TrackUnsubscribed, (track) => {
    if (track.kind !== Track.Kind.Audio) return;
    const audio = track as import('livekit-client').RemoteAudioTrack;
    for (const element of audio.detach()) element.remove();
    tracks.delete(audio); events.remoteAudio(tracks.size);
  });
  room.on(RoomEvent.Disconnected, events.disconnected);
  room.on(RoomEvent.Reconnecting, events.reconnecting);
  room.on(RoomEvent.Reconnected, events.reconnected);
  room.on(RoomEvent.AudioPlaybackStatusChanged, () => {
    if (!room.canPlaybackAudio) events.playbackRequired();
  });
  return {
    connect: (url, token) => room.connect(url, token, { autoSubscribe: true,
      websocketTimeout: 8000, peerConnectionTimeout: 10000 }),
    microphone: async (enabled) => {
      await stopMeter();
      const publication = await room.localParticipant.setMicrophoneEnabled(enabled);
      if (disposed) { publication?.track?.stop(); return; }
      if (enabled && publication?.track) startMeter(publication.track as import('livekit-client').LocalAudioTrack);
    },
    output: async (enabled) => {
      if (enabled) await room.startAudio();
      outputEnabled = enabled;
      for (const element of host.querySelectorAll('audio')) element.muted = !enabled;
    },
    disconnect: async () => {
      disposed = true;
      room.removeAllListeners();
      for (const publication of room.localParticipant.audioTrackPublications.values()) publication.track?.stop();
      for (const track of tracks) for (const element of track.detach()) element.remove();
      tracks.clear(); host.remove();
      await stopMeter();
      await room.disconnect(true);
      // Permission can resolve after disconnect; a second cleanup stops that late track too.
      for (const publication of room.localParticipant.audioTrackPublications.values()) publication.track?.stop();
    },
  };
}
