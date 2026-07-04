"""On-device voice activity detection via a vendored Silero VAD ONNX model.

Produces speech segments [(start_s, end_s)] in a clip's own source seconds. Used so the
montage never cuts through a sentence: a shot overlapping speech is grown to contain the
whole sentence (see render/montage and music/montage). Runs through onnxruntime (already a
project dep); NO torch. silero-vad model is MIT-licensed (snakers4/silero-vad), vendored at
models/silero_vad.onnx.

segments_from_probs is pure (the testable hysteresis); speech_probs runs the model; the
chunk size is the Silero-required 512 samples at 16 kHz (~32 ms)."""

from __future__ import annotations

import os
import subprocess
import tempfile
import wave

_MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "silero_vad.onnx")
_SR = 16000
_CHUNK = 512  # Silero's required window at 16 kHz
CHUNK_S = _CHUNK / _SR

_SESSION = None


def segments_from_probs(
    probs,
    chunk_s: float,
    threshold: float = 0.5,
    min_speech_s: float = 0.25,
    min_silence_s: float = 0.10,
) -> list[tuple[float, float]]:
    """Per-chunk speech probabilities -> merged speech segments (seconds). A run of chunks at
    or above `threshold` is a candidate; runs separated by less than `min_silence_s` merge
    (one sentence with a brief pause); candidates shorter than `min_speech_s` are dropped."""
    on = [p >= threshold for p in probs]
    raw: list[list[float]] = []
    i, n = 0, len(on)
    while i < n:
        if on[i]:
            j = i
            while j < n and on[j]:
                j += 1
            raw.append([i * chunk_s, j * chunk_s])
            i = j
        else:
            i += 1
    merged: list[list[float]] = []
    for seg in raw:
        if merged and seg[0] - merged[-1][1] < min_silence_s:
            merged[-1][1] = seg[1]
        else:
            merged.append(seg[:])
    return [(s, e) for s, e in merged if e - s >= min_speech_s]


def _session():
    global _SESSION
    if _SESSION is None:
        import onnxruntime as ort

        _SESSION = ort.InferenceSession(_MODEL_PATH, providers=["CPUExecutionProvider"])
    return _SESSION


_CONTEXT = 64  # Silero prepends the previous 64 samples to each 512-sample window (16 kHz)


def speech_probs(samples) -> list[float]:
    """One speech probability per 512-sample chunk of mono 16 kHz float32 audio in [-1, 1].

    Mirrors Silero's OnnxWrapper: each model input is the previous 64-sample context
    concatenated with the 512-sample chunk (576 total); the last 64 carry to the next call.
    Feeding bare 512-sample chunks (no context) makes the model output ~0 for all audio."""
    import numpy as np

    sess = _session()
    state = np.zeros((2, 1, 128), dtype=np.float32)
    context = np.zeros((1, _CONTEXT), dtype=np.float32)
    sr = np.array(_SR, dtype=np.int64)
    probs: list[float] = []
    for i in range(0, len(samples) - _CHUNK + 1, _CHUNK):
        chunk = samples[i:i + _CHUNK].astype(np.float32).reshape(1, -1)
        x = np.concatenate([context, chunk], axis=1)  # (1, 64+512)
        out = sess.run(None, {"input": x, "state": state, "sr": sr})
        probs.append(float(out[0].ravel()[0]))
        state = out[1]
        context = x[:, -_CONTEXT:]
    return probs


def _extract_16k_mono(path: str):
    """ffmpeg -> mono 16 kHz float32 samples in [-1, 1]; None if it can't be read."""
    import numpy as np

    wav = tempfile.mktemp(suffix=".wav")
    try:
        subprocess.run(["ffmpeg", "-y", "-i", path, "-vn", "-ac", "1", "-ar", str(_SR), wav],
                       capture_output=True, text=True, timeout=120)
        if not os.path.exists(wav):
            return None
        with wave.open(wav) as wf:
            raw = wf.readframes(wf.getnframes())
        return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    except (OSError, subprocess.TimeoutExpired, wave.Error):
        return None
    finally:
        if os.path.exists(wav):
            os.remove(wav)


def detect_speech(
    path: str,
    threshold: float = 0.5,
    min_speech_s: float = 0.25,
    min_silence_s: float = 0.10,
) -> list[tuple[float, float]]:
    """Speech segments [(start_s, end_s)] in the clip's source seconds; [] if no audio /
    no speech / the model can't run."""
    samples = _extract_16k_mono(path)
    if samples is None or len(samples) < _CHUNK:
        return []
    try:
        probs = speech_probs(samples)
    except Exception:
        return []
    return segments_from_probs(probs, CHUNK_S, threshold, min_speech_s, min_silence_s)
