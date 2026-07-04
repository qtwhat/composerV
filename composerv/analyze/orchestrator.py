"""Drive analysis over a clip: sample frames -> backend -> store. STUB (TDD red)."""

from __future__ import annotations

import tempfile
from collections.abc import Callable

from composerv.analyze.base import AnalyzerBackend, CaptionResult
from composerv.index.frames import FrameRef, sample_frames
from composerv.index.probe import MediaInfo
from composerv.store.db import Store


def analyze_clip(
    asset_path: str,
    proxy_path: str,
    store: Store,
    backend: AnalyzerBackend,
    fps: float = 1.0,
    frames_dir: str | None = None,
    max_frames: int | None = None,
) -> list[CaptionResult]:
    """Sample frames from the proxy, caption them with the backend, persist to the store.

    Returns the per-frame CaptionResults (same order as sampled frames).
    """
    frames_dir = frames_dir or tempfile.mkdtemp(prefix="cv_frames_")
    frames = sample_frames(proxy_path, frames_dir, fps=fps, max_frames=max_frames)
    results = backend.caption_frames([f.image_path for f in frames])
    store.replace_captions(asset_path, backend.name, list(zip(frames, results)))
    return results


def understand_and_store(
    asset: MediaInfo,
    proxy_path: str,
    store: Store,
    run: Callable[[str], str] | None = None,
    target_frames: int = 16,
    understander: Callable[[str, float], object] | None = None,
):
    """Clip-level video understanding (full-coverage frame sequence) -> store the summary +
    grounded moments. Replaces the sparse per-frame captioning with whole-clip understanding.

    By default uses the Claude frame-sequence understander; pass `understander(proxy, dur)`
    to use another (e.g. the local mlx-vlm Qwen2.5-VL backend) for a fully on-device run.
    Returns the ClipUnderstanding."""
    if understander is not None:
        u = understander(proxy_path, asset.duration_s)
    else:
        from composerv.analyze.clip_video import understand_clip

        u = understand_clip(proxy_path, asset.duration_s, run=run, target_frames=target_frames)
    store.set_clip_summary(asset.path, u.summary)
    store.replace_captions(
        asset.path,
        "clip-video",
        [
            (FrameRef(video_path=proxy_path, index=i, src_pts_s=m.t, image_path=""),
             CaptionResult(caption=m.text))
            for i, m in enumerate(u.moments)
        ],
    )
    return u
