"""Silero v6.2.1 ONNX inference, 16 kHz/512 samples; no network or recording."""

import hashlib
from collections import deque
from pathlib import Path

from app.voice.providers import SpeechError

MODEL_SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"


class SileroVAD:
    def __init__(self, path: Path):
        import numpy as np
        import onnxruntime as ort

        if (not path.is_file() or path.stat().st_size > 4_000_000
            or hashlib.sha256(path.read_bytes()).hexdigest() != MODEL_SHA256):
            raise SpeechError("vad_model_invalid")
        options = ort.SessionOptions()
        options.intra_op_num_threads = options.inter_op_num_threads = 1
        options.log_severity_level = 4
        self.model = ort.InferenceSession(str(path), sess_options=options,
                                         providers=["CPUExecutionProvider"])
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, 64), dtype=np.float32)

    def probability(self, pcm: bytes) -> float:
        import numpy as np

        if len(pcm) != 1024:
            raise ValueError("Silero requires exactly 512 PCM16 samples")
        frame = np.frombuffer(pcm, dtype="<i2").astype(np.float32).reshape(1, -1) / 32768
        data = np.concatenate((self.context, frame), axis=1)
        score, self.state = self.model.run(None, {
            "input": data, "state": self.state, "sr": np.array(16000, dtype=np.int64),
        })
        self.context = data[:, -64:]
        return float(score[0][0])


class Endpoint:
    """96 ms speech confirmation, 640 ms silence tail; maximum utterance 30 seconds."""

    def __init__(self):
        self.pre_roll = deque(maxlen=6)
        self.speaking = False
        self.positive = self.silence = self.frames = 0

    def feed(self, pcm: bytes, probability: float) -> str:
        self.pre_roll.append(pcm)
        if not self.speaking:
            self.positive = self.positive + 1 if probability >= 0.5 else 0
            if self.positive >= 3:
                self.speaking = True
                self.silence = self.frames = 0
                return "start"
            return "silence"
        self.frames += 1
        self.silence = self.silence + 1 if probability < 0.35 else 0
        if self.silence >= 20 or self.frames >= 930:
            self.speaking = False
            self.positive = 0
            return "end"
        return "speech"
