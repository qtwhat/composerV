"""A small local music library tagged by feeling (subfolders: <music_dir>/<feeling>/*.mp3).

suggest_track picks a track for a feeling, falling back to any available track so a montage
can always get music. All local; the user owns the files.
"""

from __future__ import annotations

import os

_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"}


def load_library(music_dir: str) -> dict[str, list[str]]:
    """Scan <music_dir>/<feeling>/ for audio files. Returns {feeling: [sorted paths]}."""
    lib: dict[str, list[str]] = {}
    if not os.path.isdir(music_dir):
        return lib
    for feeling in sorted(os.listdir(music_dir)):
        sub = os.path.join(music_dir, feeling)
        if not os.path.isdir(sub):
            continue
        tracks = sorted(
            os.path.join(sub, f) for f in os.listdir(sub)
            if os.path.splitext(f)[1].lower() in _AUDIO_EXTS
        )
        if tracks:
            lib[feeling] = tracks
    return lib


def suggest_track(feeling: str, library: dict[str, list[str]]) -> str | None:
    """A track tagged for `feeling`; else any track (so the montage always has music)."""
    if library.get(feeling):
        return library[feeling][0]
    for tracks in library.values():
        if tracks:
            return tracks[0]
    return None


def load_features_lib(library: dict[str, list[str]]) -> list:
    """Read the *.features.json sidecar for every track in a loaded library. Tracks with no
    sidecar are skipped (run `composerv music index` first). Returns a flat list[TrackFeatures]
    so rank_tracks can score across the whole library, not just one feeling folder."""
    from composerv.music.features import read_sidecar

    out = []
    for tracks in library.values():
        for path in tracks:
            tf = read_sidecar(path)
            if tf is not None:
                out.append(tf)
    return out
