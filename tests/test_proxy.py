"""Tests for index.proxy: produce one uniform CFR-canvas proxy per clip.

The proxy is the live-preview contract: every proxy must share fps/resolution/codec so
AVComposition can reuse one track. Source = a camera-provided proxy (.LRF) if present
(cheap, already SDR h264), else the full-res clip (transcode 10-bit HEVC -> 8-bit h264,
tone-map if HDR). Off-aspect clips are letterbox/pillarboxed into the canvas.
"""

import pytest

from composerv.index.probe import MediaInfo
from composerv.index.proxy import build_proxy_cmd, proxy_source


def test_build_proxy_cmd_has_canvas_fit_and_cfr():
    cmd = build_proxy_cmd("in.mp4", "out.mp4", canvas=(1280, 720), fps=30)
    joined = " ".join(cmd)
    assert "scale=1280:720:force_original_aspect_ratio=decrease" in joined  # fit, don't distort
    assert "pad=1280:720" in joined  # letterbox/pillarbox the rest
    assert "-fps_mode cfr" in joined and "-r 30" in joined
    assert "yuv420p" in joined  # 8-bit
    assert "-c:a aac" in joined and "-ar 48000" in joined and "-ac 2" in joined
    assert "+faststart" in joined
    assert cmd[-1] == "out.mp4"


def test_build_proxy_cmd_tonemap_only_when_requested():
    assert "tonemap" not in " ".join(build_proxy_cmd("i", "o", tonemap=False))
    assert "tonemap" in " ".join(build_proxy_cmd("i", "o", tonemap=True))


def test_proxy_source_prefers_camera_proxy_and_skips_tonemap():
    # a sibling .LRF is SDR h264 -> use it as input, no tone-map needed
    mi = MediaInfo(path="/x/clip.MP4", kind="video", is_hdr=True, camera_proxy="/x/clip.LRF")
    assert proxy_source(mi) == ("/x/clip.LRF", False)


def test_proxy_source_falls_back_to_fullres_and_tonemaps_hdr():
    mi = MediaInfo(path="/x/clip.MP4", kind="video", is_hdr=True, camera_proxy=None)
    assert proxy_source(mi) == ("/x/clip.MP4", True)
    mi2 = MediaInfo(path="/x/clip.MP4", kind="video", is_hdr=False, camera_proxy=None)
    assert proxy_source(mi2) == ("/x/clip.MP4", False)


def test_make_proxy_normalizes_portrait_clip_to_canvas(tmp_path):
    pytest.importorskip("AVFoundation")  # probe_media used to verify; ffmpeg required
    import shutil

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")

    from composerv.devtools import make_cfr_test_clip
    from composerv.index.probe import MediaInfo, probe_media
    from composerv.index.proxy import make_proxy

    # a vertical (portrait) source must come out pillarboxed to the 16:9 canvas
    src = str(tmp_path / "portrait.mp4")
    make_cfr_test_clip(src, seconds=2.0, label="A", width=720, height=1280)
    out = str(tmp_path / "portrait.proxy.mp4")
    make_proxy(MediaInfo(path=src, kind="video"), out, canvas=(1280, 720), fps=30, use_videotoolbox=False)

    mi = probe_media(out)
    assert (mi.width, mi.height) == (1280, 720)
    assert mi.codec == "h264"
    assert mi.bit_depth == 8
    assert mi.is_vfr is False
    assert abs(mi.fps_num / mi.fps_den - 30) < 0.1
