"""Generates a tiny silent WAV once at startup, used as the audio source pytgcalls joins a group call
with. A real (even silent) media stream is what keeps a participant present for more than a few
seconds - the previous implementation joined with synthetic, non-negotiated call parameters and no
real transport, which is why Telegram's call servers dropped it almost immediately."""
from __future__ import annotations

import os
import struct
import tempfile
import wave

SAMPLE_RATE = 48000
CHANNELS = 2
SECONDS = 8
_path: str | None = None


def silence_wav_path() -> str:
    global _path
    if _path and os.path.exists(_path):
        return _path
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="smm_silence_")
    os.close(fd)
    frames = SAMPLE_RATE * SECONDS
    with wave.open(path, "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(struct.pack("<h", 0) * CHANNELS * frames)
    _path = path
    return path
