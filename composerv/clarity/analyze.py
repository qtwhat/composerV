"""Ingest perception into the store: run the local VLM + Whisper once per clip, cache the table.

The slow, noisy half (local VLM for per-moment visual understanding + Whisper for the transcript)
runs HERE, once, at ingest time — so a later director run reads the cached index from the store
and edits fast and quietly, with no live model calls. Model calls are injectable for tests.
Run the live path under `taskpolicy -b` (background QoS) to keep the fans down.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable


def _default_visual(path: str, dur: float) -> list[tuple]:
    """Per-moment visual understanding from the local VLM, with grounded boxes + on-screen text
    (the grounding pass is capped per clip). Returns (t, text, ocr, objects); empty on failure."""
    if not path or not os.path.exists(path):
        return []
    try:
        from composerv.clarity.understand import understand_clip_perframe

        # per-frame (one single-image call per frame) + terse-Chinese people-first prompt: avoids
        # the multi-image repetition collapse and keeps expression/gaze/action. ~1 frame / 4s,
        # clamped, concentrated by scene-cut so the budget lands on human moments not B-roll.
        mf = max(6, min(20, round(dur / 4))) if dur else 6
        u = understand_clip_perframe(path, dur, frames_mode="scene", max_frames=mf,
                                     max_long_side=512, ground=True, ocr=True, max_ground_frames=4)
        return [(m.t, m.text, m.ocr, m.objects) for m in u.moments]
    except Exception as e:
        # do NOT fail silently — a clip that returns [] should be distinguishable from a real
        # failure (matches claude_cli's stderr-on-failure convention)
        print(f"[analyze] visual perception failed for {path}: {e!r}", file=sys.stderr)
        return []


def _default_photo_visual(path: str, dur: float = 0.0) -> list[tuple]:
    """A single photo's perception (caption + grounded boxes + on-screen text); empty on failure."""
    if not path or not os.path.exists(path):
        return []
    try:
        from composerv.clarity.understand import understand_photo

        m = understand_photo(path)
        return [(m.t, m.text, m.ocr, m.objects)]
    except Exception as e:
        print(f"[analyze] photo perception failed for {path}: {e!r}", file=sys.stderr)
        return []


def analyze_clip(
    store,
    path: str,
    *,
    visual_fn: Callable[[str, float], list] | None = None,
    speech_fn: Callable[[str], list] | None = None,
    aesthetics_fn: Callable | None = None,
    aes_fps: float = 2.0,
    enable_aesthetics: bool = True,
) -> tuple[int, int]:
    """Run perception for one clip and cache it in the store (clip_moments + transcript +
    clip_aesthetics). Returns (n_moments, n_sentences). A photo gets the single-image visual pass
    and NO transcript/aesthetics. visual_fn/speech_fn/aesthetics_fn override the live models."""
    a = store.get_asset(path)
    if not a:
        return (0, 0)
    proxy = a.proxy_path or a.path or ""
    if a.kind == "photo":
        vis = (visual_fn or _default_photo_visual)(proxy, a.duration_s) or []
        store.set_clip_moments(path, vis)
        return (len(vis), 0)  # a still has no audio

    vis = (visual_fn or _default_visual)(proxy, a.duration_s) or []
    store.set_clip_moments(path, vis)  # items may be (t,text) or (t,text,ocr[,objects])

    if enable_aesthetics:
        from composerv.analyze.aesthetics import analyze_aesthetics
        best_t, curve = (aesthetics_fn or analyze_aesthetics)(proxy, a.duration_s, aes_fps=aes_fps)
        if curve:
            store.set_clip_aesthetics(path, best_t, curve)

    from composerv.music.montage import _default_speech

    sp = (speech_fn or _default_speech)(proxy) or []
    store.set_transcript(path, [(float(w[0]), float(w[1]), w[2] if len(w) > 2 else "") for w in sp])
    return (len(vis), len(sp))


def analyze_scope(
    store,
    paths: list[str],
    *,
    visual_fn: Callable[[str, float], list] | None = None,
    speech_fn: Callable[[str], list] | None = None,
    aesthetics_fn: Callable | None = None,
    aes_fps: float = 2.0,
    enable_aesthetics: bool = True,
    cooldown_s: float = 0.0,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> list[tuple[str, int, int]]:
    """Analyze every clip in the scope, caching each into the store. Returns [(path, n_moments,
    n_sentences)]. cooldown_s sleeps between clips so the GPU idles and the fans stay calm."""
    import time

    out = []
    for i, p in enumerate(paths):
        if i and cooldown_s > 0:
            time.sleep(cooldown_s)
        nv, ns = analyze_clip(store, p, visual_fn=visual_fn, speech_fn=speech_fn,
                              aesthetics_fn=aesthetics_fn, aes_fps=aes_fps,
                              enable_aesthetics=enable_aesthetics)
        out.append((p, nv, ns))
        if on_progress:
            on_progress(p, nv, ns)
    return out
