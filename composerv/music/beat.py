"""Beat detection for a music track (librosa). Returns tempo + beat timestamps so the
montage can cut on the beat. Thin wrapper; validated on a synthesized click track."""

from __future__ import annotations


def detect_beats(audio_path: str) -> tuple[float, list[float]]:
    """Return (tempo_bpm, [beat_time_s]). Empty/zeros if the track can't be read."""
    import librosa

    try:
        y, sr = librosa.load(audio_path, mono=True)
    except Exception:
        return 0.0, []
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
    tempo_val = float(tempo[0]) if hasattr(tempo, "__len__") else float(tempo)
    return tempo_val, [float(t) for t in beats]
