"""Incremental MP3 decoding with bounded, optional ID3 metadata removal."""

from app.voice.providers import SpeechError


class MP3Decoder:
    def __init__(self):
        import av

        self.decoder = av.CodecContext.create("mp3", "r")
        self.header = bytearray()
        self.started = False

    def feed(self, encoded: bytes):
        if not self.started:
            self.header.extend(encoded)
            if len(self.header) < 10:
                return []
            if self.header[:3] == b"ID3":
                size_bytes = self.header[6:10]
                if any(value & 128 for value in size_bytes):
                    raise SpeechError("tts_decode_failed")
                size = 10 + sum(value << (7 * (3 - index))
                                for index, value in enumerate(size_bytes))
                if self.header[5] & 16:
                    size += 10
                if size > 131072:
                    raise SpeechError("tts_audio_limit")
                if len(self.header) < size:
                    return []
                encoded = bytes(self.header[size:])
            else:
                encoded = bytes(self.header)
            self.header.clear()
            self.started = True
        frames = []
        for packet in self.decoder.parse(encoded):
            frames.extend(self.decoder.decode(packet))
        return frames

    def finish(self):
        if not self.started:
            raise SpeechError("tts_decode_failed")
        frames = []
        for packet in self.decoder.parse(b""):
            frames.extend(self.decoder.decode(packet))
        frames.extend(self.decoder.decode(None))
        return frames
