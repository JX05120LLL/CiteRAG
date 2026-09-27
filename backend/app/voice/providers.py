"""Bounded speech protocols. Never log audio, text, headers or provider diagnostics."""

import asyncio
import gzip
import json
import struct
import zlib
from collections.abc import AsyncIterator
from contextlib import suppress
from uuid import uuid4

import httpx


class SpeechError(Exception):
    """Only a stable, application-owned error code crosses the provider boundary."""


async def tts_lines(response):
    buffer = bytearray()
    total = 0
    async for part in response.aiter_bytes(chunk_size=4096):
        total += len(part)
        buffer.extend(part)
        if total > 12_000_000 or len(buffer) > 2_000_000:
            raise SpeechError("tts_audio_limit")
        while (boundary := buffer.find(b"\n")) >= 0:
            line = bytes(buffer[:boundary]).decode("utf-8").rstrip("\r")
            del buffer[:boundary + 1]
            yield line
    if buffer:
        yield bytes(buffer).decode("utf-8")


def asr_packet(payload: bytes, *, audio: bool = False, final: bool = False) -> bytes:
    body = gzip.compress(payload)
    header = bytes([0x11, (0x20 if audio else 0x10) | (2 if final else 0),
                    0x01 if audio else 0x11, 0])
    return header + struct.pack(">I", len(body)) + body


def asr_response(packet: bytes) -> tuple[str, bool, int]:
    if len(packet) < 8 or len(packet) > 1_000_000 or packet[0] >> 4 != 1:
        raise ValueError("Invalid ASR envelope")
    offset = (packet[0] & 15) * 4
    kind, flags = packet[1] >> 4, packet[1] & 15
    if kind == 15:
        raise SpeechError("asr_provider_failed")
    if kind != 9 or offset < 4:
        raise ValueError("Unsupported ASR envelope")
    sequence = 0
    if flags & 1:
        sequence = struct.unpack_from(">i", packet, offset)[0]
        offset += 4
    if offset + 4 > len(packet):
        raise ValueError("Incomplete ASR envelope")
    size = struct.unpack_from(">I", packet, offset)[0]
    data = packet[offset + 4:]
    if size != len(data):
        raise ValueError("Incomplete ASR payload")
    if packet[2] & 15 == 1:
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        data = decoder.decompress(data, 1_000_001)
        if len(data) > 1_000_000 or not decoder.eof or decoder.unused_data:
            raise ValueError("ASR payload exceeds limit")
    elif packet[2] & 15 != 0:
        raise ValueError("Unsupported ASR compression")
    value = json.loads(data)
    result = value.get("result", {})
    if isinstance(result, list):
        result = result[-1] if result else {}
    text = result.get("text", "")
    if not isinstance(text, str) or len(text) > 1000:
        raise SpeechError("asr_text_limit")
    return text, bool(flags & 2), sequence


class VolcASR:
    """One bidirectional stream per VAD utterance; final only after the tail packet."""

    def __init__(self, key: str, resource: str, *, app_key: str = ""):
        self.key, self.resource, self.app_key = key, resource, app_key

    async def recognize(self, audio: AsyncIterator[bytes]) -> AsyncIterator[tuple[str, bool]]:
        from websockets.asyncio.client import connect

        headers = {"X-Api-Resource-Id": self.resource, "X-Api-Request-Id": str(uuid4())}
        if self.app_key:
            headers.update({"X-Api-App-Key": self.app_key, "X-Api-Access-Key": self.key})
        else:
            headers["X-Api-Key"] = self.key
        try:
            async with connect("wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async",
                               additional_headers=headers, open_timeout=8, close_timeout=2,
                               max_size=1_000_000, max_queue=8) as socket:
                await socket.send(asr_packet(json.dumps({
                    "user": {"uid": "citerag-local"},
                    "audio": {"format": "pcm", "codec": "raw", "rate": 16000,
                              "bits": 16, "channel": 1},
                    "request": {"model_name": "bigmodel", "enable_itn": True,
                                "enable_punc": True, "show_utterances": True,
                                "enable_nonstream": True},
                }).encode()))

                async def send():
                    size = 0
                    async for chunk in audio:
                        size += len(chunk)
                        if size > 32_000 * 30:
                            raise SpeechError("asr_audio_limit")
                        await socket.send(asr_packet(chunk, audio=True))
                    await socket.send(asr_packet(b"", audio=True, final=True))

                sender = asyncio.create_task(send())
                try:
                    last_sequence = 0
                    while True:
                        packet = await asyncio.wait_for(socket.recv(), timeout=12)
                        if not isinstance(packet, bytes):
                            raise SpeechError("asr_protocol_invalid")
                        text, final, sequence = asr_response(packet)
                        # Signed tail sequence follows positive intermediate sequence.
                        if sequence and abs(sequence) < last_sequence:
                            continue
                        last_sequence = max(last_sequence, abs(sequence))
                        yield text, final
                        if final:
                            await sender
                            return
                finally:
                    sender.cancel()
                    with suppress(asyncio.CancelledError):
                        await sender
        except asyncio.CancelledError:
            raise
        except SpeechError:
            raise
        except Exception:
            raise SpeechError("asr_provider_failed") from None


class MiniMaxTTS:
    def __init__(self, key: str, voice: str, *, client: httpx.AsyncClient | None = None):
        self.key, self.voice, self.client = key, voice, client

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        if not 1 <= len(text) <= 1500:
            raise SpeechError("tts_text_limit")
        client = self.client or httpx.AsyncClient(timeout=httpx.Timeout(20, connect=8),
                                               follow_redirects=False)
        try:
            async with client.stream("POST", "https://api.minimax.cn/v1/t2a_v2", headers={
                "Authorization": "Bearer " + self.key,
            }, json={"model": "speech-02-turbo", "text": text, "stream": True,
                     "stream_options": {"exclude_aggregated_audio": True},
                     "output_format": "hex", "language_boost": "Chinese",
                     "voice_setting": {"voice_id": self.voice, "speed": 1, "vol": 1, "pitch": 0},
                     "audio_setting": {"sample_rate": 24000, "format": "mp3", "channel": 1},
                     "subtitle_enable": False}) as response:
                if response.status_code != 200:
                    raise SpeechError("tts_provider_failed")
                produced = False
                async for line in tts_lines(response):
                    if not line.startswith("data:"):
                        continue
                    value = json.loads(line[5:].strip())
                    if value.get("base_resp", {}).get("status_code", 0) != 0:
                        raise SpeechError("tts_provider_failed")
                    data = value.get("data", {})
                    # The status=2 chunk may contain the complete aggregate. Never replay it.
                    if data.get("status") == 2:
                        if not produced:
                            raise SpeechError("tts_stream_interrupted")
                        return
                    encoded = data.get("audio", "")
                    if encoded:
                        if not isinstance(encoded, str):
                            raise SpeechError("tts_protocol_invalid")
                        audio = bytes.fromhex(encoded)
                        if audio:
                            produced = True
                            yield audio
                raise SpeechError("tts_stream_interrupted")
        except asyncio.CancelledError:
            raise
        except SpeechError:
            raise
        except Exception:
            raise SpeechError("tts_provider_failed") from None
        finally:
            if self.client is None:
                await client.aclose()
