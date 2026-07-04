"""Tests for the clarity ingest pipeline (proxy + summary + keyframes -> store)."""

import os
import shutil

import pytest

from composerv.clarity.ingest import ingest_clip, ingest_dir
from composerv.clarity.summarize import ClaritySummary
from composerv.index.probe import probe_media
from composerv.store.db import Store


def _ffmpeg_or_skip():
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")


def test_ingest_photo_registers_asset_with_downscaled_proxy(tmp_path):
    from PIL import Image

    from composerv.clarity.ingest import ingest_photo
    from composerv.index.probe import MediaInfo

    src = str(tmp_path / "DJI_20260101120000_0025_D.JPG")
    Image.new("RGB", (4000, 3000), (90, 110, 130)).save(src)
    store = Store(str(tmp_path / "c.db"))
    media = MediaInfo(path=src, kind="photo", capture_time="2026-01-01T12:00:00")

    proxy = ingest_photo(media, store, proxy_path=str(tmp_path / "px" / "p.jpg"))
    assert os.path.exists(proxy)
    a = store.get_asset(src)
    assert a.kind == "photo" and a.proxy_path == proxy
    assert a.width == 4000 and a.height == 3000               # ORIGINAL dims recorded (reframe needs them)
    assert a.capture_time.startswith("2026-01-01")
    w, h = Image.open(proxy).size
    assert max(w, h) <= 1280                                  # proxy is downscaled


def test_ingest_photos_walks_photos_in_a_mixed_dir(tmp_path):
    _ffmpeg_or_skip()
    from PIL import Image

    from composerv.clarity.ingest import ingest_photos
    from composerv.devtools import make_cfr_test_clip

    Image.new("RGB", (4000, 3000), (90, 110, 130)).save(tmp_path / "DJI_20260101120000_0025_D.JPG")
    make_cfr_test_clip(str(tmp_path / "DJI_20260101120001_0026_D.MP4"), seconds=3.0, label="V")
    store = Store(str(tmp_path / "c.db"))
    n = ingest_photos(str(tmp_path), store, work_dir=str(tmp_path / "work"), log=lambda *a: None)
    assert n == 1                                  # the photo is walked + registered (not zero)
    a = store.get_asset(str(tmp_path / "DJI_20260101120000_0025_D.JPG"))
    assert a is not None and a.kind == "photo" and a.proxy_path


def test_ingest_clip_stores_summary_proxy_and_keyframes(tmp_path):
    _ffmpeg_or_skip()
    from composerv.devtools import make_cfr_test_clip

    src = str(tmp_path / "DJI_X.mp4")
    make_cfr_test_clip(src, seconds=6.0, label="X")
    media = probe_media(src)
    store = Store(str(tmp_path / "c.db"))

    def fake_summarize(proxy, dur):
        return ClaritySummary(text="a test clip", source="local")

    cs = ingest_clip(media, store, proxy_path=str(tmp_path / "px.mp4"),
                     frames_dir=str(tmp_path / "kf"), summarize=fake_summarize,
                     keyframe_count=3, video_toolbox=False)

    assert cs.text == "a test clip"
    rec = store.get_clarity(src)
    assert rec.summary == "a test clip" and rec.source == "local"
    asset = store.get_asset(src)
    assert asset.proxy_path and os.path.exists(asset.proxy_path)
    kfs = store.get_keyframes(src)
    assert len(kfs) == 3 and all(os.path.exists(p) for _t, p in kfs)


def test_ingest_dir_processes_all_then_skips_existing(tmp_path):
    _ffmpeg_or_skip()
    from composerv.devtools import make_cfr_test_clip

    d = tmp_path / "clips"
    d.mkdir()
    make_cfr_test_clip(str(d / "DJI_20251130070000_0001_D.mp4"), seconds=3.0, label="A")
    make_cfr_test_clip(str(d / "DJI_20251130080000_0002_D.mp4"), seconds=3.0, label="B")
    store = Store(str(tmp_path / "c.db"))

    def fake_summarize(proxy, dur):
        return ClaritySummary(text="x", source="local")

    n = ingest_dir(str(d), store, work_dir=str(tmp_path / "work"),
                   summarize=fake_summarize, video_toolbox=False, log=lambda *a: None)
    assert n == 2

    # second pass: both already have a summary -> skipped
    n2 = ingest_dir(str(d), store, work_dir=str(tmp_path / "work"),
                    summarize=fake_summarize, video_toolbox=False, log=lambda *a: None)
    assert n2 == 0
