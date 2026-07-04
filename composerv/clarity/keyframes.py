"""Pick a few representative frames per clip, for the user to eyeball "what is this".

These are display thumbnails (verification at a glance), separate from the dense
analysis frames used for understanding. Reuses the proven CFR-aware sampler, choosing
an fps that spreads `count` frames across the WHOLE clip (never just the first seconds).
"""

from __future__ import annotations

from composerv.index.frames import sample_frames


def pick_keyframes(
    proxy_path: str,
    out_dir: str,
    duration_s: float,
    count: int = 4,
) -> list[tuple[float, str]]:
    """Extract ~`count` thumbnails spread across the clip; return (t_seconds, path) in order."""
    count = max(1, count)
    # fps = count/duration => frames land at i*duration/count, i.e. evenly across the whole clip
    fps = count / duration_s if duration_s and duration_s > 0 else 1.0
    frames = sample_frames(proxy_path, out_dir, fps=fps, max_frames=count)
    return [(f.src_pts_s, f.image_path) for f in frames]
