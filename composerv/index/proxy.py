"""Produce one uniform CFR-canvas proxy per clip. STUB (TDD red).

Uniform proxies (same fps/resolution/codec/pixfmt) are the precondition for the live
AVComposition preview (single reused track, no cross-frame-rate seek freeze). Source is a
camera-provided proxy (.LRF/.LRV) when present, else the full-res clip.
"""

from __future__ import annotations

import subprocess

from composerv.index.probe import MediaInfo


def proxy_source(media: MediaInfo) -> tuple[str, bool]:
    """Return (input_path, needs_tonemap). Prefer a camera proxy (.LRF/.LRV): it is
    already SDR h264, so no tone-map. Otherwise use the full-res clip and tone-map iff HDR.
    """
    if media.camera_proxy:
        return media.camera_proxy, False
    return media.path, media.is_hdr


def build_proxy_cmd(
    src: str,
    out: str,
    canvas: tuple[int, int] = (1280, 720),
    fps: int = 30,
    tonemap: bool = False,
    use_videotoolbox: bool = False,
) -> list[str]:
    w, h = canvas
    vf: list[str] = []
    if tonemap:
        # best-effort HDR (PQ/HLG) -> SDR bt709; requires an ffmpeg built with zscale/zimg
        vf.append("zscale=t=linear:npl=100,tonemap=hable,zscale=t=bt709:m=bt709:r=tv")
    # fit into the canvas without distortion, then letterbox/pillarbox the remainder
    vf.append(f"scale={w}:{h}:force_original_aspect_ratio=decrease")
    vf.append(f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black")
    vf.append("format=yuv420p")

    if use_videotoolbox:
        vcodec = ["-c:v", "h264_videotoolbox", "-b:v", "6M"]
    else:
        vcodec = ["-c:v", "libx264", "-crf", "20", "-preset", "veryfast"]

    return [
        "ffmpeg", "-y", "-i", src,
        "-vf", ",".join(vf),
        "-r", str(fps), "-fps_mode", "cfr",
        *vcodec, "-g", str(max(1, fps // 2)), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", "-loglevel", "error",
        out,
    ]


def make_proxy(
    media: MediaInfo,
    out: str,
    canvas: tuple[int, int] = (1280, 720),
    fps: int = 30,
    use_videotoolbox: bool = False,
) -> str:
    src, tonemap = proxy_source(media)
    subprocess.run(build_proxy_cmd(src, out, canvas, fps, tonemap, use_videotoolbox), check=True)
    return out
