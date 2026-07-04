"""Export a reel to a LITE MP4 (real AVAssetExportSession round-trip on synth clips)."""

import os

import pytest

pytest.importorskip("AVFoundation")

from composerv.render.export import export_mp4  # noqa: E402


def _seconds(path):
    from AVFoundation import AVURLAsset
    from CoreMedia import CMTimeGetSeconds
    from Foundation import NSURL
    asset = AVURLAsset.URLAssetWithURL_options_(NSURL.fileURLWithPath_(path), None)
    return CMTimeGetSeconds(asset.duration()), bool(asset.tracksWithMediaType_("vide"))


def test_export_writes_a_playable_mp4_with_burned_title(synth_clips, tmp_path):
    clips = [
        {"kind": "clip", "file": synth_clips["A"], "in": 0.0, "out": 2.0},
        {"kind": "clip", "file": synth_clips["B"], "in": 0.0, "out": 1.5},
    ]
    out = str(tmp_path / "reel.lite.mp4")
    path = export_mp4(clips, 30, None, out, title="2026年1月1日 下午", tail_s=1.0)

    assert os.path.exists(path) and os.path.getsize(path) > 0
    dur, has_video = _seconds(path)
    assert has_video
    assert abs(dur - 3.5) < 0.5            # ~the summed clip duration


def test_export_raises_clearly_on_empty_reel(tmp_path):
    import pytest as _pytest
    with _pytest.raises(RuntimeError, match="no clips"):
        export_mp4([], 30, None, str(tmp_path / "x.mp4"))
    with _pytest.raises(RuntimeError, match="no clips"):
        export_mp4([{"kind": "gap", "duration": 2.0}], 30, None, str(tmp_path / "x.mp4"))


def test_export_overwrites_existing_file(synth_clips, tmp_path):
    clips = [{"kind": "clip", "file": synth_clips["A"], "in": 0.0, "out": 1.0}]
    out = str(tmp_path / "reel.lite.mp4")
    open(out, "w").write("stale")          # a stale file must not block the export
    export_mp4(clips, 30, None, out, tail_s=0.5)
    assert os.path.getsize(out) > 100      # replaced by a real mp4, not the 5-byte stub
