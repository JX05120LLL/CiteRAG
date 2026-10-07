"""Actual LiveKit PCM input/output. Speech services are used only after explicit start."""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import suppress

from app.services.errors import ServiceError
from app.voice.audio import MP3Decoder
from app.voice.providers import MiniMaxTTS, SpeechError, VolcASR
from app.voice.turns import VoiceTurns
from app.voice.vad import Endpoint, SileroVAD


class LiveKitWorker:
    def __init__(self, runtime, call):
        from livekit import rtc

        self.runtime, self.call = runtime, call
        self.registry = runtime.registry
        self.settings = runtime.app.state.settings
        self.vad = SileroVAD(self.settings.voice_vad_model)
        self.endpoint = Endpoint()
        self.room = rtc.Room()
        self.connect_task = None
        self.stream = None
        self.input_task = None
        self.asr_task = None
        self.audio_queue = None
        self.source = None
        self.publication = None
        self.revocations = set()
        self.listeners = []
        self.utterance = 0
        self.closed = False
        self.done = asyncio.Event()
        self.asr = VolcASR(self.settings.voice_asr_key.get_secret_value(),
                           self.settings.voice_asr_resource,
                           app_key=self.settings.voice_asr_app_key.get_secret_value()
                           if self.settings.voice_asr_app_key else "")
        self.tts = MiniMaxTTS(self.settings.voice_tts_key.get_secret_value(),
                              self.settings.voice_tts_voice)
        self.turns = VoiceTurns(self.registry, call, runtime.answer, self.play,
                                play_stream=self.play_stream)
        call.flush_audio = self.flush_audio

    def flush_audio(self):
        # This immediately discards actual RTC queued PCM; it is not a state flag.
        if self.source:
            self.source.clear_queue()
        publication = self.publication
        self.publication = None
        if publication:
            task = asyncio.create_task(self.room.local_participant.unpublish_track(publication.sid))
            self.revocations.add(task)

    def reset_input(self):
        self.endpoint = Endpoint()
        self.audio_queue = None
        if self.asr_task:
            self.asr_task.cancel()

    async def run(self):
        from livekit import rtc

        @self.room.on("track_subscribed")
        def subscribed(track, publication, participant):
            if (self.closed or participant.identity != self.call.identity
                or track.kind != rtc.TrackKind.KIND_AUDIO
                or publication.source != rtc.TrackSource.SOURCE_MICROPHONE):
                return
            if self.input_task and not self.input_task.done():
                return
            self.input_task = asyncio.create_task(self.receive(track))

        @self.room.on("participant_disconnected")
        def left(participant):
            if participant.identity == self.call.identity:
                self.registry.interrupt(self.call, "media_disconnected")
                # Stop ASR too; old finals must not enter a resumed session.
                if self.asr_task:
                    self.asr_task.cancel()
                self.done.set()

        @self.room.on("disconnected")
        def disconnected(_reason):
            self.done.set()

        self.listeners = [("track_subscribed", subscribed),
                          ("participant_disconnected", left), ("disconnected", disconnected)]

        terminal_reason = "worker_disconnected"
        try:
            # LiveKit's FFI requires room.connect() to finish its ready handshake.
            # Cancelling it mid-join can terminate the entire Python process.
            self.connect_task = asyncio.create_task(self.room.connect(
                self.settings.livekit_url, self.runtime.token(self.call, worker=True)))
            await asyncio.shield(self.connect_task)
            await self.runtime.check(self.call)
            self.call.phase = "listening"
            self.registry.emit(self.call, "ready", phase="listening")
            await self.done.wait()
        except asyncio.CancelledError:
            raise
        except ServiceError:
            terminal_reason = "binding_or_lease_invalid"
        except Exception:
            self.registry.emit(self.call, "error", code="voice_worker_failed", text_available=False)
        finally:
            if not self.call.closed and not self.call.closing:
                # Separate task prevents the worker awaiting its own cancellation.
                asyncio.create_task(self.registry.close_call(call=self.call,
                    reason=getattr(self, "terminal_reason", terminal_reason)))

    async def receive(self, track):
        from livekit import rtc

        stream = rtc.AudioStream.from_track(track=track, sample_rate=16000,
                                                num_channels=1, frame_size_ms=20, capacity=32)
        self.stream = stream
        buffer = bytearray()
        try:
            async for event in stream:
                if self.closed or not self.registry.current(self.call, self.call.generation):
                    return
                buffer.extend(bytes(event.frame.data))
                while len(buffer) >= 1024:
                    pcm = bytes(buffer[:1024])
                    del buffer[:1024]
                    stage = self.endpoint.feed(pcm, self.vad.probability(pcm))
                    if stage == "start":
                        await self.runtime.check(self.call)
                        self.registry.interrupt(self.call, "barge_in")
                        if self.asr_task:
                            self.asr_task.cancel()
                            with suppress(asyncio.CancelledError):
                                await self.asr_task
                        self.utterance += 1
                        self.audio_queue = asyncio.Queue(maxsize=64)
                        for part in self.endpoint.pre_roll:
                            self.audio_queue.put_nowait(part)
                        self.call.phase = "recognizing"
                        self.registry.emit(self.call, "phase", phase="recognizing")
                        self.asr_task = asyncio.create_task(self.recognize(
                            self.audio_queue, self.utterance, self.call.generation))
                    elif stage in {"speech", "end"} and self.audio_queue:
                        self.audio_queue.put_nowait(pcm)
                        if stage == "end":
                            self.audio_queue.put_nowait(None)
                            self.audio_queue = None
        except asyncio.CancelledError:
            raise
        except ServiceError:
            self.terminal_reason = "binding_or_lease_invalid"
            self.done.set()
        except Exception:
            self.registry.emit(self.call, "error", code="voice_input_failed", text_available=False)
            self.done.set()
        finally:
            await stream.aclose()

    async def recognize(self, queue, utterance, generation):
        async def audio():
            while (part := await queue.get()) is not None:
                yield part

        try:
            async for text, final in self.asr.recognize(audio()):
                if (utterance != self.utterance
                    or not self.registry.current(self.call, generation)):
                    return
                await self.runtime.check(self.call, generation)
                if final:
                    if text.strip():
                        await self.turns.submit(utterance, text)
                    else:
                        self.registry.emit(self.call, "error", code="asr_empty",
                                           text_available=False)
                        self.registry.emit(self.call, "phase", phase="listening")
                else:
                    self.registry.emit(self.call, "transcript", text=text, final=False,
                                       utterance=utterance, revision=1)
        except asyncio.CancelledError:
            raise
        except SpeechError as error:
            if self.registry.current(self.call, generation):
                self.registry.emit(self.call, "error", code=str(error), text_available=False)
                self.registry.emit(self.call, "phase", phase="listening")

    async def play(self, text, generation):
        async def single() -> AsyncIterator[str]:
            yield text

        await self._play_segments(single(), generation, caption=False)

    async def play_stream(self, segments: AsyncIterator[str], generation: int):
        await self._play_segments(segments, generation, caption=True)

    async def _play_segments(self, segments: AsyncIterator[str], generation: int, *, caption: bool):
        import av
        from livekit import rtc

        await self.runtime.check(self.call, generation)
        source = rtc.AudioSource(24000, 1, queue_size_ms=200)
        self.source = source
        track = rtc.LocalAudioTrack.create_audio_track(
            f"citerag-output-{generation}", source)
        resampler = av.AudioResampler(format="s16", layout="mono", rate=24000)
        publication = None
        pcm = bytearray()
        caption_pending = None
        first_audio = True

        async def decoded(frames):
            nonlocal caption_pending, first_audio
            for frame in frames:
                for mono in resampler.resample(frame):
                    pcm.extend(mono.to_ndarray().astype("<i2").tobytes())
                    while len(pcm) >= 960:
                        if not self.registry.current(self.call, generation):
                            return
                        frame_data = bytes(pcm[:960])
                        del pcm[:960]
                        await source.capture_frame(rtc.AudioFrame(frame_data, 24000, 1, 480))
                        if first_audio and self.call.turn_started_at is not None:
                            elapsed_ms = round(
                                (time.monotonic() - self.call.turn_started_at) * 1000)
                            self.registry.emit(self.call, "timing", metric="first_audio_sent",
                                               elapsed_ms=elapsed_ms)
                            first_audio = False
                        if (caption_pending is not None
                            and self.registry.current(self.call, generation)):
                            self.registry.emit(self.call, "speech_text", text=caption_pending)
                            caption_pending = None

        try:
            publication = await self.room.local_participant.publish_track(track,
                rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
            self.publication = publication
            async for text in segments:
                if not self.registry.current(self.call, generation):
                    return
                await self.runtime.check(self.call, generation)
                decoder = MP3Decoder()
                caption_pending = text if caption else None
                async for encoded in self.tts.stream(text):
                    if not self.registry.current(self.call, generation):
                        return
                    await self.runtime.check(self.call, generation)
                    await decoded(decoder.feed(encoded))
                await decoded(decoder.finish())
                if pcm and self.registry.current(self.call, generation):
                    await source.capture_frame(rtc.AudioFrame(bytes(pcm), 24000, 1, len(pcm) // 2))
                    pcm.clear()
                    if first_audio and self.call.turn_started_at is not None:
                        elapsed_ms = round((time.monotonic() - self.call.turn_started_at) * 1000)
                        self.registry.emit(self.call, "timing", metric="first_audio_sent",
                                           elapsed_ms=elapsed_ms)
                        first_audio = False
                    if caption_pending is not None:
                        self.registry.emit(self.call, "speech_text", text=caption_pending)
                        caption_pending = None
            await source.wait_for_playout()
            if self.registry.current(self.call, generation):
                self.registry.emit(self.call, "playout_drained", estimated=True)
        except asyncio.CancelledError:
            raise
        except SpeechError:
            raise
        except Exception:
            raise SpeechError("tts_decode_failed") from None
        finally:
            source.clear_queue()
            pcm.clear()
            try:
                if publication and self.publication is publication:
                    self.publication = None
                    await self.room.local_participant.unpublish_track(publication.sid)
            finally:
                if self.source is source:
                    self.source = None
                await source.aclose()

    async def close(self):
        from livekit import api
        from livekit.api.twirp_client import TwirpError

        self.closed = True
        # A cancelled worker.run() must not cancel the SDK join. Wait for the
        # ready handshake before disconnecting or deleting its local room.
        if self.connect_task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self.connect_task), timeout=15)
            except TimeoutError:
                raise SpeechError("voice_cleanup_failed") from None
            except Exception:
                pass  # A failed join has no connected room to disconnect.
        self.flush_audio()
        tasks = [task for task in (self.input_task, self.asr_task) if task]
        for task in tasks:
            task.cancel()
        done, pending = await asyncio.wait(tasks, timeout=3) if tasks else (set(), set())
        for task in done:
            with suppress(asyncio.CancelledError, Exception):
                task.result()
        for task in list(self.revocations):
            with suppress(Exception):
                await task
            self.revocations.discard(task)
        for event, callback in self.listeners:
            self.room.off(event, callback)
        self.listeners.clear()
        try:
            await self.room.disconnect()
        finally:
            try:
                await self.runtime.api.room.delete_room(api.DeleteRoomRequest(room=self.call.room))
            except TwirpError as error:
                if error.code != "not_found":
                    raise
        if pending:
            raise SpeechError("voice_cleanup_failed")
