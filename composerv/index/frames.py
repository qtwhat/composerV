"""Sample analysis frames from a (CFR) proxy. STUB (TDD red)."""

from __future__ import annotations

import os
import subprocess

from pydantic import BaseModel


class FrameRef(BaseModel):
    video_path: str  # the proxy the frame came from
    index: int
    src_pts_s: float  # seconds into the timeline (index/fps on a CFR proxy)
    image_path: str


def sample_frames(
    video_path: str,
    out_dir: str,
    fps: float = 1.0,
    max_frames: int | None = None,
    *,
    start_s: float = 0.0,
    duration_s: float | None = None,
) -> list[FrameRef]:
    """Extract one frame every 1/fps seconds as JPEGs into out_dir; return FrameRefs.

    start_s / duration_s restrict extraction to a sub-window using input-side -ss/-t.
    FrameRef.src_pts_s is the ABSOLUTE source time (start_s + i/fps).
    """
    os.makedirs(out_dir, exist_ok=True)
    pattern = os.path.join(out_dir, "frame_%06d.jpg")
    cmd = ["ffmpeg", "-y"]
    if start_s:
        cmd += ["-ss", f"{start_s:.6f}"]
    if duration_s is not None:
        cmd += ["-t", f"{duration_s:.6f}"]
    cmd += ["-i", video_path, "-vf", f"fps={fps}", "-q:v", "3"]
    if max_frames is not None:
        cmd += ["-frames:v", str(max_frames)]
    cmd += ["-loglevel", "error", pattern]
    subprocess.run(cmd, check=True)

    files = sorted(f for f in os.listdir(out_dir) if f.startswith("frame_") and f.endswith(".jpg"))
    return [
        FrameRef(
            video_path=video_path,
            index=i,
            src_pts_s=start_s + i / fps,  # absolute source time
            image_path=os.path.join(out_dir, name),
        )
        for i, name in enumerate(files)
    ]
