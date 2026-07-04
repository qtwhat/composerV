"""Compute + cache offline audio features for the music library (spec §5).

compute_features uses librosa (already a dep) to read each track's energy curve, tempo,
beats, approximate key/mode, and approximate valence, then write a sibling *.features.json
sidecar. rank_tracks (music/score.py) reads these. Reused librosa.load pattern from beat.py.

CLI: `composerv music index <dir>` (composerv/cli/main.py) walks a folder and writes sidecars.
"""

from __future__ import annotations

import os
from typing import Callable

from composerv.models import TrackFeatures

_AUDIO_EXTS = {".mp3", ".wav", ".flac", ".aif", ".aiff", ".m4a", ".ogg"}


# Krumhansl-Schmugler key profiles (standard published 12-value weights).
_KS_MAJOR = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
_KS_MINOR = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)
_PC_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_MODE_CONF_FLOOR = 0.5  # below this, compute_features sets mode="unknown" (spec D3 guard)


def _estimate_key_mode(y, sr) -> tuple[str, str, float]:
    """Return (mode, root_name, confidence). mode in {"major","minor"}; caller maps low
    confidence to "unknown". chroma_cqt + KS profiles, argmax over the 12 rotations of each."""
    import librosa
    import numpy as np

    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)  # (12, n_frames)
    profile = chroma.mean(axis=1)
    profile = profile - profile.mean()
    maj = np.asarray(_KS_MAJOR) - np.mean(_KS_MAJOR)
    mino = np.asarray(_KS_MINOR) - np.mean(_KS_MINOR)

    def corr(a, b):
        d = np.linalg.norm(a) * np.linalg.norm(b)
        return float(np.dot(a, b) / d) if d else 0.0

    maj_c = [corr(profile, np.roll(maj, k)) for k in range(12)]
    min_c = [corr(profile, np.roll(mino, k)) for k in range(12)]
    bi_maj, bi_min = int(np.argmax(maj_c)), int(np.argmax(min_c))
    if maj_c[bi_maj] >= min_c[bi_min]:
        return "major", _PC_NAMES[bi_maj], round(maj_c[bi_maj], 4)
    return "minor", _PC_NAMES[bi_min], round(min_c[bi_min], 4)


def _estimate_valence(y, sr, mode: str, tempo_bpm: float) -> float:
    """0..1 brightness/positivity approximation: major + faster + brighter timbre -> higher
    (spec §9). Coarse by design (D3); calibrate weights on REAL tracks, not synthetic tones."""
    import librosa
    import numpy as np

    cent = float(librosa.feature.spectral_centroid(y=y, sr=sr)[0].mean())
    brightness = float(np.clip(cent / (sr / 2.0), 0.0, 1.0))
    tempo_n = float(np.clip((tempo_bpm - 60.0) / (160.0 - 60.0), 0.0, 1.0))
    mode_n = 1.0 if mode == "major" else (0.0 if mode == "minor" else 0.5)
    v = 0.45 * mode_n + 0.30 * tempo_n + 0.25 * brightness
    return round(float(np.clip(v, 0.0, 1.0)), 4)


def _energy_curve_raw(y, sr):
    """Per-frame loudness over time (RMS). Returns a 1-D numpy array, one value per STFT frame.
    RMS is the robust choice for the sustained-loudness arc the spec wants (§3)."""
    import librosa

    return librosa.feature.rms(y=y)[0]  # shape (n_frames,), >= 0


def _resample_curve(curve, n: int = 16) -> list[float]:
    """Resample a 1-D curve to n points on a normalized 0..1 time axis, then min-max normalize
    the values to 0..1. Captures SHAPE not absolute loudness. A flat/silent curve has no arc, so
    return [] (the scorer's empty-curve branch then scores it 0, not a deceptive mid-range hit)."""
    import numpy as np

    arr = np.asarray(curve, dtype=float)
    if arr.size == 0:
        return []
    x_old = np.linspace(0.0, 1.0, num=arr.size)
    x_new = np.linspace(0.0, 1.0, num=n)
    res = np.interp(x_new, x_old, arr)
    lo, hi = float(res.min()), float(res.max())
    if hi - lo < 1e-9:  # flat/silent track: not a real arc
        return []
    res = (res - lo) / (hi - lo)
    return [round(float(v), 4) for v in res]


# ---------------------------------------------------------------------------
# Sidecar I/O
# ---------------------------------------------------------------------------

def sidecar_path(audio_path: str) -> str:
    """Return the sidecar path for audio_path (e.g. satie.mp3 -> satie.features.json)."""
    base, _ext = os.path.splitext(audio_path)
    return base + ".features.json"


def write_sidecar(features: TrackFeatures) -> str:
    """Write features to the sidecar next to the audio. Returns the path written."""
    out = sidecar_path(features.path)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(features.model_dump_json(indent=2))
    return out


def read_sidecar(audio_path: str) -> TrackFeatures | None:
    """Load the sidecar; None if it doesn't exist or won't parse (stale/corrupt -> recompute)."""
    path = sidecar_path(audio_path)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return TrackFeatures.model_validate_json(fh.read())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Feature computation
# ---------------------------------------------------------------------------

def compute_features(audio_path: str, *, source: str = "", license: str = "") -> TrackFeatures:
    """Compute all offline features for one track (spec §5). Reuses detect_beats for tempo +
    beat_times (do NOT recompute tempo); RMS energy resampled to 16 points; KS key/mode with an
    'unknown' floor; heuristic valence."""
    import librosa

    from composerv.music.beat import detect_beats

    tempo_bpm, beat_times = detect_beats(audio_path)
    try:
        y, sr = librosa.load(audio_path, mono=True)
    except Exception:
        return TrackFeatures(path=audio_path, duration_s=0.0, tempo_bpm=tempo_bpm,
                             beat_times=beat_times, source=source, license=license)
    duration_s = float(librosa.get_duration(y=y, sr=sr))
    energy = _resample_curve(_energy_curve_raw(y, sr), n=16)
    mode, _root, conf = _estimate_key_mode(y, sr)
    if conf < _MODE_CONF_FLOOR:
        mode = "unknown"
    valence = _estimate_valence(y, sr, mode, tempo_bpm)
    return TrackFeatures(
        path=audio_path, duration_s=duration_s, tempo_bpm=tempo_bpm,
        beat_times=beat_times, mode=mode, energy_curve=energy, valence=valence,
        source=source, license=license,
    )


# ---------------------------------------------------------------------------
# Directory indexer
# ---------------------------------------------------------------------------

def index_music_dir(
    directory: str,
    *,
    output_dir: str | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> int:
    """Walk `directory` for audio files; compute + write a sidecar for any track missing one or
    whose source file is newer than its sidecar (mtime check, spec D2). Carries forward source +
    license from the existing sidecar so re-indexing never wipes provenance (spec D7). Returns the
    count (re)computed. output_dir is reserved (sidecars currently always sit next to the audio)."""
    if not os.path.isdir(directory):
        return 0
    count = 0
    for root, _dirs, files in os.walk(directory):
        for name in sorted(files):
            if os.path.splitext(name)[1].lower() not in _AUDIO_EXTS:
                continue
            audio = os.path.join(root, name)
            sc = sidecar_path(audio)
            if os.path.isfile(sc) and os.path.getmtime(sc) >= os.path.getmtime(audio):
                continue  # sidecar fresh
            old = read_sidecar(audio)  # preserve provenance across recompute
            src = old.source if old else ""
            lic = old.license if old else ""
            features = compute_features(audio, source=src, license=lic)
            write_sidecar(features)
            count += 1
            if on_progress:
                on_progress(audio)
    return count
