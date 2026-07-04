"""Tests for scanner + the analyze orchestrator (with the fake backend)."""

import shutil

import pytest

from composerv.analyze.base import get_backend
from composerv.analyze.orchestrator import analyze_clip
from composerv.index.scanner import scan_dir
from composerv.index.probe import MediaInfo
from composerv.store.db import Store


def _ffmpeg_or_skip():
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")


def test_scan_dir_finds_videos_skips_camera_proxies(tmp_path):
    _ffmpeg_or_skip()
    from composerv.devtools import make_cfr_test_clip

    make_cfr_test_clip(str(tmp_path / "A.mp4"), seconds=1.0, label="A")
    make_cfr_test_clip(str(tmp_path / "B.mp4"), seconds=1.0, label="B")
    (tmp_path / "A.LRF").write_bytes(b"fake proxy")  # camera proxy: not a primary asset
    (tmp_path / "notes.txt").write_text("ignore me")

    assets = scan_dir(str(tmp_path))
    paths = sorted(a.path for a in assets)
    assert [p.rsplit("/", 1)[-1] for p in paths] == ["A.mp4", "B.mp4"]
    # the .LRF sibling is detected as A.mp4's camera proxy
    a = next(x for x in assets if x.path.endswith("A.mp4"))
    assert a.camera_proxy is not None and a.camera_proxy.endswith("A.LRF")


def test_analyze_clip_with_fake_backend(tmp_path):
    _ffmpeg_or_skip()
    from composerv.devtools import make_cfr_test_clip

    clip = str(tmp_path / "clip.mp4")
    make_cfr_test_clip(clip, seconds=4.0, label="A")
    store = Store(str(tmp_path / "c.db"))
    store.upsert_asset(MediaInfo(path=clip, kind="video"), proxy_path=clip)

    results = analyze_clip(clip, clip, store, get_backend("fake"), fps=1.0,
                           frames_dir=str(tmp_path / "frames"))
    assert len(results) >= 4
    stored = store.get_captions(clip)
    assert len(stored) == len(results)
    assert all(c.caption for c in stored)
    assert stored[0].backend == "fake"


def test_understand_and_store_persists_summary_and_grounded_moments(tmp_path):
    _ffmpeg_or_skip()
    import json

    from composerv.analyze.orchestrator import understand_and_store
    from composerv.devtools import make_cfr_test_clip

    clip = str(tmp_path / "clip.mp4")
    make_cfr_test_clip(clip, seconds=5.0, label="A")
    store = Store(str(tmp_path / "c.db"))
    asset = MediaInfo(path=clip, kind="video", duration_s=5.0)
    store.upsert_asset(asset, proxy_path=clip)

    canned = json.dumps({"summary": "a sequence unfolds over time",
                         "moments": [{"t": 0.0, "happening": "it begins"},
                                     {"t": 3.0, "happening": "it ends"}]})
    u = understand_and_store(asset, clip, store, run=lambda p: canned, target_frames=4)

    assert u.summary == "a sequence unfolds over time"
    assert store.get_clip_summary(clip) == "a sequence unfolds over time"
    caps = store.get_captions(clip)
    assert [c.caption for c in caps] == ["it begins", "it ends"]
    assert caps[0].src_pts_s == 0.0 and caps[1].src_pts_s == 3.0
    assert caps[0].backend == "clip-video"
